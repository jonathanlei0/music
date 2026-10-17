import unittest
from unittest.mock import patch
from virtualdj_pixels import IdleShowPixels, BLACK
from test_virtualdj_led import packet


class IdleTests(unittest.TestCase):
    def test_no_signal_has_dim_gradient_after_fade(self):
        effect=IdleShowPixels(50)
        self.assertEqual(effect.render(0),BLACK)
        pixels=effect.render(1)
        self.assertGreater(sum(pixels),0)
        self.assertNotEqual(pixels[:3],pixels[-3:])
        self.assertLessEqual(max(pixels),11)
        self.assertEqual(len(pixels),900)
        self.assertEqual(effect.render(2),pixels)

    def test_palette_changes_only_when_entering_new_idle_period(self):
        effect=IdleShowPixels()
        with patch('random.choice',side_effect=lambda choices:choices[0]) as choice:
            effect.render(0);effect.render(1)
            first=effect.idle_pair
            self.assertEqual(choice.call_count,1)
            effect.update(packet(),2);effect.render(2)
            self.assertFalse(effect.idle)
            effect.render(2.4)
            self.assertFalse(effect.idle)
            effect.render(3.1)
            self.assertTrue(effect.idle)
            self.assertNotEqual(effect.idle_pair,first)
            self.assertEqual(choice.call_count,2)

    def test_resuming_music_crossfades_without_jump(self):
        effect=IdleShowPixels()
        effect.render(0);before=effect.render(1)
        effect.update(packet(),1.1)
        self.assertEqual(effect.render(1.1),before)
        effect.update(packet(),1.2)
        self.assertNotEqual(effect.render(1.2),before)

    def test_zero_user_brightness_keeps_idle_black(self):
        effect=IdleShowPixels(0)
        effect.render(0)
        self.assertEqual(effect.render(2),BLACK)
