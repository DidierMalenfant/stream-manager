#!/usr/bin/env python3
#
# DPRStreamManager
# Copyright 2021-2022 by Didier Malenfant.
#
# A little Swiss army knife script used to listen to tracks being played
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

# TODO: Document code.
# TODO: Better error handling.
# TODO: If no midi device is present just post new tracks right away.

import configparser
import getopt
import os
import sys

from midiclient import MidiClient
from twitterclient import TwitterClient
from mastodonclient import MastodonClient
from obsclient import OBSClient
from traktorclient import TraktorClient


# -- Classes
class StreamManager:
    """Manage our stream."""

    def __init__(self, args):
        """Initialize the client based on user configuration."""

        self.midi_client = None
        self.obs_client = None
        self.twitter_client = None
        self.traktor_client = None

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
                        MidiClient.print_midi_devices()
                        sys.exit(2)
                    elif opt == '-l':
                        MidiClient.listen_to_midi(arg_val)

        except getopt.GetoptError:
            print('usage: StreamManager.py <-devices> <-listen> config.ini')
            sys.exit(2)

        if config_file_path is None:
            print('Couldn\'t find any ini file path on the command line.')
            sys.exit(2)

        print('Reading configuration...')

        if not os.path.exists(config_file_path):
            print(f'Can\'t read ini file at \'{config_file_path}\'.')
            sys.exit(2)

        config = configparser.ConfigParser()
        config.read(config_file_path)

        self.midi_client = MidiClient(config['midi'])
        self.twitter_client = TwitterClient(config['twitter'], config['posts'])
        self.mastodon_client = MastodonClient(config['mastodon'], config['posts'])

        post_clients = [self.twitter_client, self.mastodon_client]

        self.traktor_client = TraktorClient(config['traktor'], self.midi_client, post_clients)
        self.obs_client = OBSClient(config['obs'], self.midi_client, post_clients)

    def main(self):
        if self.traktor_client is not None:
            self.traktor_client.update_track_string()
            self.traktor_client.update_track_artwork(False)
            self.traktor_client.start()

    def shutdown(self):
        if self.midi_client is not None:
            self.midi_client.shutdown()

        if self.obs_client is not None:
            self.obs_client.shutdown()


def main():
    stream_manager = None

    try:
        # -- Remove the first argument (which is the script filename)
        stream_manager = StreamManager(sys.argv[1:])

        if stream_manager is not None:
            stream_manager.main()
    except KeyboardInterrupt:
        pass

    if stream_manager is not None:
        stream_manager.shutdown()


if __name__ == '__main__':
    main()
