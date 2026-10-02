import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import queue

import numpy as np

import audio_led
from audio_led import AudioDelayLine, MusicalAnalyzer, massive_drop, midi_frequency, parse_args
from led_effects import layers_from_analysis


class LayersTests(unittest.TestCase):
    def test_callback_records_playback_time_without_modifying_audio(self):
        captured = queue.Queue()
        callback = audio_led.make_audio_callback(AudioDelayLine(0))
        samples = np.full((1024, 2), 0.125, dtype='float32')
        output = np.empty_like(samples)
        timing = SimpleNamespace(outputBufferDacTime=20.12, currentTime=20.0)
        with patch.object(audio_led, 'analysis_queue', captured), \
                patch.object(audio_led.time, 'monotonic', return_value=100.0):
            callback(samples, output, 1024, timing, None)
        timestamp, audible, mono = captured.get_nowait()
        self.assertEqual(timestamp, 100)
        self.assertAlmostEqual(audible, 105.12)
        np.testing.assert_array_equal(output, samples)
        np.testing.assert_array_equal(mono, samples[:, 0])

    def test_led_lead_validation_and_timestamp_fallback(self):
        self.assertEqual(parse_args([]).led_lead_ms, 200)
        self.assertTrue(parse_args(['--led-stats']).led_stats)
        self.assertEqual(parse_args(['50', '--led-lead-ms', '120']).led_lead_ms, 120)
        with patch.object(audio_led, 'visual_delay_seconds', 5.2):
            self.assertAlmostEqual(audio_led.playback_time(10, SimpleNamespace()), 15.2)
        for value in ('-1', '501', 'nan', 'inf'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(['--led-lead-ms', value])

    def test_foreground_pitch_tracks_a_simple_melody(self):
        analyzer = MusicalAnalyzer()
        for note in (60, 64, 67, 62, 72):
            for block in range(12):
                t = (np.arange(1024)+block*1024)/48000
                tone = (0.2*np.sin(2*np.pi*midi_frequency(note)*t)).astype('float32')
                analyzer._update_pitch(tone, 0.5)
            self.assertEqual(analyzer.note_midi, note)

    def test_only_large_contextual_drops_trigger(self):
        history = [-25.0] * 94
        bass_history = [0.4] * 94
        def check(db=-14, bass=0.98, onset=3, since=20, dbs=history):
            return massive_drop(True, db, bass, onset, 0.6, dbs, bass_history, since)
        self.assertTrue(check())
        self.assertFalse(check(db=-21))  # Ordinary loudness lift.
        self.assertFalse(check(bass=0.7))
        self.assertFalse(check(onset=1.2))
        self.assertFalse(check(since=5))
        self.assertFalse(check(dbs=[-90.0]*94))  # Playback starting.
        self.assertFalse(check(dbs=[-25.0]*20))  # Insufficient context.

    def test_packet_preserves_background_and_foreground(self):
        frame = dict(hue=0x1234, level=71, treble=191, bass=82,
                     onset=0.5, bpm=120, mood=30)
        packet = layers_from_analysis(frame, event=3, phrase=1).packet()
        self.assertEqual(len(packet), 19)
        self.assertEqual(packet[:2], b'\xa5\x5a')
        data = packet[2:-1]
        self.assertEqual(data[3:9], bytes((71, 71, 191, 191, 0x34, 0x12)))
        self.assertEqual(data[9:12], bytes((3, 1, 120)))
        checksum = 0
        for value in data:
            checksum = ((checksum << 1) | (checksum >> 7)) & 255
            checksum ^= value
        self.assertEqual(packet[-1], checksum)

    def test_darkness_options(self):
        self.assertEqual(parse_args([]).darkness, 65)
        args = parse_args(['50', '--darkness', '80'])
        self.assertEqual((args.brightness, args.darkness), (50, 80))
        for value in ('-1', '101', 'nan'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(['--darkness', value])

    def test_sparkles_follow_treble_not_just_volume(self):
        t = np.arange(1024) / 48000
        low = MusicalAnalyzer().process((0.1*np.sin(2*np.pi*120*t)).astype('float32'), 0)
        high = MusicalAnalyzer().process((0.1*np.sin(2*np.pi*6000*t)).astype('float32'), 0)
        self.assertGreater(high['treble'], low['treble'] + 100)
        silence = MusicalAnalyzer().process(np.zeros(1024, dtype='float32'), 0)
        self.assertEqual(silence['treble'], 0)

    def test_five_second_audio_delay_is_unchanged(self):
        delay = AudioDelayLine(5000)
        source = np.random.default_rng(42).uniform(-1, 1, (245760, 2)).astype('float32')
        result = np.empty_like(source)
        for i in range(0, len(source), 1024):
            delay.process(source[i:i+1024], result[i:i+1024])
        np.testing.assert_array_equal(result[:240000], 0)
        np.testing.assert_array_equal(result[240000:], source[:-240000])


if __name__ == '__main__':
    unittest.main()
