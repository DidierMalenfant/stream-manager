#
# MidiClient
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

import mido
import sys

from time import sleep


# -- Functions
def on_listening_midi_msg(message):
    if(message.type != 'note_on'):
        return

    print(f'Note: {message.note} Channel: {message.channel + 1}')


# -- Classes
class MidiClient:
    """Manage all our Midi interactions."""

    def __init__(self, config):
        """Initialize the client based on user configuration."""

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

    @staticmethod
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

    @staticmethod
    def print_midi_devices():
        print('Input Devices:')
        print(f'{mido.get_input_names()}')
        print('Output Devices:')
        print(f'{mido.get_output_names()}')
