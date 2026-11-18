import unittest
from virtualdj_pixels import RomanticPixels, EnergyShowPixels, BLACK
from test_virtualdj_led import packet


class RomanticTests(unittest.TestCase):
    def test_romantic_timing_is_slower_but_still_follows_tempo(self):
        romantic=RomanticPixels();normal=EnergyShowPixels()
        self.assertGreater(romantic.RELEASE_SECONDS,normal.RELEASE_SECONDS)
        self.assertGreater(romantic.music.timing()['grow'],normal.music.timing()['grow'])
        romantic.music.bpm=70;slow=romantic.music.timing()['color_fade']
        romantic.music.bpm=145
        self.assertLess(romantic.music.timing()['color_fade'],slow)

    def test_rendering_is_bounded_retains_orange_marker_and_zero_brightness(self):
        effect=RomanticPixels(50)
        effect.render(0)
        pixels=effect.render(1)
        self.assertEqual(pixels[:12],bytes((13,2,0))*4)
        self.assertGreater(sum(pixels[12:]),0)
        self.assertLessEqual(max(pixels),128)
        self.assertEqual(RomanticPixels(0).render(0),BLACK)

    def test_notes_still_change_colors_without_a_cold_blue_wash(self):
        effect=RomanticPixels(50);data=packet()
        data['decks'][0].update(kick=0,bass=0,vocal=.25,instru=.12)
        for i in range(100):
            now=i*.05;data['decks'][0]['beatpos']=now*2
            effect.update(data,now);effect.music.set_note(60,now);first=effect.render(now)
        for i in range(100,130):
            now=i*.05;data['decks'][0]['beatpos']=now*2
            effect.update(data,now);effect.music.set_note(67,now);second=effect.render(now)
        self.assertNotEqual(first,second)
        self.assertLessEqual(len(effect.particles),12)
