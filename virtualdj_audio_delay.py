#!/usr/bin/env python3
"""Relay VirtualDJ's BlackHole output with an adjustable audio-only delay."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import time

if __name__ == '__main__' and importlib.util.find_spec('sounddevice') is None:
    python = Path(__file__).resolve().parent / 'venv/bin/python'
    if python.is_file() and Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), __file__, *sys.argv[1:]])

import sounddevice as sd
from audio_led import AudioDelayLine, SAMPLE_RATE, find_audio_output_device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delay-ms', type=float, default=50)
    parser.add_argument('--output-device', default='AirPods', help='output device name substring')
    args = parser.parse_args()
    if not 0 <= args.delay_ms <= 2000:
        parser.error('--delay-ms must be between 0 and 2000')
    devices = sd.query_devices()
    source = next((i for i, d in enumerate(devices)
                   if d['name'] == 'BlackHole 2ch' and d['max_input_channels'] >= 2), None)
    if source is None:
        raise RuntimeError('BlackHole 2ch input was not found.')
    output = find_audio_output_device(args.output_device)
    delay = AudioDelayLine(args.delay_ms)
    stats = {'status': ''}

    def callback(indata, outdata, frames, timing, status):
        if status:
            stats['status'] = str(status)
        delay.process(indata, outdata)

    with sd.Stream(device=(source, output), channels=(2, 2), samplerate=SAMPLE_RATE,
                   blocksize=256, dtype='float32', latency='low', callback=callback) as stream:
        print(f"BlackHole 2ch -> {devices[output]['name']}", flush=True)
        print(f'Added sample delay: {args.delay_ms:g} ms. '
              f'Reported stream latency: input {stream.latency[0]*1000:.1f} ms, '
              f'output {stream.latency[1]*1000:.1f} ms.', flush=True)
        print('Route VirtualDJ Master ONLY to BlackHole 2ch, channels 1 & 2. '
              'Keep virtualdj_led.py running separately. Ctrl+C stops audio relay.', flush=True)
        while stream.active:
            time.sleep(0.25)
            if stats['status']:
                print(f"Audio stream: {stats['status']}", flush=True)
                stats['status'] = ''
        raise RuntimeError('Audio stream stopped unexpectedly.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, sd.PortAudioError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
