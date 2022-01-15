#!/usr/bin/env python3
#
# -- Listen the tracks being played in Traktor and update them on command
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
next_track_title_string = None
next_track_artist_string = None
next_track_filename = None
current_track_title_string = ''
current_track_artist_string = ''
current_track_filename = None
track_file_collection = {}

midi_input = None
midi_output = None

light_on = False

obs = None
obs_in_transition = False
obs_scenes = []
obs_stream_on = False

# -- Configuration
playing_track_title_filename = None
playing_track_title_prefix = None
playing_track_artist_filename = None
playing_track_artist_prefix = None
playing_track_artwork_filename = None
no_artwork_placeholder_filename = None

input_device_name = None
output_device_name = None

traktor_collection_path = None

new_track_available_channel = 0
new_track_available_note = 0
new_track_available_velocity = 0

clear_current_track_channel = 0
clear_current_track_note = 0

skip_next_track_channel = 0
skip_next_track_note = 0

obs_server_address = None
obs_server_port = 0
obs_server_password = None
obs_scene_selection_notes = []
obs_current_scene_channel = 0
obs_current_scene_velocity = 0
obs_current_scene_index = 0
obs_stream_status_note = 0
obs_stream_status_channel = 0
obs_stream_status_on_velocity = 0
obs_stream_status_off_velocity = 0

twitter_client = None
twitter_bearer_token = None
twitter_consumer_key = None
twitter_consumer_secret = None
twitter_access_token = None
twitter_access_token_secret = None
twitter_stream_start_text = None
twitter_stream_stop_text = None
twitter_track_update_text = None


def call_at_interval(period, callback, args):
    while True:
        sleep(period)
        callback(*args)


def set_interval(period, callback, *args):
    Thread(target=call_at_interval, args=(period, callback, args)).start()


def read_config():
    global playing_track_title_filename
    global playing_track_title_prefix
    global playing_track_artist_filename
    global playing_track_artist_prefix
    global playing_track_artwork_filename
    global no_artwork_placeholder_filename
    global input_device_name
    global output_device_name
    global traktor_collection_path
    global new_track_available_channel
    global new_track_available_note
    global new_track_available_velocity
    global clear_current_track_note
    global clear_current_track_channel
    global skip_next_track_note
    global skip_next_track_channel
    global obs_server_address
    global obs_server_port
    global obs_server_password
    global obs_scene_selection_notes
    global obs_current_scene_channel
    global obs_current_scene_velocity
    global obs_stream_status_note
    global obs_stream_status_channel
    global obs_stream_status_on_velocity
    global obs_stream_status_off_velocity
    global twitter_bearer_token
    global twitter_consumer_key
    global twitter_consumer_secret
    global twitter_access_token
    global twitter_access_token_secret
    global twitter_stream_start_text
    global twitter_stream_stop_text
    global twitter_track_update_text

    print('Reading configuration...')

    current_script_path = os.path.realpath(__file__)
    config_file_path = Path(current_script_path).with_suffix('.ini')

    config = configparser.ConfigParser()
    config.read(config_file_path)

    general = config['general']
    playing_track_title_filename = general['OutputTitleFilename']
    playing_track_title_prefix = general['OutputTitlePrefix']
    playing_track_artist_filename = general['OutputArtistFilename']
    playing_track_artist_prefix = general['OutputArtistPrefix']
    playing_track_artwork_filename = general['OutputArtworkFilename']
    no_artwork_placeholder_filename = general['NoArtworkPlaceHolderFilename']

    midi = config['midi']
    input_device_name = midi['InputDeviceName']
    output_device_name = midi['OutputDeviceName']

    traktor = config['traktor']
    traktor_collection_path = Path(traktor['CollectionFilename'])
    new_track_available_channel = int(
        config['traktor']['NewTrackAvailableChannel']) - 1
    new_track_available_note = int(traktor['NewTrackAvailableNote'])
    new_track_available_velocity = int(traktor['NewTrackAvailableVelocity'])
    clear_current_track_channel = int(traktor['ClearCurrentTrackChannel']) - 1
    clear_current_track_note = int(traktor['ClearCurrentTrackNote'])
    skip_next_track_channel = int(traktor['SkipNextTrackChannel']) - 1
    skip_next_track_note = int(traktor['SkipNextTrackNote'])

    obs = config['obs']
    obs_server_address = obs['ObsServerAddress']
    obs_server_port = int(obs['ObsServerPort'])
    obs_server_password = obs['ObsServerPassword']
    obs_current_scene_channel = int(obs['CurrentSceneChannel']) - 1
    obs_current_scene_velocity = int(obs['CurrentSceneVelocity'])

    for note in obs['SceneSelectionNotes'].split(','):
        obs_scene_selection_notes.append(int(note))

    obs_stream_status_note = int(obs['StreamStatusNote'])
    obs_stream_status_channel = int(obs['StreamStatusChannel']) - 1
    obs_stream_status_on_velocity = int(obs['StreamOnVelocity'])
    obs_stream_status_off_velocity = int(obs['StreamOffVelocity'])

    twitter = config['twitter']
    twitter_bearer_token = twitter['BearerToken']
    twitter_consumer_key = twitter['ConsumerKey']
    twitter_consumer_secret = twitter['ConsumerSecret']
    twitter_access_token = twitter['AccessToken']
    twitter_access_token_secret = twitter['AccessTokenSecret']
    twitter_stream_start_text = twitter['StreamStartText']
    twitter_stream_stop_text = twitter['StreamStopText']
    twitter_track_update_text = twitter['TrackUpdateText']


def note_on(note, channel, velocity):
    global midi_output

    if midi_output is not None:
        midi_output.send(mido.Message('note_on',
                                      channel=channel,
                                      note=note,
                                      velocity=velocity))


def note_off(note, channel):
    global midi_output

    if midi_output is not None:
        midi_output.send(mido.Message('note_off',
                                      channel=channel,
                                      note=note))


def update_obs_current_scene_index():
    global obs
    global obs_scenes
    global obs_current_scene_index

    current_scene = obs.call(obswebsocket.requests.GetCurrentScene())
    obs_current_scene_index = obs_scenes.index(current_scene.getName())


def set_obs_current_scene_note(on_or_off):
    global obs_scene_selection_notes
    global obs_current_scene_channel
    global obs_current_scene_velocity
    global obs_current_scene_index

    if on_or_off:
        note_on(channel=obs_current_scene_channel,
                note=obs_scene_selection_notes[obs_current_scene_index],
                velocity=obs_current_scene_velocity)
    else:
        note_off(channel=obs_current_scene_channel,
                 note=obs_scene_selection_notes[obs_current_scene_index])


def clear_obs_stream_status_note():
    global obs_stream_status_note
    global obs_stream_status_channel

    note_off(channel=obs_stream_status_channel,
             note=obs_stream_status_note)


def update_obs_stream_status_note():
    global obs_stream_on
    global obs_stream_status_note
    global obs_stream_status_channel
    global obs_stream_status_on_velocity
    global obs_stream_status_off_velocity

    if obs_stream_on:
        note_on(channel=obs_stream_status_channel,
                note=obs_stream_status_note,
                velocity=obs_stream_status_on_velocity)
    else:
        note_on(channel=obs_stream_status_channel,
                note=obs_stream_status_note,
                velocity=obs_stream_status_off_velocity)


def on_obstransition(message):
    global obs_in_transition

    obs_in_transition = True


def do_obs_scene_changed():
    global obs_in_transition

    set_obs_current_scene_note(False)
    update_obs_current_scene_index()
    set_obs_current_scene_note(True)

    obs_in_transition = False


def on_obs_scene_changed(message):
    Thread(target=do_obs_scene_changed).start()


def on_obs_streamstarted(message):
    global obs_stream_on
    global twitter_stream_start_text

    obs_stream_on = True

    update_obs_stream_status_note()
    tweet(twitter_stream_start_text)

    return


def on_obs_streamstopped(message):
    global obs_stream_on
    global twitter_stream_stop

    obs_stream_on = False

    update_obs_stream_status_note()
    tweet(twitter_stream_stop_text)

    return


def on_listening_midi_msg(message):
    if(message.type != 'note_on'):
        return

    print(f'Note: {message.note} Channel: {message.channel + 1}')


def on_midi_msg(message):
    global obs
    global obs_in_transition
    global obs_scenes
    global obs_current_scene_channel
    global obs_scene_selection_notes
    global obs_stream_status_note
    global obs_stream_on
    global next_track_title_string
    global next_track_artist_string
    global next_track_filename
    global current_track_title_string
    global current_track_artist_string
    global current_track_filename
    global new_track_available_channel
    global new_track_available_note
    global clear_current_track_channel
    global clear_current_track_note
    global skip_next_track_channel
    global skip_next_track_note

    if(message.type != 'note_on'):
        return

    if message.channel == new_track_available_channel and message.note == new_track_available_note:
        if next_track_title_string is None:
            return

        current_track_title_string = next_track_title_string
        next_track_title_string = None

        current_track_artist_string = next_track_artist_string
        next_track_artist_string = None

        current_track_filename = next_track_filename
        next_track_filename = None
    elif message.channel == clear_current_track_channel and message.note == clear_current_track_note:
        print('Clearing Track Name')

        next_track_title_string = ''
        current_track_title_string = next_track_title_string

        next_track_artist_string = ''
        current_track_artist_string = next_track_artist_string

        next_track_filename = None
        current_track_filename = next_track_filename
    elif message.channel == skip_next_track_channel and message.note == skip_next_track_note:
        if next_track_title_string is None:
            return

        print('Skipping Next Track')

        next_track_title_string = None
        next_track_artist_string = None
        next_track_filename = None
    elif message.channel == obs_stream_status_channel and message.note == obs_stream_status_note:
        if obs_stream_on:
            print('Set Stream OFF')
            obs.call(obswebsocket.requests.StopStreaming())
        else:
            print('Set Stream ON')
            obs.call(obswebsocket.requests.StartStreaming())

        return
    elif message.channel == obs_current_scene_channel and message.note in obs_scene_selection_notes:
        if obs_in_transition:
            return

        scene_index = obs_scene_selection_notes.index(message.note)
        if scene_index >= len(obs_scenes):
            return

        obs.call(obswebsocket.requests.SetCurrentScene(
            obs_scenes[scene_index]))
        return
    else:
        return

    update_track_string()
    update_track_artwork()
    update_twitter_status()


def setup_twitter():
    global twitter_client
    global twitter_bearer_token
    global twitter_consumer_key
    global twitter_consumer_secret
    global twitter_access_token
    global twitter_access_token_secret

    print('Setting up Twitter...')

    twitter_client = tweepy.Client(bearer_token=twitter_bearer_token,
                                   consumer_key=twitter_consumer_key,
                                   consumer_secret=twitter_consumer_secret,
                                   access_token=twitter_access_token,
                                   access_token_secret=twitter_access_token_secret)


def tweet(text, media_id=None):
    global twitter_client

    if twitter_client is None:
        return

    # -- Update the status
    print(f'Tweet!: {text}')
    if media_id is None:
        twitter_client.create_tweet(text=text)
    else:
        twitter_client.create_tweet(text=text, media_ids=[media_id])


def update_twitter_status():
    global twitter_track_update_text
    global current_track_title_string
    global current_track_artist_string
    global playing_track_artwork_filename

    if len(current_track_title_string) == 0 or len(current_track_title_string) == 0:
        return

    update_message = twitter_track_update_text.replace(
        '{title}', current_track_title_string)
    update_message = update_message.replace(
        '{artist}', current_track_artist_string)

    # media_id = None

    # if os.path.exists(playing_track_artwork_filename):
    #    base = os.path.splitext(playing_track_artwork_filename)[0]
    #    extension = imghdr.what(playing_track_artwork_filename)

    #    new_playing_track_artwork_filename = base + '.' + extension

    #    with open(playing_track_artwork_filename, 'rb') as file:
    #        media_id = API.simple_upload(new_playing_track_artwork_filename,
    # file)

    tweet(update_message)


def update_track_artwork(need_placeholder_artwork=True):
    global current_track_filename
    global playing_track_artwork_filename

    artwork = None

    if current_track_filename is not None:
        if os.path.exists(current_track_filename):
            try:
                # -- Mutagen can automatically detect format and type of tags
                file = MutagenFile(current_track_filename)

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
    global current_track_title_string
    global current_track_artist_string
    global playing_track_title_filename
    global playing_track_artist_filename

    title = current_track_title_string
    artist = current_track_artist_string

    if len(title) != 0 and playing_track_title_prefix is not None:
        title = playing_track_title_prefix + ' ' + title

    if len(artist) != 0 and playing_track_artist_prefix is not None:
        artist = playing_track_artist_prefix + ' ' + artist

    Path(playing_track_title_filename).write_text(f'{title}')
    Path(playing_track_artist_filename).write_text(f'{artist}')

    print(f'Output: {title} {artist}')


def check_for_new_tracks():
    global light_on
    global midi_output
    global next_track_title_string
    global new_track_available_channel
    global new_track_available_note
    global new_track_available_velocity
    global skip_next_track_channel
    global skip_next_track_note
    global skip_next_track_velocity

    if next_track_title_string is not None:
        note_on(skip_next_track_note,
                skip_next_track_channel,
                skip_next_track_velocity)

        if midi_output is not None:
            if light_on:
                light_on = False
                note_off(new_track_available_note, new_track_available_channel)
            else:
                light_on = True
                note_on(new_track_available_note,
                        new_track_available_channel,
                        new_track_available_velocity)
    else:
        light_on = False
        note_off(new_track_available_note, new_track_available_channel)
        note_off(skip_next_track_note, skip_next_track_channel)


def update_traktor_meta(data):
    global next_track_title_string
    global next_track_artist_string
    global next_track_filename
    global playing_track_title_prefix
    global playing_track_artist_prefix
    global track_file_collection

    info = dict(data)
    title = info.get("title", "")
    artist = info.get("artist", "")

    if len(title) == 0 or len(artist) == 0:
        return

    track_string = f'{title}{artist}'
    next_track_title_string = title
    next_track_artist_string = artist

    print(f'Available: {title} {artist}')

    next_track_filename = track_file_collection.get(track_string, None)


def setup_obs():
    global obs
    global obs_scenes
    global obs_stream_on
    global obs_server_address
    global obs_server_port
    global obs_server_password

    print('Setting up OBS...')

    obs = obswebsocket.obsws(obs_server_address,
                             obs_server_port,
                             obs_server_password)
    obs.register(on_obstransition, obswebsocket.events.TransitionBegin)
    obs.register(on_obs_scene_changed, obswebsocket.events.TransitionEnd)
    obs.register(on_obs_streamstarted, obswebsocket.events.StreamStarted)
    obs.register(on_obs_streamstopped, obswebsocket.events.StreamStopped)

    try:
        obs.connect()
    except obswebsocket.exceptions.ConnectionFailure:
        obs = None
        return

    # -- Get all the scenes
    all_scenes = obs.call(obswebsocket.requests.GetSceneList())
    for s in all_scenes.getScenes():
        obs_scenes.append(s['name'])

    # -- Update the current scene
    update_obs_current_scene_index()
    set_obs_current_scene_note(True)

    # -- Update the initial stream status
    status = obs.call(obswebsocket.requests.GetStreamingStatus())
    obs_stream_on = status.getStreaming()

    update_obs_stream_status_note()


def setup_midi():
    global midi_input
    global midi_output
    global input_device_name
    global output_device_name

    print('Setting up midi...')

    if input_device_name is not None:
        for device_name in mido.get_input_names():
            if device_name == input_device_name:
                midi_input = mido.open_input(device_name, callback=on_midi_msg)
                print(f'Input: {device_name}')
                break

    if output_device_name is not None:
        for device_name in mido.get_output_names():
            if device_name == output_device_name:
                midi_output = mido.open_output(device_name)
                print(f'Output: {device_name}')
                break


def parse_traktor_collection():
    global track_file_collection
    global traktor_collection_path

    print('Parsing Traktor collection...')

    xml_root = xml_tree.ElementTree(file=traktor_collection_path).getroot()

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

            if key not in track_file_collection:
                track_file_collection[key] = filename


def read_args(args):
    global midi_input
    global midi_output

    try:
        # -- Gather the arguments
        opts, arg = getopt.getopt(args, 'dl:')

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
                    print(f'Listening from "{arg_val}".')

                    midi_input = mido.open_input(arg_val,
                                                 callback=on_listening_midi_msg)
                    if midi_input is not None:
                        try:
                            while 1:
                                sleep(1)
                        except KeyboardInterrupt:
                            pass

                        if midi_input is not None:
                            midi_input.close()

                    sys.exit(2)
    except getopt.GetoptError:
        print('usage: args_demo.py <-devices> <-listen>')
        sys.exit(2)


def shutdown():
    global obs
    global obs_stream_on
    global midi_input
    global midi_output
    global new_track_available_note
    global new_track_available_channel
    global skip_next_track_note
    global skip_next_track_channel

    if obs is not None:
        clear_obs_stream_status_note()

        set_obs_current_scene_note(False)

        obs.disconnect()

    if midi_input is not None:
        midi_input.close()

    if midi_output is not None:
        if new_track_available_note:
            note_off(new_track_available_note, new_track_available_channel)
            note_off(skip_next_track_note, skip_next_track_channel)

            # -- Give some time for the note off to be sent thru
            sleep(5)

        midi_output.close()


def main():
    # -- Remove the first argument (the filename)
    read_args(sys.argv[1:])

    read_config()
    update_track_string()
    update_track_artwork(False)
    set_interval(1, check_for_new_tracks)
    setup_midi()
    setup_obs()
    setup_twitter()
    parse_traktor_collection()

    print('Listening to Traktor...')
    listener = TraktorListener(port=8000,
                               quiet=True,
                               custom_callback=update_traktor_meta)

    listener.start()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass

    shutdown()
