#!/usr/bin/env python3
"""Continuous system-audio playback/analysis, independent of LED rendering."""
import importlib.util
import os
from pathlib import Path
import sys

if importlib.util.find_spec('serial') is None:
    python=Path(__file__).resolve().parent/'venv/bin/python'
    if python.is_file():
        os.execv(str(python),[str(python),__file__,*sys.argv[1:]])

from audio_led import parse_args
from system_audio_led import SystemLighting
import sounddevice as sd

if __name__ == '__main__':
    try:
        args=parse_args(['--audio-only',*sys.argv[1:]])
        SystemLighting(args).run()
    except (OSError,RuntimeError,sd.PortAudioError) as exc:
        print(f'Error: {exc}',file=sys.stderr)
        sys.exit(1)
