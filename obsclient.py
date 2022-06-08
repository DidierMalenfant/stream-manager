#
# OBSClient
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

import obswebsocket
import obswebsocket.events
import obswebsocket.requests

from threading import Thread


# -- Classes
class OBSClient:
    """Manage all our OBS interactions."""

    def __init__(self, config, midi_client, post_clients):
        """Initialize the client based on user configuration."""

        print('Setting up OBS...')

        self.midi_client = midi_client
        self.post_clients = post_clients
        self.server_address = config['ObsServerAddress']
        self.server_port = int(config['ObsServerPort'])
        self.server_password = config['ObsServerPassword']
        self.current_scene_channel = int(config['CurrentSceneChannel']) - 1
        self.current_scene_velocity = int(config['CurrentSceneVelocity'])

        self.scene_selection_notes = []
        for note_as_string in config['SceneSelectionNotes'].split(','):
            note = int(note_as_string)

            self.scene_selection_notes.append(note)

            self.midi_client.add_callback(self.current_scene_channel, note,
                                          self.set_current_scene)

        self.stream_status_note = int(config['StreamStatusNote'])
        self.stream_status_channel = int(config['StreamStatusChannel']) - 1
        self.stream_status_on_velocity = int(config['StreamOnVelocity'])
        self.stream_status_off_velocity = int(config['StreamOffVelocity'])

        self.midi_client.add_callback(self.stream_status_channel,
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
        if on_or_off:
            self.midi_client.note_on(channel=self.current_scene_channel,
                                     note=self.scene_selection_notes[self.current_scene_index],
                                     velocity=self.current_scene_velocity)
        else:
            self.midi_client.note_off(channel=self.current_scene_channel,
                                      note=self.scene_selection_notes[self.current_scene_index])

    def update_stream_status_note(self):
        if self.stream_on:
            self.midi_client.note_on(channel=self.stream_status_channel, note=self.stream_status_note,
                                     velocity=self.stream_status_on_velocity)
        else:
            self.midi_client.note_on(channel=self.stream_status_channel, note=self.stream_status_note,
                                     velocity=self.stream_status_off_velocity)

    def start_streaming(self):
        print('Set Stream ON')
        self.obs.call(obswebsocket.requests.StartStreaming())

    def stop_streaming(self):
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
        self.stream_on = True

        self.update_stream_status_note()

        for client in self.post_clients:
            client.post_start_text()

        return

    def on_stream_stopped(self, message):
        self.stream_on = False

        self.update_stream_status_note()

        for client in self.post_clients:
            client.post_stop_text()

        return

    def shutdown(self):
        print('Shutting down obs...')

        if self.obs is None:
            return

        self.obs.disconnect()
