#
# MastodonClient
# Copyright 2021-2022 by Didier Malenfant.
#
# A little Swiss army knife script used to listen to tracks being played
# in Traktor, control OBS via midi and post track lists to social media.
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
import magic

from mastodon import Mastodon


# -- Classes
class MastodonClient:
    """Manage all our Mastodon interactions."""

    def __init__(self, config, posts):
        """Initialize the client based on user configuration."""

        print('Setting up Mastodon...')

        self.stream_start_text = posts['StreamStartText']
        self.stream_stop_text = posts['StreamStopText']
        self.track_update_text = posts['TrackUpdateText']
        self.track_update_no_label_text = posts['TrackUpdateNoLabelText']
        self.last_toot_status_id = None

        self.client = Mastodon(client_id=config['ClientID'],
                               client_secret=config['ClientSecret'],
                               access_token=config['AccessToken'],
                               api_base_url=config['APIBaseURL'])

    def toot(self, text, in_reply_to=None, media_filename=None):
        """Toot some text."""

        if self.client is None:
            return None

        result = None

        # -- Update the status
        try:
            media_ids = None

            if media_filename is not None and os.path.exists(media_filename):
                mime = magic.Magic(mime=True)
                mime_type = mime.from_file(media_filename)

                with open(media_filename, 'rb') as f:
                    content = f.read()

                    media = self.client.media_post(media_file=content,
                                                   mime_type=mime_type,
                                                   description="Cover art of the track currently playing.")
                    media_ids = [media["id"]]

                    f.close()

            result = self.client.status_post(text,
                                             in_reply_to_id=in_reply_to,
                                             media_ids=media_ids)

            print(f'Toot!: {text}')
        except Exception as e:
            print(f'Error tooting!: {text}')
            print(f'-=> {e}')
            return None

        return result["id"]

    def post_start_text(self):
        """Toot the stream start text."""
        self.last_toot_status_id = self.toot(self.stream_start_text)

    def post_stop_text(self):
        """Toot the stream stop text."""
        self.toot(self.stream_stop_text, in_reply_to=self.last_toot_status_id)
        self.last_toot_status_id = None

    def post_status(self, title, artist, label=None, artwork_filename=None):
        """Toot the currently playing track."""

        if len(title) == 0 or len(artist) == 0:
            return

        if label is None or len(label) == 0:
            update_message = self.track_update_no_label_text.replace('{title}', title)
            update_message = update_message.replace('{artist}', artist)
        else:
            update_message = self.track_update_text.replace('{title}', title)
            update_message = update_message.replace('{artist}', artist)
            update_message = update_message.replace('{label}', label)

        self.last_toot_status_id = self.toot(text=update_message,
                                             media_filename=artwork_filename,
                                             in_reply_to=self.last_toot_status_id)
