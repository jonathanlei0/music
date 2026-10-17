import unittest
from virtualdj_pixels import DropDetector, ShowPixels, BLACK
from test_virtualdj_led import packet


def snapshot(t, loud=False):
    data=packet(t*2)
    data['decks'][0].update(kick=.3 if loud else 0,
                            bass=.2 if loud else 0,instru=.01)
    return data


class DropTests(unittest.TestCase):
    def test_quiet_passage_then_percussion_return_triggers_once(self):
        detector=DropDetector()
        for i in range(41):
            self.assertFalse(detector.update(snapshot(i*.05),i*.05))
        self.assertTrue(detector.update(snapshot(2.05,True),2.05))
        for i in range(42,101):
            self.assertFalse(detector.update(snapshot(i*.05,True),i*.05))
        for i in range(101,161):
            self.assertFalse(detector.update(snapshot(i*.05),i*.05))
        self.assertFalse(detector.update(snapshot(8.05,True),8.05)) # cooldown

    def test_playback_start_and_steady_drums_are_not_drops(self):
        detector=DropDetector()
        for i in range(80):
            self.assertFalse(detector.update(snapshot(i*.05,True),i*.05))

    def test_seeks_switches_and_stale_restarts_disarm_detection(self):
        for mode in ('seek','switch','stale'):
            detector=DropDetector()
            for i in range(41):detector.update(snapshot(i*.05),i*.05)
            data=snapshot(2.05,True);now=2.05
            if mode=='seek':data['decks'][0]['beatpos']=50
            if mode=='switch':data['decks'][0]['id']=2
            if mode=='stale':now=4
            self.assertFalse(detector.update(data,now))

    def test_drop_show_is_wide_bounded_and_stale_black(self):
        effect=ShowPixels(30)
        effect.update(snapshot(0),0)
        effect.drop_at=0
        effect.update(snapshot(.25),.25)
        pixels=effect.render(.25)
        self.assertGreater(sum(any(pixels[i:i+3]) for i in range(0,900,3)),250)
        self.assertLessEqual(max(pixels),round(255*.3))
        self.assertEqual(effect.render(1),BLACK)

    def test_show_expires_after_three_seconds(self):
        effect=ShowPixels()
        effect.update(snapshot(0),0);effect.drop_at=0
        effect.update(snapshot(3.1),3.1)
        shown=effect.render(3.1)
        effect.drop_at=float('-inf')
        self.assertEqual(shown,effect.render(3.1))

    def test_loud_build_with_thin_bass_then_gradual_bass_return(self):
        detector=DropDetector()
        for i in range(61):
            now=i*.05;data=snapshot(now,True)
            data['decks'][0]['bass']=.02
            self.assertFalse(detector.update(data,now))
        triggers=[]
        for i in range(61,81):
            now=i*.05;data=snapshot(now,True)
            data['decks'][0]['bass']=min(.25,.02+(i-60)*.025)
            if detector.update(data,now):triggers.append(now)
        self.assertEqual(len(triggers),1)

    def test_sustained_energy_adds_motion_without_more_sparkles(self):
        quiet=ShowPixels();busy=ShowPixels()
        low=snapshot(0);high=snapshot(0,True)
        low['decks'][0]['instru']=high['decks'][0]['instru']=0
        for i in range(121):
            now=i*.05
            low['decks'][0]['beatpos']=high['decks'][0]['beatpos']=now*2
            quiet.update(low,now);busy.update(high,now)
            calm=quiet.render(now);active=busy.render(now)
        self.assertGreater(sum(active),sum(calm))
        self.assertGreater(busy.action_level,.7)
        self.assertEqual(busy.spawn_count,0)
        self.assertLessEqual(max(active),128)
