#!/usr/bin/env python3
"""Render VirtualDJ stem lighting with optional BlackHole melody sparkles."""
import argparse
import json
import math
import socket
import sys
import time

# Reuse the existing project environment for the plain python3 entry point.
import importlib.util
import os
from pathlib import Path
if __name__ == '__main__' and importlib.util.find_spec('serial') is None:
    python = Path(__file__).resolve().parent / 'venv/bin/python'
    if python.is_file() and Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), __file__, *sys.argv[1:]])

import serial
from audio_led import brightness_percent, find_arduino_port, midi_name
from virtualdj_pixels import EnergyShowPixels, PixelLink, BLACK
from section_palette import SectionPalette

STEMS = ('vocal', 'instru', 'bass', 'kick', 'hihat')


def clamp(value):
    return max(0.0, min(1.0, value))


def validate_packet(raw, now):
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('unsupported bridge packet')
    for field in ('sent_at', 'master'):
        if not isinstance(data.get(field), (int, float)) or not math.isfinite(data[field]):
            raise ValueError(f'invalid {field}')
    if not -0.1 <= now - data['sent_at'] <= 0.25:
        raise ValueError('stale bridge packet')
    decks = data.get('decks')
    if not isinstance(decks, list) or not 1 <= len(decks) <= 4:
        raise ValueError('invalid decks')
    ids = set()
    for deck in decks:
        if not isinstance(deck, dict) or deck.get('id') not in (1, 2, 3, 4):
            raise ValueError('invalid deck')
        if deck['id'] in ids:
            raise ValueError('duplicate deck')
        ids.add(deck['id'])
        if not isinstance(deck.get('valid'), bool):
            raise ValueError('invalid query status')
        for field in ('playing', 'volume', 'level', 'bpm', 'beatpos', *STEMS):
            value = deck.get(field)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'invalid deck {field}')
    return data


class StemLights:
    def __init__(self):
        self.beats = {}
        self.last_time = None
        self.activity = 0.0
        self.hue = 0.0
        self.last_vocal = 0.0
        self.last_phrase = -100.0
        self.baseline = None
        self.started = None
        self.last_drop = -100.0
        self.palettes = SectionPalette()

    def frame(self, data, now):
        dt = 0.02 if self.last_time is None else min(0.2, max(0, now-self.last_time))
        self.last_time = now
        audible = [d for d in data['decks'] if d['valid'] and d['playing'] > 0
                   and d['volume'] > 0 and d['level'] > 0.001]
        sums = {s: clamp(sum(max(0, d[s])*clamp(d['volume']) for d in audible)) for s in STEMS}
        level = clamp(data['master']) if audible else 0.0
        event = 0
        dominant = max(audible, key=lambda d: d['level']*d['volume'], default=None)
        bpm = max(50, min(200, round(dominant['bpm']))) if dominant else 90
        # Grid crossings generate only one pulse, never replay skipped beats.
        active_ids = {d['id'] for d in audible}
        self.beats = {key: value for key, value in self.beats.items() if key in active_ids}
        for deck in audible:
            beat = math.floor(deck['beatpos'])
            old = self.beats.get(deck['id'])
            self.beats[deck['id']] = deck['beatpos']
            if (level > 0.001 and deck is dominant and old is not None
                    and 0 < deck['beatpos']-old < 0.5 and beat > math.floor(old)):
                event = 2 if beat % 4 == 0 else 1
        rhythm = clamp((sums['kick'] + sums['hihat'])*3)
        target = clamp(0.65*rhythm + 0.25*sums['bass']*3 + 0.10*sums['instru'])
        if level < 0.001:
            target = 0.0
            self.started = None
            self.baseline = None
        elif self.started is None:
            self.started = now
        self.activity += (target-self.activity)*dt/(1.5+dt)
        # Only an established, large energy increase with strong bass is a drop.
        if self.baseline is not None and self.started is not None:
            if (now-self.started > 2 and event and sums['bass'] > 0.15
                    and level > max(0.20, self.baseline*2.5) and now-self.last_drop > 12):
                event = 3
                self.last_drop = now
        if level > 0.001:
            self.baseline = level if self.baseline is None else self.baseline+(level-self.baseline)*dt/(4+dt)
        phrase = (level > 0.001 and sums['vocal'] > max(0.08, self.last_vocal*1.6)
                  and now-self.last_phrase > 0.65)
        if phrase:
            self.last_phrase = now
        self.last_vocal = sums['vocal']
        # Meter data has no pitch: gentle instrument-driven movement, not note tracking.
        self.hue = (self.hue + dt*(40+500*sums['instru'])*self.activity) % 65536
        frame = dict(level=round(math.sqrt(level)*255), bass=round(math.sqrt(sums['bass'])*255),
                     treble=round(math.sqrt(sums['hihat'])*255), hue=round(self.hue),
                     mood=round(self.activity*255), activity=round(self.activity*255),
                     event=event, phrase=int(phrase), bpm=bpm,
                     onset=clamp(sums['kick']*4), db=20*math.log10(max(level, 1e-9)))
        self.palettes.update(frame, now)
        frame.update(palette=self.palettes.palette, palette_fast=self.palettes.fast)
        return frame


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('brightness', nargs='?', type=brightness_percent, default=100)
    parser.add_argument('--darkness', type=brightness_percent, default=35, help='legacy option; ignored by pixel rendering')
    parser.add_argument('--serial-port', help='Arduino port (otherwise auto-detect)')
    parser.add_argument('--monitor', action='store_true', help='show live stem readings without opening Arduino')
    parser.add_argument('--no-melody', action='store_true', help='disable BlackHole pitch analysis')
    parser.add_argument('--melody-device', default='BlackHole 2ch', help='stereo input for melody analysis')
    return parser.parse_args(argv)


def run(args):
    lights = StemLights()
    renderer = EnergyShowPixels(args.brightness)
    melody = None
    link = None
    next_frame = 0.0
    ser = None
    last_received = None
    last_status = 0.0
    last_sent_at = 0.0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 19785))
        sock.settimeout(0.002)
        try:
            if not args.monitor:
                port = args.serial_port or find_arduino_port()
                if not port:
                    raise RuntimeError('Arduino not found. Connect it or use --monitor to test VirtualDJ.')
                ser = serial.Serial(port, 500000, timeout=.03, write_timeout=.2)
                time.sleep(2)
                ser.reset_input_buffer()
                link = PixelLink(ser)
                link.send(BLACK)
                print('Computer-rendered RGB: 500000 baud, target 30 FPS, kick + vocals + instrumentals.', flush=True)
            if not args.no_melody:
                from virtualdj_melody import MelodyInput
                import sounddevice as sd
                try:
                    melody = MelodyInput(args.melody_device).start()
                    print(f'Melody input: {args.melody_device}; route VirtualDJ Master there.', flush=True)
                except (RuntimeError, sd.PortAudioError) as exc:
                    print(f'Melody unavailable: {exc}; stem layers remain active.', flush=True)
            print('Waiting for LED Stem Bridge on localhost:19785. Ctrl+C to quit.', flush=True)
            print('Melody capture does not play audio. Keep your audio-delay relay running for BlackHole playback.', flush=True)
            while True:
                data = None
                try:
                    raw = sock.recv(8192)
                    data = validate_packet(raw, time.time())
                    # Drain queued packets, retaining only the newest valid snapshot.
                    sock.setblocking(False)
                    while True:
                        try:
                            candidate = validate_packet(sock.recv(8192), time.time())
                            if candidate['sent_at'] > data['sent_at']:
                                data = candidate
                        except (ValueError, KeyError, TypeError):
                            continue
                        except BlockingIOError:
                            break
                except (socket.timeout, ValueError, KeyError, TypeError):
                    pass
                finally:
                    sock.settimeout(0.002)
                now = time.monotonic()
                if data is not None and data['sent_at'] > last_sent_at:
                    last_sent_at = data['sent_at']
                    last_received = now
                    frame = lights.frame(data, now)
                    renderer.update(data, now)
                    if now-last_status > 1:
                        meters = ' | '.join(f"deck {d['id']} " + ' '.join(f'{s}={d[s]:.2f}' for s in STEMS)
                                            for d in data['decks'] if d['valid'] and d['playing'])
                        reading = melody.reading if melody else None
                        note_label = midi_name(reading.note) if reading and reading.note is not None and now-reading.at <= .35 else '--'
                        audio_label = f"audio={reading.rms:.4f} pitch_conf={reading.confidence:.2f}" if reading else "melody=off"
                        print(f"{frame['bpm']} BPM | master={data['master']:.3f} "
                              f"pixel={max(renderer.render(now))}/255 kicks={renderer.kick_count} drops={renderer.drop_count} note={note_label} {audio_label} energy={renderer.music.energy:.2f} "
                              f"event={frame['event']} | "
                              f"{meters or 'No playing deck / stem queries unavailable'}", flush=True)
                        last_status = now
                elif last_received is None or now-last_received > 0.3:
                    if now-last_status > 3:
                        print('No fresh bridge data; showing idle gradient. Enable LED Stem Bridge in a VirtualDJ effect slot; restart VirtualDJ if it is not listed.', flush=True)
                        last_status = now
                now = time.monotonic()
                if melody:
                    renderer.set_melody(melody.reading,now)
                    if melody.status:
                        print(f'Melody: {melody.status}', flush=True)
                        melody.status = ''
                if link and now >= next_frame:
                    link.send(renderer.render(now))
                    next_frame = max(next_frame + 1/30, time.monotonic())
        finally:
            if melody:
                melody.close()
            if ser:
                try:
                    if link:
                        link.send(BLACK)
                except (OSError, RuntimeError, serial.SerialException):
                    pass  # Firmware watchdog also clears the last displayed frame.
                finally:
                    ser.close()


def main():
    try:
        run(parse_args())
    except KeyboardInterrupt:
        print('\nStopped.')
    except (OSError, RuntimeError, serial.SerialException) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
