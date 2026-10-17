import unittest
from virtualdj_pixels import EnergyShowPixels
from test_virtualdj_led import packet


class CoverageTests(unittest.TestCase):
    def settle(self, bass, vocal, instrument):
        effect=EnergyShowPixels();data=packet()
        for i in range(200):
            now=i*.05
            data['decks'][0].update(bass=bass,vocal=vocal,instru=instrument,kick=0,beatpos=now*2)
            effect.update(data,now)
        effect.render(now)
        return effect

    def test_bass_dominance_allocates_much_more_orange_space(self):
        low=self.settle(.02,.20,.10)
        high=self.settle(.8,.03,.01)
        self.assertGreater(high.bass_width,200)
        self.assertLess(low.bass_width,30)
        self.assertGreater(sum(high.section_shares),.99)
        self.assertLessEqual(sum(high.section_shares),1)

    def test_each_stem_can_dominate_and_allocations_fit_strip(self):
        for bass,vocal,instrument,dominant in ((.5,0,0,0),(0,.3,0,1),(0,0,.2,2)):
            effect=self.settle(bass,vocal,instrument)
            self.assertGreater(effect.section_shares[dominant],.99)
            occupied=effect.bass_width
            occupied+=sum(stop-start for start,stop,center in effect.vocal_zones)
            occupied+=sum(stop-start for start,stop in effect.instrument_zones)
            self.assertEqual(occupied,300)
            self.assertEqual(len(effect.render(9.95)),900)

    def test_coverage_changes_smoothly_and_duplicate_decks_do_not_amplify(self):
        one=EnergyShowPixels();two=EnergyShowPixels();data=packet()
        one.update(data,0)
        data['decks'].append(dict(data['decks'][0],id=2))
        two.update(data,0)
        self.assertEqual(one.section_shares,two.section_shares)
        before=list(one.section_shares)
        for deck in data['decks']:deck.update(bass=1,vocal=0,instru=0)
        one.update(data,.02)
        self.assertLess(max(abs(a-b) for a,b in zip(before,one.section_shares)),.04)

    def test_weak_stem_loses_significantly_more_coverage(self):
        effect=self.settle(.4,.025,.012)
        self.assertGreater(effect.section_shares[0],.98)
        self.assertLess(effect.section_shares[1],.005)
        self.assertLess(effect.section_shares[2],.005)

    def test_all_weak_stems_leave_unused_space(self):
        effect=self.settle(.04,.025,.012)
        self.assertLess(sum(effect.section_shares),.05)
        occupied=effect.bass_width
        occupied+=sum(stop-start for start,stop,center in effect.vocal_zones)
        occupied+=sum(stop-start for start,stop in effect.instrument_zones)
        self.assertLess(occupied,15)

    def test_absent_stems_fade_to_zero_allocation(self):
        effect=self.settle(.4,0,0)
        data=packet();data['decks'][0].update(bass=0,kick=0,vocal=0,instru=0)
        for i in range(200):effect.update(data,10+i*.05)
        self.assertEqual(effect.section_shares,[0,0,0])
