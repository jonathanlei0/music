import contextlib
import io
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from audio_led import SAMPLE_RATE,parse_args
from system_audio_led import SystemLighting
from system_audio_stems import SpectralStems
from virtualdj_pixels import EnergyShowPixels


class SystemAudioTests(unittest.TestCase):
    def test_delay_defaults_and_invalid_delays(self):
        self.assertEqual(parse_args([]).delay_ms,50)
        self.assertEqual(parse_args(["--delay-ms","50"]).delay_ms,50)
        for value in ('nan','inf','-1','0','49','5001'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                parse_args(['--delay-ms',value])

    def test_actual_callback_delays_impulse_exactly_one_second_and_schedules(self):
        lighting=SystemLighting(parse_args(['50','--delay-ms','1000']))
        # Use callback blocks of varying sizes to exercise delay across boundaries.
        frames=512;source=np.zeros((SAMPLE_RATE+frames*2,2),dtype=np.float32)
        source[0]=1;outputs=[]
        timing=SimpleNamespace(outputBufferDacTime=20.12,currentTime=20)
        with patch('system_audio_led.time.monotonic',return_value=100):
            for start in range(0,len(source),frames):
                block=source[start:start+frames];out=np.empty_like(block)
                lighting.callback(block,out,len(block),timing,None);outputs.append(out)
        joined=np.concatenate(outputs)
        self.assertEqual(np.flatnonzero(joined[:,0]).tolist(),[SAMPLE_RATE])
        captured,due,samples=lighting.input.get_nowait()
        self.assertEqual(captured,100)
        self.assertAlmostEqual(due,101.12) # Timestamp is audible time; LED receiver applies its lead.
        self.assertGreater(lighting.dropped,0)
        self.assertLessEqual(lighting.input.qsize(),32)

    def test_silence_estimates_zero_and_drives_idle_renderer(self):
        analyzer=SpectralStems();renderer=EnergyShowPixels(50)
        for i in range(5):
            now=i*1024/SAMPLE_RATE
            data,note=analyzer.process(np.zeros((1024,2),dtype=np.float32),now)
            renderer.update(data,now)
        self.assertEqual(data['decks'][0]['playing'],0)
        for name in ('bass','vocal','instru','kick','hihat'):
            self.assertEqual(data['decks'][0][name],0)
        renderer.render(0)
        self.assertGreater(sum(renderer.render(1)),0)

    def test_bass_and_center_side_estimates_and_renderer_compatibility(self):
        def signal(frequency,side=False):
            analyzer=SpectralStems();renderer=EnergyShowPixels(50)
            for i in range(25):
                now=i*1024/SAMPLE_RATE
                t=(np.arange(1024)+i*1024)/SAMPLE_RATE
                x=(.1*np.sin(2*np.pi*frequency*t)).astype(np.float32)
                audio=np.column_stack((x,-x if side else x))
                data,note=analyzer.process(audio,now)
                renderer.update(data,now)
            self.assertEqual(len(renderer.render(now)),900)
            return data['decks'][0]
        bass=signal(110);center=signal(440);side=signal(440,True)
        self.assertGreater(bass['bass'],bass['vocal']*4)
        self.assertGreater(center['vocal'],side['vocal'])
        self.assertGreater(side['instru'],center['instru'])
        self.assertGreater(side['level'],0)

    def test_worker_publishes_due_data_and_stops(self):
        lighting=SystemLighting(parse_args([]))
        import threading,time
        now=time.monotonic();due=now+1
        lighting.input.put((now,due,np.zeros((1024,2),dtype=np.float32)))
        worker=threading.Thread(target=lighting.analyze);worker.start()
        deadline=time.monotonic()+1
        while not lighting.schedule and time.monotonic()<deadline:time.sleep(.005)
        lighting.stop.set();worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(lighting.error)
        self.assertEqual(lighting.schedule[0][0],due)

    def test_slightly_late_analysis_still_reaches_lights(self):
        lighting=SystemLighting(parse_args(['--delay-ms','50']))
        fake_analyzer=SimpleNamespace(process=lambda audio,captured:(dict(decks=[]),None))
        now=100.0
        lighting.input.put((99.94,99.96,np.zeros((1024,2),dtype=np.float32)))
        # Stop after processing this one queued block.
        original_get=lighting.input.get
        def get(*args,**kwargs):
            item=original_get(*args,**kwargs)
            lighting.stop.set()
            return item
        with patch.object(lighting.input,'get',side_effect=get),              patch('system_audio_led.SpectralStems',return_value=fake_analyzer),              patch('system_audio_led.time.monotonic',return_value=now):
            lighting.analyze()
        self.assertIsNone(lighting.error)
        self.assertEqual(lighting.late,1)
        self.assertEqual(lighting.expired,0)
        self.assertAlmostEqual(lighting.schedule[0][0],100.04)

    def test_long_stale_analysis_is_still_discarded(self):
        lighting=SystemLighting(parse_args([]))
        fake_analyzer=SimpleNamespace(process=lambda audio,captured:(dict(decks=[]),None))
        lighting.input.put((98,99,np.zeros((1024,2),dtype=np.float32)))
        original_get=lighting.input.get
        def get(*args,**kwargs):
            item=original_get(*args,**kwargs);lighting.stop.set();return item
        with patch.object(lighting.input,'get',side_effect=get),              patch('system_audio_led.SpectralStems',return_value=fake_analyzer),              patch('system_audio_led.time.monotonic',return_value=100):
            lighting.analyze()
        self.assertEqual(lighting.expired,1)
        self.assertEqual(len(lighting.schedule),0)
