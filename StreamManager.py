#!/usr/bin/env python3
#
# DPRStreamManager
# Copyright 2021-2011 by Didier Malenfant.
#
# A little swiss army knife script used to listen to tracks being played
# in Traktor, control OBS via midi and post track lists to Twitter.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import os
import mido
import configparser
import getopt
import sys
import tweepy
# import imghdr

import obswebsocket
import obswebsocket.events
import obswebsocket.requests
import xml.etree.ElementTree as xml_tree

from pathlib import Path
from traktor_nowplaying import Listener as TraktorListener
from threading import Thread
from time import sleep
from mutagen import File as MutagenFile


# -- Global variables
midi_client = None
obs_client = None
twitter_client = None
traktor_client = None

playing_track_title_filename = None
playing_track_title_prefix = None
playing_track_artist_filename = None
playing_track_artist_prefix = None
playing_track_artwork_filename = None
no_artwork_placeholder_filename = None


# -- Classes
class TwitterClient:
    """Manage all our Twitter interactions."""

    def __init__(self, config):
        """Initialize the client based on user configuration."""

        print('Setting up Twitter...')

        self.bearer_token = config['BearerToken']
        self.consumer_key = config['ConsumerKey']
        self.consumer_secret = config['ConsumerSecret']
        self.access_token = config['AccessToken']
        self.access_token_secret = config['AccessTokenSecret']
        self.stream_start_text = config['StreamStartText']
        self.stream_stop_text = config['StreamStopText']
        self.track_update_text = config['TrackUpdateText']

        self.twitter_api = tweepy.Client(bearer_token=self.bearer_token, consumer_key=self.consumer_key,
                                         consumer_secret=self.consumer_secret, access_token=self.access_token,
                                         access_token_secret=self.access_token_secret)

    def tweet(self, text, media_id=None):
        """Tweet some text."""
        if self.twitter_api is None:
            return

        # -- Update the status
        print(f'Tweet!: {text}')
        if media_id is None:
            self.twitter_api.create_tweet(text=text)
        else:
            self.twitter_api.create_tweet(text=text, media_ids=[media_id])

    def tweet_start_text(self):
        """Tweet the stream start text."""
        self.tweet(self.twitter_stream_start_text)

    def tweet_stop_text(self):
        """Tweet the stream strop text."""
        self.tweet(self.twitter_stream_stop_text)

    def update_status(self, title, artist):
        """Tweet the currently playing track."""
        # global playing_track_artwork_filename

        if len(title) == 0 or len(artist) == 0:
            return

        update_message = self.track_update_text.replace('{title}', title)
        update_message = update_message.replace('{artist}', artist)

        # media_id = None

        # if os.path.exists(playing_track_artwork_filename):
        #    base = os.path.splitext(playing_track_artwork_filename)[0]
        #    extension = imghdr.what(playing_track_artwork_filename)

        #    new_playing_track_artwork_filename = base + '.' + extension

        #    with open(playing_track_artwork_filename, 'rb') as file:
        #        media_id = API.simple_upload(new_playing_track_artwork_filename,
        # file)

        self.tweet(update_message)


class MidiClient:
    """Manage all our Midi interactions."""

    def __init__(self, config):
        print('Setting up midi...')

        self.midi_input = None
        self.midi_output = None

        input_device_name = config['InputDeviceName']
        if input_device_name is not None:
            for device_name in mido.get_input_names():
                if device_name == input_device_name:
                    self.midi_input = mido.open_input(device_name, callback=self.on_midi_msg)
                    print(f'Input: {device_name}')
                    break

        if self.midi_input is None:
            print(f'Can\'t open midi input device {input_device_name}')

        output_device_name = config['OutputDeviceName']
        if output_device_name is not None:
            for device_name in mido.get_output_names():
                if device_name == output_device_name:
                    self.midi_output = mido.open_output(device_name)
                    print(f'Output: {device_name}')
                    break

        if self.midi_output is None:
            print(f'Can\'t open midi output device {output_device_name}')

        self.notes_currently_on = []
        self.note_on_callbacks = {}

    def add_callback(self, channel, note, callback):
        existing_callbacks_for_channel = self.note_on_callbacks.get(channel, {})

        existing_callbacks_for_channel[note] = callback
        self.note_on_callbacks[channel] = existing_callbacks_for_channel

    def on_midi_msg(self, message):
        if(message.type != 'note_on'):
            return

        existing_callbacks_for_channel = self.note_on_callbacks.get(message.channel, None)
        if existing_callbacks_for_channel is None:
            return

        callback = existing_callbacks_for_channel.get(message.note, None)
        if callback is None:
            return

        callback(message.channel, message.note)

    def note_on(self, note, channel, velocity):
        if self.midi_output is None:
            return

        self.midi_output.send(mido.Message('note_on', channel=channel, note=note, velocity=velocity))

        note_channel_combo = [note, channel]
        if note_channel_combo not in self.notes_currently_on:
            self.notes_currently_on.append(note_channel_combo)

    def note_off(self, note, channel):
        if self.midi_output is None:
            return

        self.midi_output.send(mido.Message('note_off', channel=channel, note=note))

        note_channel_combo = [note, channel]
        if note_channel_combo in self.notes_currently_on:
            self.notes_currently_on.remove(note_channel_combo)

    def shutdown(self):
        print('Shutting down midi...')

        if self.midi_input is not None:
            self.midi_input.close()

        if self.midi_output is not None:
            for note_channel_combo in self.notes_currently_on:
                self.note_off(note_channel_combo[0], note_channel_combo[1])

                # -- Give some time for the note off to be sent thru
                sleep(5)

            self.midi_output.close()


class OBSClient:
    """Manage all our OBS interactions."""

    def __init__(self, config):
        """Initialize the client based on user configuration."""
        global midi_client

        print('Setting up OBS...')

        self.server_address = config['ObsServerAddress']
        self.server_port = int(config['ObsServerPort'])
        self.server_password = config['ObsServerPassword']
        self.current_scene_channel = int(config['CurrentSceneChannel']) - 1
        self.current_scene_velocity = int(config['CurrentSceneVelocity'])

        self.scene_selection_notes = []
        for note_as_string in config['SceneSelectionNotes'].split(','):
            note = int(note_as_string)

            self.scene_selection_notes.append(note)

            midi_client.add_callback(self.current_scene_channel, note, self.set_current_scene)

        self.stream_status_note = int(config['StreamStatusNote'])
        self.stream_status_channel = int(config['StreamStatusChannel']) - 1
        self.stream_status_on_velocity = int(config['StreamOnVelocity'])
        self.stream_status_off_velocity = int(config['StreamOffVelocity'])

        midi_client.add_callback(self.stream_status_channel,
                                 self.stream_status_note,
                                 self.toggle_stream_status)

        self.obs = obswebsocket.obsws(self.server_address, self.server_port, self.server_password)
        self.obs.register(self.on_transition, obswebsocket.events.TransitionBegin)
        self.obs.register(self.on_scene_changed, obswebsocket.events.TransitionEnd)
        self.obs.register(self.on_stream_started, obswebsocket.events.StreamStarted)
        self.obs.register(self.on_stream_stopped, obswebsocket.events.StreamStopped)

        try:
            self.obs.connect()
        except obswebsocket.exceptions.ConnectionFailure:
            self.obs = None
            return

        # -- Get all the scenes
        all_scenes = self.obs.call(obswebsocket.requests.GetSceneList())

        self.scenes = []
        for scene in all_scenes.getScenes():
            self.scenes.append(scene['name'])

        # -- Update the current scene
        self.update_current_scene_index()
        self.set_current_scene_note(True)

        # -- Update the initial stream status
        status = self.obs.call(obswebsocket.requests.GetStreamingStatus())
        self.stream_on = status.getStreaming()

        self.in_transition = False

        self.update_stream_status_note()

    def update_current_scene_index(self):
        current_scene = self.obs.call(obswebsocket.requests.GetCurrentScene())
        self.current_scene_index = self.scenes.index(current_scene.getName())

    def set_current_scene_note(self, on_or_off):
        global midi_client

        if on_or_off:
            midi_client.note_on(channel=self.current_scene_channel,
                                note=self.scene_selection_notes[self.current_scene_index],
                                velocity=self.current_scene_velocity)
        else:
            midi_client.note_off(channel=self.current_scene_channel,
                                 note=self.scene_selection_notes[self.current_scene_index])

    def update_stream_status_note(self):
        global midi_client

        if self.stream_on:
            midi_client.note_on(channel=self.stream_status_channel, note=self.stream_status_note,
                                velocity=self.stream_status_on_velocity)
        else:
            midi_client.note_on(channel=self.stream_status_channel, note=self.stream_status_note,
                                velocity=self.stream_status_off_velocity)

    def start_streaming(self, channel, note):
        print('Set Stream ON')
        self.obs.call(obswebsocket.requests.StartStreaming())

    def stop_streaming(self, channel, note):
        print('Set Stream OFF')
        self.obs.call(obswebsocket.requests.StopStreaming())

    def toggle_stream_status(self, channel, note):
        if self.stream_on:
            self.stop_streaming()
        else:
            self.start_streaming()

    def set_current_scene(self, channel, note):
        if self.in_transition:
            return

        scene_index = self.scene_selection_notes.index(note)
        if scene_index >= len(self.scenes):
            return

        self.obs.call(obswebsocket.requests.SetCurrentScene(self.scenes[scene_index]))

    def on_transition(self, message):
        self.in_transition = True

    def do_obs_scene_changed(self):
        self.set_current_scene_note(False)
        self.update_current_scene_index()
        self.set_current_scene_note(True)

        self.in_transition = False

    def on_scene_changed(self, message):
        Thread(target=self.do_obs_scene_changed).start()

    def on_stream_started(self, message):
        global twitter_client

        self.stream_on = True

        self.update_stream_status_note()
        twitter_client.tweet_start_text()

        return

    def on_stream_stopped(self, message):
        global twitter_client

        self.stream_on = False

        self.update_stream_status_note()
        twitter_client.tweet_stop_text()

        return

    def shutdown(self):
        print('Shutting down obs...')

        if self.obs is None:
            return

        self.obs.disconnect()


class TraktorClient:
    """Manage all our Traktor interactions."""

    def __init__(self, config):
        """Initialize the client based on user configuration."""
        global midi_client

        print('Setting up Traktor...')

        self.collection_path = Path(config['CollectionFilename'])
        self.new_track_available_channel = int(config['NewTrackAvailableChannel']) - 1
        self.new_track_available_note = int(config['NewTrackAvailableNote'])
        self.new_track_available_velocity = int(config['NewTrackAvailableVelocity'])
        self.clear_current_track_channel = int(config['ClearCurrentTrackChannel']) - 1
        self.clear_current_track_note = int(config['ClearCurrentTrackNote'])
        self.skip_next_track_channel = int(config['SkipNextTrackChannel']) - 1
        self.skip_next_track_note = int(config['SkipNextTrackNote'])
        self.skip_next_track_velocity = int(config['SkipNextTrackVelocity'])

        self.next_track_title_string = None
        self.next_track_artist_string = None
        self.next_track_filename = None
        self.current_track_title_string = ''
        self.current_track_artist_string = ''
        self.current_track_filename = None
        self.light_on = False
        self.track_file_collection = {}

        midi_client.add_callback(self.new_track_available_channel,
                                 self.new_track_available_note,
                                 self.new_track_available)
        midi_client.add_callback(self.clear_current_track_channel,
                                 self.clear_current_track_note,
                                 self.clear_current_track)
        midi_client.add_callback(self.skip_next_track_channel,
                                 self.skip_next_track_note,
                                 self.skip_next_track)

    def update_meta(self, data):
        info = dict(data)
        title = info.get("title", "")
        artist = info.get("artist", "")

        if len(title) == 0 or len(artist) == 0:
            return

        track_string = f'{title}{artist}'
        self.next_track_title_string = title
        self.next_track_artist_string = artist

        print(f'Available: {title} {artist}')

        self.next_track_filename = self.track_file_collection.get(track_string, None)

    def parse_collection(self):
        print('Parsing Traktor collection...')

        xml_root = xml_tree.ElementTree(file=self.collection_path).getroot()

        for collection in xml_root.findall('COLLECTION'):
            for entry in collection.findall('ENTRY'):
                location = entry.find('LOCATION')

                if location is None:
                    continue

                volume = location.get('VOLUME')

                if volume is None:
                    continue

                directory = location.get('DIR')

                if directory is None:
                    continue

                file = location.get('FILE')

                if file is None:
                    continue

                filename = '/Volumes/' + volume + \
                    directory.replace('/:', '/') + file

                title = entry.get('TITLE')

                if title is None:
                    continue

                artist = entry.get('ARTIST')

                if artist is None:
                    continue

                key = f'{title}{artist}'

                if key not in self.track_file_collection:
                    self.track_file_collection[key] = filename

    def start(self):
        self.parse_collection()

        set_interval(1, self.check_for_new_tracks)

        print('Listening to Traktor...')
        listener = TraktorListener(port=8000, quiet=True, custom_callback=self.update_meta)

        listener.start()

    def check_for_new_tracks(self):
        global midi_client

        if self.next_track_title_string is not None:
            midi_client.note_on(self.skip_next_track_note, self.skip_next_track_channel, self.skip_next_track_velocity)

            if self.light_on:
                self.light_on = False
                midi_client.note_off(self.new_track_available_note, self.new_track_available_channel)
            else:
                self.light_on = True
                midi_client.note_on(self.new_track_available_note, self.new_track_available_channel,
                                    self.new_track_available_velocity)
        else:
            self.light_on = False
            midi_client.note_off(self.new_track_available_note, self.new_track_available_channel)
            midi_client.note_off(self.skip_next_track_note, self.skip_next_track_channel)

    def new_track_available(self, channel, note):
        global twitter_client

        if self.next_track_title_string is None:
            return

        self.current_track_title_string = self.next_track_title_string
        self.next_track_title_string = None

        self.current_track_artist_string = self.next_track_artist_string
        self.next_track_artist_string = None

        self.current_track_filename = self.next_track_filename
        self.next_track_filename = None

        update_track_string()
        update_track_artwork()

        twitter_client.update_status(self.current_track_title_string, self.current_track_artist_string)

    def clear_current_track(self, channel, note):
        global twitter_client

        print('Clearing Track Name')

        self.next_track_title_string = ''
        self.current_track_title_string = self.next_track_title_string

        self.next_track_artist_string = ''
        self.current_track_artist_string = self.next_track_artist_string

        self.next_track_filename = None
        self.current_track_filename = self.next_track_filename

        update_track_string()
        update_track_artwork()

        twitter_client.update_status(self.current_track_title_string, self.current_track_artist_string)

    def skip_next_track(self, channel, note):
        global twitter_client

        if self.next_track_title_string is None:
            return

        print('Skipping Next Track')

        self.next_track_title_string = None
        self.next_track_artist_string = None
        self.next_track_filename = None

        update_track_string()
        update_track_artwork()

        twitter_client.update_status(self.current_track_title_string, self.current_track_artist_string)


# -- Functions
def call_at_interval(period, callback, args):
    while True:
        sleep(period)
        callback(*args)


def set_interval(period, callback, *args):
    Thread(target=call_at_interval, args=(period, callback, args)).start()


def update_track_artwork(need_placeholder_artwork=True):
    global traktor_client
    global playing_track_artwork_filename

    artwork = None

    if traktor_client.current_track_filename is not None:
        if os.path.exists(traktor_client.current_track_filename):
            try:
                # -- Mutagen can automatically detect format and type of tags
                file = MutagenFile(traktor_client.current_track_filename)

                # -- Access APIC frame and grab the image
                tag = file.tags.get('APIC:', None)

                if tag is not None:
                    artwork = tag.data
                else:
                    cover_list = file.get('covr', None)
                    if cover_list is not None and len(cover_list):
                        artwork = cover_list[0]
            except Exception:
                artwork = None

            if artwork is not None:
                # -- Write artwork to new image
                with open(playing_track_artwork_filename, 'wb') as dest_file:
                    dest_file.write(artwork)
                    need_placeholder_artwork = False

    if need_placeholder_artwork:
        with open(no_artwork_placeholder_filename, 'rb') as src_file:
            with open(playing_track_artwork_filename, 'wb') as dest_file:
                dest_file.write(src_file.read())
    elif not artwork and os.path.exists(playing_track_artwork_filename):
        os.remove(playing_track_artwork_filename)


def update_track_string():
    global traktor_client
    global playing_track_title_filename
    global playing_track_title_prefix
    global playing_track_artist_filename
    global playing_track_artist_prefix

    title = traktor_client.current_track_title_string
    artist = traktor_client.current_track_artist_string

    if len(title) != 0 and playing_track_title_prefix is not None:
        title = playing_track_title_prefix + ' ' + title

    if len(artist) != 0 and playing_track_artist_prefix is not None:
        artist = playing_track_artist_prefix + ' ' + artist

    Path(playing_track_title_filename).write_text(f'{title}')
    Path(playing_track_artist_filename).write_text(f'{artist}')

    print(f'Output: {title} {artist}')


def on_listening_midi_msg(message):
    if(message.type != 'note_on'):
        return

    print(f'Note: {message.note} Channel: {message.channel + 1}')


def listen_to_midi(input_device_name):
    print(f'Listening from "{input_device_name}".')

    midi_input = mido.open_input(input_device_name, callback=on_listening_midi_msg)

    if midi_input is not None:
        try:
            while 1:
                sleep(1)
        except KeyboardInterrupt:
            pass

        midi_input.close()

    sys.exit(2)


def read_config(config_file_path):
    global obs_client
    global midi_client
    global twitter_client
    global traktor_client
    global playing_track_title_filename
    global playing_track_title_prefix
    global playing_track_artist_filename
    global playing_track_artist_prefix
    global playing_track_artwork_filename
    global no_artwork_placeholder_filename

    print('Reading configuration...')

    if not os.path.exists(config_file_path):
        print(f'Can\'t read ini file at \'{config_file_path}\'.')
        sys.exit(2)

    config = configparser.ConfigParser()
    config.read(config_file_path)

    general = config['general']
    playing_track_title_filename = general['OutputTitleFilename']
    playing_track_title_prefix = general['OutputTitlePrefix']
    playing_track_artist_filename = general['OutputArtistFilename']
    playing_track_artist_prefix = general['OutputArtistPrefix']
    playing_track_artwork_filename = general['OutputArtworkFilename']
    no_artwork_placeholder_filename = general['NoArtworkPlaceHolderFilename']

    midi_client = MidiClient(config['midi'])
    traktor_client = TraktorClient(config['traktor'])
    obs_client = OBSClient(config['obs'])
    twitter_client = TwitterClient(config['twitter'])


def read_args(args):
    config_file_path = None

    try:
        # -- Gather the arguments
        opts, other_arguments = getopt.getopt(args, 'dl:')

        for argument in other_arguments:
            if config_file_path is not None:
                print('Found multiple ini files on the command line.')
                sys.exit(2)

            config_file_path = argument

        if len(opts):
            # -- Iterate over the options and values
            for opt, arg_val in opts:
                if opt == '-d':
                    print('Input Devices:')
                    print(f'{mido.get_input_names()}')
                    print('Output Devices:')
                    print(f'{mido.get_output_names()}')
                    sys.exit(2)
                elif opt == '-l':
                    listen_to_midi(arg_val)

    except getopt.GetoptError:
        print('usage: StreamManager.py <-devices> <-listen> config.ini')
        sys.exit(2)

    if config_file_path is None:
        print('Couldn\'t find any ini file path on the command line.')
        sys.exit(2)

    return config_file_path


def shutdown():
    global midi_client
    global obs_client

    midi_client.shutdown()
    obs_client.shutdown()


def main():
    global traktor_client

    # -- Remove the first argument (which is the script filename)
    config_file_path = read_args(sys.argv[1:])
    read_config(config_file_path)

    update_track_string()
    update_track_artwork(False)

    traktor_client.start()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass

    shutdown()
