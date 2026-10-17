import json
import unittest
from virtualdj_led import StemLights, validate_packet


def packet(beat=1.9, volume=1, playing=1):
    return dict(version=1, seq=1, sent_at=100, master=0.2,
                decks=[dict(id=1, valid=True, playing=playing, volume=volume,
                            level=0.2, bpm=120, beatpos=beat, vocal=0.1,
                            instru=0.1, bass=0.2, kick=0.1, hihat=0.1)])


class VirtualDJTests(unittest.TestCase):
    def test_stale_and_nonfinite_data_rejected(self):
        for data in [dict(packet(), sent_at=99), dict(packet(), master=float('nan')),
                     dict(packet(), decks=[]), dict(packet(), version=2)]:
            with self.assertRaises(ValueError):
                validate_packet(json.dumps(data).encode(), 100)
        self.assertEqual(validate_packet(json.dumps(packet()).encode(), 100)['version'], 1)

    def test_each_crossing_emits_only_one_event(self):
        lights = StemLights()
        self.assertEqual(lights.frame(packet(1.9), 0)['event'], 0)
        self.assertEqual(lights.frame(packet(2.01), .02)['event'], 1)
        self.assertEqual(lights.frame(packet(2.04), .04)['event'], 0)
        self.assertEqual(lights.frame(packet(30), .06)['event'], 0)  # seek

    def test_muted_and_stopped_decks_do_not_drive_lights(self):
        for data in (packet(volume=0), packet(playing=0)):
            frame = StemLights().frame(data, 0)
            self.assertEqual((frame['level'],frame['bass'],frame['event']), (0,0,0))

    def test_inaudible_deck_does_not_contribute_stems(self):
        data = packet()
        other = dict(data['decks'][0], id=2, bass=1, volume=0)
        data['decks'].append(other)
        actual = StemLights().frame(data, 0)
        expected = StemLights().frame(packet(), 0)
        self.assertEqual(actual['bass'], expected['bass'])

    def test_restarting_does_not_replay_old_beat(self):
        lights = StemLights()
        lights.frame(packet(1.9), 0)
        lights.frame(packet(playing=0), .02)
        self.assertEqual(lights.frame(packet(20.01), .04)['event'], 0)
