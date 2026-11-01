"""Restartable LED renderer: receives scheduled analysis, never opens audio."""
from collections import deque
import json
import math
import socket
import time
import serial
from audio_led import find_arduino_port
from virtualdj_melody import MelodyReading
from virtualdj_pixels import EnergyShowPixels, PixelLink, BLACK


class AnalysisBuffer:
    def __init__(self, lead_seconds=0):
        self.lead_seconds=lead_seconds
        self.frames=deque(maxlen=256)
        self.session=None
        self.sequence=-1
        self.received=0
        self.accepted=0
        self.rejected=0
        self.expired=0

    def accept(self, raw, now):
        self.received += 1
        accepted = self._accept(raw,now)
        if accepted:self.accepted += 1
        else:self.rejected += 1
        return accepted

    def _accept(self, raw, now):
        try:
            packet=json.loads(raw)
            due=packet['due'];seq=packet['seq'];session=packet['session']
            if packet.get('clock')=='unix':
                if not isinstance(due,(int,float)) or not math.isfinite(due):return False
                due=now+(due-time.time())
            elif packet.get('clock','monotonic')!='monotonic':
                return False
            if (packet['version']!=1 or not isinstance(due,(int,float)) or not math.isfinite(due)
                    or not now-.15 <= due <= now+6 or not isinstance(seq,int)
                    or not isinstance(session,str) or len(session)!=32):return False
            due -= self.lead_seconds
            data=packet['data']
            if not isinstance(data,dict) or not isinstance(data.get('decks'),list) or len(data['decks'])!=1:return False
            deck=data['decks'][0]
            for key in ('playing','volume','level','bpm','beatpos','vocal','instru','bass','kick','hihat'):
                if not isinstance(deck.get(key),(int,float)) or not math.isfinite(deck[key]):return False
            if deck.get('id')!=1 or deck.get('valid') is not True:return False
            note=packet.get('note')
            if note is not None:
                if note.get('note') is not None and (not isinstance(note['note'],int) or not 0<=note['note']<=127):return False
                for key in ('at','confidence','strength','rms'):
                    if not isinstance(note.get(key),(int,float)) or not math.isfinite(note[key]):return False
            if session!=self.session:
                self.frames.clear();self.sequence=-1;self.session=session
            if seq<=self.sequence:return False
            if self.frames and due<self.frames[-1][0]:return False
            self.sequence=seq
            self.frames.append((due,data,note))
            return True
        except (ValueError,TypeError,KeyError,AttributeError):
            return False

    def pop_due(self, now):
        result=[]
        while self.frames and self.frames[0][0]<=now:
            frame=self.frames.popleft()
            if now-frame[0]<=.15:result.append(frame)
            else:self.expired += 1
        return result


def run(args):
    buffer=AnalysisBuffer(args.led_lead_ms/1000)
    ser=None;link=None
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver:
        receiver.bind(('127.0.0.1',args.analysis_port))
        receiver.setblocking(False)
        try:
            print('Searching for Arduino...',flush=True)
            port=args.serial_port or find_arduino_port()
            if not port:raise RuntimeError('Arduino not found.')
            print(f'Opening {port} at 500000 baud...',flush=True)
            ser=serial.Serial(port,500000,timeout=.03,write_timeout=.2)
            print('Waiting for Arduino reset...',flush=True)
            time.sleep(2);ser.reset_input_buffer()
            print('Checking RGB firmware acknowledgement...',flush=True)
            link=PixelLink(ser);link.send(BLACK)
            if getattr(args,'debug_startup',False):
                import faulthandler
                faulthandler.cancel_dump_traceback_later()
            # Discard startup datagrams; rebuild timing from fresh future frames.
            for _ in range(512):
                try:receiver.recv(16384)
                except BlockingIOError:break
            renderer=EnergyShowPixels(args.brightness)
            reading=MelodyReading()
            next_frame=last_print=0
            frames=0;stats_at=time.monotonic()
            last_print=stats_at
            print('LED-only renderer. Start audio_playback.py in another terminal. '
                  'Restarting this process does not stop music.',flush=True)
            while True:
                now=time.monotonic()
                for _ in range(128):
                    try:raw=receiver.recv(16384)
                    except BlockingIOError:break
                    buffer.accept(raw,now)
                for due,data,note in buffer.pop_due(now):
                    renderer.update(data,due)
                    if note is not None:
                        reading=MelodyReading(note['note'],note['confidence'],note['strength'],due,note['rms'])
                    renderer.set_melody(reading,due)
                if now>=next_frame:
                    link.send(renderer.render(now));frames+=1
                    next_frame=max(next_frame+1/30,time.monotonic())
                else:time.sleep(.002)
                if now-last_print>2:
                    fresh=renderer.last_update is not None and now-renderer.last_update<=.3
                    state='Receiving audio analysis' if fresh else 'Waiting for audio service; idle gradient'
                    print(f'{state} | packets={buffer.received} accepted={buffer.accepted} rejected={buffer.rejected} expired={buffer.expired} | kicks={renderer.kick_count} drops={renderer.drop_count}',flush=True)
                    if args.led_stats:
                        print(f'RGB sends: {frames/max(.001,now-stats_at):.1f} FPS',flush=True)
                        frames=0;stats_at=now
                    last_print=now
        except KeyboardInterrupt:
            pass
        finally:
            if ser:
                try:
                    if link:link.send(BLACK)
                except (OSError,RuntimeError,serial.SerialException):pass
                ser.close()
            print('LEDs stopped. Audio service is unaffected.',flush=True)
