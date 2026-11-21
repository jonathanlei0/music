import unittest
from unittest.mock import patch
from virtualdj_pixels import GameDayPixels, EnergyShowPixels, BLACK


class GameDayTests(unittest.TestCase):
    def test_palette_remaps_all_other_colors_and_retains_marker(self):
        frame=bytearray(BLACK);frame[:12]=bytes((13,2,0))*4
        for i in range(4,300):
            frame[i*3:i*3+3]=bytes(((0,255,255),(255,0,0),(255,120,0))[i%3])
        with patch.object(EnergyShowPixels,'render',return_value=bytes(frame)):
            pixels=GameDayPixels(50).render(0)
        self.assertEqual(pixels[:12],frame[:12])
        for i in range(4,300):
            r,g,b=pixels[i*3:i*3+3]
            self.assertFalse(g and b)
            self.assertGreater(r,0)
            self.assertLessEqual(max(r,g,b),128)

    def test_white_sparkles_are_occasional_and_jump_in_both_directions(self):
        effect=GameDayPixels(50)
        with patch.object(EnergyShowPixels,'render',return_value=BLACK):
            self.assertEqual(effect.render(0),BLACK)
            effect.render(3)
            pixels=effect.render(3.2)
            self.assertEqual(len(effect.white_sparkles),4)
            self.assertTrue(any(pixels[i] and pixels[i]==pixels[i+1]==pixels[i+2]
                                for i in range(12,900,3)))
            self.assertEqual(effect.render(6),BLACK)
        for direction in (-1,1):
            sparkle=(0,150,direction)
            self.assertEqual(effect.white_position(sparkle,.10),150+4*direction)
            self.assertEqual(effect.white_position(sparkle,.30),150+4*direction)
            self.assertEqual(effect.white_position(sparkle,.45),150+8*direction)

    def test_zero_brightness_remains_off_even_during_white_burst(self):
        effect=GameDayPixels(0);effect.render(0)
        self.assertEqual(effect.render(3.2),BLACK)
        self.assertEqual(effect.ATTACK_SECONDS,.080)
        self.assertEqual(effect.RELEASE_SECONDS,.360)
