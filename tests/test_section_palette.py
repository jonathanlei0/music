import unittest

from section_palette import SectionPalette
from led_effects import layers_from_analysis


def frame(value, event=0):
    return dict(level=value, bass=value, treble=value, mood=value, event=event,
                hue=123, onset=0, bpm=120)


class SectionPaletteTests(unittest.TestCase):
    def run_frames(self, planner, value, start, count):
        changes = []
        for i in range(count):
            change = planner.update(frame(value), start+i*0.1)
            if change is not None:
                changes.append(change)
        return changes

    def test_confirmation_returns_original_boundary_inside_lookahead(self):
        planner = SectionPalette()
        changes = self.run_frames(planner, 255, 0, 31)
        self.assertEqual(changes, [(0.0, 1, False)])
        self.assertLess(planner.CONFIRM_SECONDS, 5)

    def test_short_accent_does_not_change_palette(self):
        planner = SectionPalette()
        self.run_frames(planner, 128, 0, 10)
        self.assertEqual(self.run_frames(planner, 255, 1, 5), [])
        self.assertEqual(self.run_frames(planner, 128, 1.5, 40), [])
        self.assertEqual(planner.palette, 0)

    def test_hold_and_massive_drop_override(self):
        planner = SectionPalette()
        self.run_frames(planner, 255, 0, 31)
        self.assertEqual(self.run_frames(planner, 0, 3.1, 70), [])
        self.assertEqual(planner.update(frame(255, 3), 10.1), (10.1, 2, True))
        self.assertEqual(self.run_frames(planner, 0, 10.2, 100), [])

    def test_quiet_section_gets_breakdown_palette(self):
        planner = SectionPalette()
        self.assertEqual(self.run_frames(planner, 0, 0, 31), [(0.0, 3, False)])

    def test_packet_carries_palette_without_changing_note_hue(self):
        source = frame(150)
        source.update(palette=2, palette_fast=True)
        payload = layers_from_analysis(source).packet()[2:-1]
        self.assertEqual(payload[12], 0xAA)
        self.assertEqual(payload[7:9], bytes((123, 0)))


if __name__ == '__main__':
    unittest.main()
