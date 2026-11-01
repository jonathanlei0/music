"""Best-effort dominant-pitch tracking of BlackHole's full mix, off the audio callback."""
from collections import deque
from dataclasses import dataclass
from typing import Optional
import threading
import time

import numpy as np
import sounddevice as sd
from audio_led import (SAMPLE_RATE, PITCH_WINDOW_SIZE, PITCH_MIN_CONFIDENCE,
                       detect_harmonic_note)


@dataclass(frozen=True)
class MelodyReading:
    note: Optional[int] = None
    confidence: float = 0.0
    strength: float = 0.0
    at: float = 0.0
    rms: float = 0.0


class NoteTracker:
    def __init__(self):
        self.pending = None
        self.count = 0
        self.note = None
        self.recent = deque(maxlen=5)

    def analyze(self, samples, now):
        rms = float(np.sqrt(np.mean(samples*samples)))
        if rms < .003:
            self.pending, self.count = None, 0
            self.recent.clear()
            return MelodyReading(at=now, rms=rms)
        note, confidence = detect_harmonic_note(samples)
        if confidence < PITCH_MIN_CONFIDENCE:
            self.pending, self.count = None, 0
            self.recent.append(None)
            return MelodyReading(confidence=confidence, at=now, rms=rms)
        if note == self.pending:
            self.count += 1
        else:
            self.pending, self.count = note, 1
        self.recent.append((now,note))
        # Dense mixes can alternate between harmonics/chord tones. Three matching
        # pitch classes in the last five estimates also establish a stable note.
        support = sum(item is not None and now-item[0] <= .3 and item[1]%12 == note%12
                      for item in self.recent)
        if self.count >= 2 or support >= 3:
            self.note = note
        # No unconfirmed note gets published as the new melody.
        if note != self.note:
            return MelodyReading(confidence=confidence, at=now, rms=rms)
        strength = min(1.0, rms/.08)*min(1.0, confidence/3)
        return MelodyReading(note, confidence, strength, now, rms)


class MelodyInput:
    def __init__(self, device_name='BlackHole 2ch'):
        self.device_name = device_name
        self.blocks = deque(maxlen=8)
        self.reading = MelodyReading()
        self.status = ''
        self.stop_event = threading.Event()
        self.stream = None
        self.thread = None

    def start(self):
        devices = sd.query_devices()
        matches = [i for i,d in enumerate(devices) if self.device_name.lower() in d['name'].lower()
                   and d['max_input_channels'] >= 2]
        if not matches:
            raise RuntimeError(f'No stereo audio input matching {self.device_name!r}.')
        self.stream = sd.InputStream(device=matches[0], channels=2, samplerate=SAMPLE_RATE,
                                     blocksize=1024, dtype='float32', latency='low',
                                     callback=self.callback)
        try:
            self.stream.start()
            self.thread = threading.Thread(target=self.worker, daemon=True)
            self.thread.start()
        except Exception:
            self.stream.close()
            raise
        return self

    def callback(self, indata, frames, timing, status):
        if status:
            self.status = str(status)
        # Only copy audio here. FFT and pitch estimation happen in the worker.
        self.blocks.append((time.monotonic(), indata.copy()))

    def worker(self):
        tracker = NoteTracker()
        window = np.zeros(PITCH_WINDOW_SIZE, dtype=np.float32)
        filled = blocks = 0
        previous = None
        try:
            while not self.stop_event.is_set():
                try:
                    captured, audio = self.blocks.popleft()
                except IndexError:
                    self.stop_event.wait(.005)
                    continue
                if previous is not None and captured-previous > .1:
                    filled = blocks = 0
                    tracker = NoteTracker()
                previous = captured
                # Avoid stereo phase cancellation: analyze the louder channel.
                channel = int(np.argmax(np.mean(audio*audio, axis=0)))
                mono = audio[:,channel]
                count = min(len(mono),len(window))
                window[:-count] = window[count:]
                window[-count:] = mono[-count:]
                filled = min(len(window),filled+count)
                blocks += 1
                if filled == len(window) and blocks >= 2:
                    blocks = 0
                    self.reading = tracker.analyze(window,captured)
        except Exception as exc:
            self.status = f'Melody analysis stopped: {exc}'
            self.reading = MelodyReading()

    def close(self):
        self.stop_event.set()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        if self.thread is not None:
            self.thread.join(timeout=1)
