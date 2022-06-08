#
# TraktorClient
# Copyright 2021-2022 by Didier Malenfant.
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
import xml.etree.ElementTree as xml_tree

from traktor_nowplaying import Listener as TraktorListener
from mutagen import File as MutagenFile
from pathlib import Path
from threading import Thread
from time import sleep


# -- Functions
def call_at_interval(period, callback, args):
    while True:
        sleep(period)
        callback(*args)


def set_interval(period, callback, *args):
    Thread(target=call_at_interval, args=(period, callback, args)).start()


# -- Classes
class TraktorClient:
    """Manage all our Traktor interactions."""

    def __init__(self, config, midi_client, post_clients):
        """Initialize the client based on user configuration."""

        print('Setting up Traktor...')

        self.midi_client = midi_client
        self.post_clients = post_clients
        self.playing_track_title_filename = config['OutputTitleFilename']
        self.playing_track_title_prefix = config['OutputTitlePrefix']
        self.playing_track_artist_filename = config['OutputArtistFilename']
        self.playing_track_artist_prefix = config['OutputArtistPrefix']
        self.playing_track_label_filename = config['OutputLabelFilename']
        self.playing_track_label_prefix = config['OutputLabelPrefix']
        self.playing_track_artwork_filename = config['OutputArtworkFilename']
        self.no_artwork_placeholder_filename = config['NoArtworkPlaceHolderFilename']
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
        self.next_track_label_string = None
        self.next_track_filename = None
        self.current_track_title_string = ''
        self.current_track_artist_string = ''
        self.current_track_label_string = ''
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

        track_info = self.track_file_collection.get(track_string, None)
        if track_info is None:
            self.next_track_filename = None
            self.next_track_label_string = ''
        else:
            self.next_track_filename = track_info[0]
            self.next_track_label_string = track_info[1]

    def update_track_artwork(self, need_placeholder_artwork=True):
        artwork = None

        if self.current_track_filename is not None:
            if os.path.exists(self.current_track_filename):
                try:
                    # -- Mutagen can automatically detect format and type of tags
                    file = MutagenFile(self.current_track_filename)

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
                    with open(self.playing_track_artwork_filename, 'wb') as dest_file:
                        dest_file.write(artwork)
                        need_placeholder_artwork = False

        if need_placeholder_artwork:
            with open(self.no_artwork_placeholder_filename, 'rb') as src_file:
                with open(self.playing_track_artwork_filename, 'wb') as dest_file:
                    dest_file.write(src_file.read())
        elif not artwork and os.path.exists(self.playing_track_artwork_filename):
            os.remove(self.playing_track_artwork_filename)

    def update_track_string(self):
        title = self.current_track_title_string

        if len(title) != 0 and self.playing_track_title_prefix is not None:
            title = self.playing_track_title_prefix + ' ' + title

        Path(self.playing_track_title_filename).write_text(f'{title}')

        artist = self.current_track_artist_string

        if len(artist) != 0 and self.playing_track_artist_prefix is not None:
            artist = self.playing_track_artist_prefix + ' ' + artist

        Path(self.playing_track_artist_filename).write_text(f'{artist}')

        label = self.current_track_label_string

        if len(label) != 0 and self.playing_track_label_prefix is not None:
            label = self.playing_track_label_prefix + ' ' + label

        Path(self.playing_track_label_filename).write_text(f'{label}')

        print(f'Output: {title} {artist} {label}')

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

                label = ''
                info = entry.find('INFO')

                if info is not None:
                    found_label = info.get('LABEL')

                    if found_label is not None:
                        label = found_label

                if key not in self.track_file_collection:
                    self.track_file_collection[key] = [filename, label]

    def start(self):
        self.parse_collection()

        set_interval(1, self.check_for_new_tracks)

        print('Listening to Traktor...')
        listener = TraktorListener(port=8000, quiet=True, custom_callback=self.update_meta)

        listener.start()

    def check_for_new_tracks(self):
        if self.next_track_title_string is not None:
            self.midi_client.note_on(self.skip_next_track_note, self.skip_next_track_channel,
                                     self.skip_next_track_velocity)

            if self.light_on:
                self.light_on = False
                self.midi_client.note_off(self.new_track_available_note,
                                          self.new_track_available_channel)
            else:
                self.light_on = True
                self.midi_client.note_on(self.new_track_available_note,
                                         self.new_track_available_channel,
                                         self.new_track_available_velocity)
        else:
            self.light_on = False
            self.midi_client.note_off(self.new_track_available_note,
                                      self.new_track_available_channel)
            self.midi_client.note_off(self.skip_next_track_note,
                                      self.skip_next_track_channel)

    def new_track_available(self, channel, note):
        if self.next_track_title_string is None:
            return

        self.current_track_title_string = self.next_track_title_string
        self.next_track_title_string = None

        self.current_track_artist_string = self.next_track_artist_string
        self.next_track_artist_string = None

        self.current_track_label_string = self.next_track_label_string
        self.next_track_label_string = None

        self.current_track_filename = self.next_track_filename
        self.next_track_filename = None

        self.update_track_string()
        self.update_track_artwork()

        for client in self.post_clients:
            client.post_status(self.current_track_title_string,
                               self.current_track_artist_string,
                               self.current_track_label_string,
                               self.playing_track_artwork_filename)

    def clear_current_track(self, channel, note):
        print('Clearing Track Name')

        self.next_track_title_string = ''
        self.current_track_title_string = self.next_track_title_string

        self.next_track_artist_string = ''
        self.current_track_artist_string = self.next_track_artist_string

        self.next_track_label_string = ''
        self.current_track_label_string = self.next_track_label_string

        self.next_track_filename = None
        self.current_track_filename = self.next_track_filename

        self.update_track_string()
        self.update_track_artwork(False)

    def skip_next_track(self, channel, note):
        if self.next_track_title_string is None:
            return

        print('Skipping Next Track')

        self.next_track_title_string = None
        self.next_track_artist_string = None
        self.next_track_label_string = None
        self.next_track_filename = None

        self.update_track_string()
        self.update_track_artwork()
