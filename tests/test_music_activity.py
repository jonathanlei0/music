import unittest

from music_activity import ActivityDetector
from led_effects import layers_from_analysis


def simulate(bpm, beat_interval, level=220, db=-12):
    detector = ActivityDetector()
    scores = []
    for i in range(801):
        now = i/40
        event = bool(beat_interval and i % beat_interval == 0)
        source = dict(event=event, bpm=bpm, level=level, db=db)
        scores.append(detector.update(source, now)[0])
    return scores


class ActivityTests(unittest.TestCase):
    def test_sparse_stays_slow_even_when_loud_or_tempo_is_wrong(self):
        self.assertLess(simulate(180, None, 255)[-1], 12)
        self.assertLess(simulate(60, 80)[-1], 30)

    def test_fast_rhythm_is_more_active_than_slow_rhythm(self):
        slow = simulate(60, 40)[-1]
        fast = simulate(160, 15)[-1]
        self.assertGreater(fast, 180)
        self.assertGreater(fast, slow+100)

    def test_silence_and_no_abrupt_score_jumps(self):
        self.assertEqual(simulate(180, None, 0, -90)[-1], 0)
        scores = simulate(160, 15)
        self.assertLess(max(abs(a-b) for a, b in zip(scores, scores[1:])), 5)

    def test_packet_transmits_activity_independently_of_old_mood(self):
        source = dict(hue=0, level=100, bass=0, treble=0, onset=0,
                      bpm=90, mood=240, activity=3)
        self.assertEqual(layers_from_analysis(source).packet()[2+15], 3)

    def test_change_score_compares_sustained_recent_and_long_term_energy(self):
        detector = ActivityDetector()
        for i in range(400):
            _, change = detector.update(dict(event=0,bpm=90,level=40,db=-30),i/40)
        self.assertEqual(change, 0)
        for i in range(400,480):
            _, change = detector.update(dict(event=0,bpm=90,level=220,db=-10),i/40)
        self.assertGreater(change, 100)


if __name__ == '__main__':
    unittest.main()
