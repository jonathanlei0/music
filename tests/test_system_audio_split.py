import contextlib
import io
import json
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from audio_led import parse_args
from system_audio_led import SystemLighting
from system_led_receiver import AnalysisBuffer
from test_virtualdj_led import packet


def message(now,seq=1,session='a'*32):
    return json.dumps(dict(version=1,session=session,seq=seq,due=now+1,
                           data=packet(),note=None)).encode()


class SplitTests(unittest.TestCase):
    def test_timestamps_lead_and_duplicates(self):
        buffer=AnalysisBuffer(.03)
        self.assertTrue(buffer.accept(message(100),100))
        self.assertFalse(buffer.accept(message(100),100))
        self.assertEqual(buffer.pop_due(100.96),[])
        self.assertAlmostEqual(buffer.pop_due(100.98)[0][0],100.97)

    def test_stale_malformed_and_restarted_service(self):
        buffer=AnalysisBuffer()
        self.assertFalse(buffer.accept(b'not json',100))
        self.assertFalse(buffer.accept(message(90),100))
        self.assertTrue(buffer.accept(message(100,100),100))
        self.assertTrue(buffer.accept(message(100,0,'b'*32),100))
        self.assertEqual(len(buffer.frames),1)
        self.assertEqual(buffer.pop_due(102),[]) # Don't replay missed beats.

    def test_audio_keeps_running_with_no_receiver_and_after_reconnect(self):
        # Exercise the real service loop/UDP publisher with a fake audio device.
        probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
        args=parse_args(['--audio-only','--analysis-port',str(port)])
        service=SystemLighting(args)
        callbacks=[]
        class FakeStream:
            latency=(0,.1)
            active=True
            def __init__(self,**kwargs):self.callback=kwargs['callback'];self.done=threading.Event()
            def __enter__(self):
                def pump():
                    timing=SimpleNamespace(outputBufferDacTime=1.1,currentTime=1)
                    while not self.done.is_set():
                        audio=np.zeros((1024,2),dtype=np.float32)
                        self.callback(audio,np.empty_like(audio),1024,timing,None)
                        callbacks.append(1)
                        self.done.wait(.02)
                self.worker=threading.Thread(target=pump);self.worker.start();return self
            def __exit__(self,*args):
                self.done.set();self.worker.join(timeout=1)
        errors=[]
        def run():
            try:service.run()
            except Exception as exc:errors.append(exc)
        with patch('system_audio_led.require_blackhole_system_output'), \
             patch('system_audio_led.find_audio_device',return_value=0), \
             patch('system_audio_led.find_audio_output_device',return_value=1), \
             patch('system_audio_led.sd.query_devices',return_value={'name':'test'}), \
             patch('system_audio_led.sd.Stream',FakeStream), \
             patch('system_audio_led.audio_service_lock',side_effect=contextlib.nullcontext), \
             contextlib.redirect_stdout(io.StringIO()):
            thread=threading.Thread(target=run);thread.start()
            try:
                time.sleep(.08) # No LED process at all.
                before=len(callbacks)
                for _ in range(2):
                    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver:
                        receiver.bind(('127.0.0.1',port));receiver.settimeout(1)
                        raw=receiver.recv(16384)
                        self.assertEqual(json.loads(raw)['version'],1)
                    time.sleep(.06) # Renderer closed; audio must continue.
                self.assertGreater(len(callbacks),before+4)
                self.assertTrue(thread.is_alive())
            finally:
                service.stop.set();thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])
