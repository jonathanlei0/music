"""Independent audio playback/analysis service and LED-only entry-point dispatch."""
from collections import deque
import math
import queue
import threading
import time
import sounddevice as sd
from audio_led import (AudioDelayLine, SAMPLE_RATE, BLOCK_SIZE, find_audio_device,
                       find_audio_output_device,
                       require_blackhole_system_output)
from system_audio_stems import SpectralStems


class SystemLighting:
    def __init__(self, args):
        self.args = args
        self.delay = AudioDelayLine(args.delay_ms)
        self.input = queue.Queue(maxsize=32)
        self.schedule = deque(maxlen=256)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error = None
        self.status = ''
        self.dropped = self.late = self.expired = 0

    def callback(self, indata, outdata, frames, timing, status):
        if status:
            self.status = str(status)
        self.delay.process(indata,outdata)
        now = time.monotonic()
        latency = float(timing.outputBufferDacTime-timing.currentTime)
        if not math.isfinite(latency) or latency<0:
            latency = 0
        due = now+self.args.delay_ms/1000+latency
        item = (now,due,indata.copy())
        try:
            self.input.put_nowait(item)
        except queue.Full:
            self.dropped += 1
            try:self.input.get_nowait()
            except queue.Empty:pass
            try:self.input.put_nowait(item)
            except queue.Full:pass

    def analyze(self):
        analyzer = SpectralStems()
        try:
            while not self.stop.is_set():
                try:captured,due,audio = self.input.get(timeout=.1)
                except queue.Empty:continue
                data,note = analyzer.process(audio,captured)
                finished = time.monotonic()
                if finished>due:
                    self.late += 1
                    if finished-due > .25:
                        self.expired += 1
                        continue # A long stall must not replay obsolete beats.
                    # At short audio delays, prefer slightly late lights to no
                    # lights. This changes only LED scheduling, never playback.
                    due = finished+.04
                with self.lock:
                    self.schedule.append((due,data,note))
        except Exception as exc:
            self.error = exc
            self.stop.set()

    def run(self):
        import socket
        import json
        import uuid
        from dataclasses import asdict
        args = self.args
        if args.audio_source=='system':
            require_blackhole_system_output()
        input_device = find_audio_device()
        output_device = find_audio_output_device(args.output_device)
        worker = threading.Thread(target=self.analyze,daemon=True)
        worker.start()
        session = uuid.uuid4().hex
        sequence = 0
        try:
            with audio_service_lock(), socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as publisher:
                with sd.Stream(device=(input_device,output_device),channels=(2,2),
                               samplerate=SAMPLE_RATE,blocksize=BLOCK_SIZE,dtype='float32',
                               latency='low',callback=self.callback) as stream:
                    print(f"Audio service: {sd.query_devices(input_device)['name']} -> "
                          f"{sd.query_devices(output_device)['name']}",flush=True)
                    print(f'Added delay {args.delay_ms:g} ms; output latency '
                          f'{stream.latency[1]*1000:.1f} ms. No Arduino connection.',flush=True)
                    print('Music continues while the LED sender is stopped/restarted. Ctrl+C stops music.',flush=True)
                    last_print = 0
                    while not self.stop.is_set() and stream.active:
                        pending = []
                        with self.lock:
                            while self.schedule:pending.append(self.schedule.popleft())
                        for due,data,note in pending:
                            # Wall-clock timestamps cross process boundaries safely on older
                            # macOS Python; each receiver converts to its local monotonic clock.
                            wire_due = time.time()+(due-time.monotonic())
                            packet = dict(version=1,session=session,seq=sequence,due=wire_due,clock='unix',
                                          data=data,note=asdict(note) if note else None)
                            sequence += 1
                            try:
                                publisher.sendto(json.dumps(packet,allow_nan=False).encode(),
                                                 ('127.0.0.1',args.analysis_port))
                            except OSError:
                                pass # LED delivery must never interrupt music.
                        now = time.monotonic()
                        if now-last_print>3:
                            print(f'Audio running | analysis late={self.late} expired={self.expired} skipped={self.dropped}',flush=True)
                            if self.status:print(f'Audio: {self.status}',flush=True);self.status=''
                            last_print=now
                        self.stop.wait(.005)
                    if self.error:raise RuntimeError(f'Stem analysis failed: {self.error}')
                    if not stream.active and not self.stop.is_set():raise RuntimeError('Audio stream stopped.')
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set();worker.join(timeout=2)
            print('Audio service stopped.',flush=True)


def run(args):
    if args.audio_only:
        SystemLighting(args).run()
    else:
        from system_led_receiver import run as run_leds
        run_leds(args)


from contextlib import contextmanager


@contextmanager
def audio_service_lock():
    import fcntl
    import os
    import tempfile
    path = os.path.join(tempfile.gettempdir(),f'music-audio-service-{os.getuid()}.lock')
    with open(path,'a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('An audio playback service is already running; leave it running and start only audio_led.py.')
        yield

