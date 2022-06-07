#
# TwitterClient
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

# TODO: Add back support for V2 API tweeting
# self.bearer_token = config['BearerToken']
# self.api = tweepy.Client(bearer_token=self.bearer_token, consumer_key=self.consumer_key,
#                          consumer_secret=self.consumer_secret, access_token=self.access_token,
#                          access_token_secret=self.access_token_secret)

import os
import tweepy


# -- Classes
class TwitterClient:
    """Manage all our Twitter interactions."""

    def __init__(self, config, posts):
        """Initialize the client based on user configuration."""

        print('Setting up Twitter...')

        self.consumer_key = config['ConsumerKey']
        self.consumer_secret = config['ConsumerSecret']
        self.access_token = config['AccessToken']
        self.access_token_secret = config['AccessTokenSecret']

        self.stream_start_text = posts['StreamStartText']
        self.stream_stop_text = posts['StreamStopText']
        self.track_update_text = posts['TrackUpdateText']
        self.track_update_no_label_text = posts['TrackUpdateNoLabelText']
        self.last_tweet_status_id = None

        auth = tweepy.OAuthHandler(self.consumer_key, self.consumer_secret)
        auth.set_access_token(self.access_token, self.access_token_secret)
        self.api = tweepy.API(auth)

    def tweet(self, text, in_reply_to=None, media_filename=None):
        """Tweet some text.

        Parameters
        ----------
        text : str
            Text of the status to post.
        in_reply_to : Optional[str]
            Optional status ID of a tweet to reply to.
        media_filename : Optional[str]
            Optional filename of an image to post as media.

        Returns
        -------
        str
            Status ID of the tweet posted or None if something went wrong.
        """

        if self.api is None:
            return None

        result = None
        media_ids = None

        # -- Update the status
        try:
            if media_filename is not None and os.path.exists(media_filename):
                # -- Posting media requires elevated Twitter API access (because it uses V1 api)
                file = open(media_filename, 'rb')
                media = self.api.media_upload(filename=media_filename, file=file)
                file.close()

                media_ids = [media.media_id_string]

            result = self.api.update_status(status=text,
                                            in_reply_to_status_id=in_reply_to,
                                            media_ids=media_ids)

            print(f'Tweet!: {text}')
        except Exception as e:
            print(f'Error tweeting!: {text}')
            print(f'-=> {e}')
            return None

        return result.id_str

    def tweet_start_text(self):
        """Tweet the stream start text."""
        self.last_tweet_status_id = self.tweet(self.stream_start_text)

    def tweet_stop_text(self):
        """Tweet the stream stop text."""
        self.tweet(self.stream_stop_text, in_reply_to=self.last_tweet_status_id)
        self.last_tweet_status_id = None

    def update_status(self, title, artist, label=None, artwork_filename=None):
        """Tweet the currently playing track."""

        if len(title) == 0 or len(artist) == 0:
            return

        if label is None or len(label) == 0:
            update_message = self.track_update_no_label_text.replace('{title}', title)
            update_message = update_message.replace('{artist}', artist)
        else:
            update_message = self.track_update_text.replace('{title}', title)
            update_message = update_message.replace('{artist}', artist)
            update_message = update_message.replace('{label}', label)

        self.last_tweet_status_id = self.tweet(text=update_message,
                                               media_filename=artwork_filename,
                                               in_reply_to=self.last_tweet_status_id)
