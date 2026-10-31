import unittest
from virtualdj_pixels import EnergyShowPixels
from test_virtualdj_led import packet


class CoverageTests(unittest.TestCase):
    def settle(self, bass, vocal, instrument):
        effect=EnergyShowPixels();data=packet()
        for i in range(400):
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
            self.assertEqual(len(effect.render(19.95)),900)

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
        for i in range(400):effect.update(data,20+i*.05)
        self.assertEqual(effect.section_shares,[0,0,0])

    def test_first_four_leds_are_steady_five_percent_orange(self):
        effect=EnergyShowPixels(50)
        self.assertEqual(effect.render(0)[:12],bytes((13,2,0))*4)
        self.assertEqual(effect.render(1)[:12],bytes((13,2,0))*4)
        effect.update(packet(),1.1)
        self.assertEqual(effect.render(1.1)[:12],bytes((13,2,0))*4)

    def test_orange_marker_respects_zero_and_lower_user_brightness(self):
        self.assertEqual(EnergyShowPixels(0).render(0),bytes(900))
        self.assertEqual(EnergyShowPixels(2).render(0)[:12],bytes((5,1,0))*4)

    def test_size_targets_wait_between_updates(self):
        effect=EnergyShowPixels();data=packet()
        effect.update(data,0);original=list(effect.coverage_targets)
        data['decks'][0].update(bass=1,vocal=0,instru=0)
        effect.update(data,.1)
        self.assertEqual(effect.coverage_targets,original)
        effect.update(data,.4)
        self.assertNotEqual(effect.coverage_targets,original)

    def test_fast_song_updates_sizes_and_colors_sooner_than_slow_song(self):
        slow=EnergyShowPixels();fast=EnergyShowPixels()
        low=packet();high=packet()
        low['decks'][0]['bpm']=70;high['decks'][0]['bpm']=145
        slow.update(low,0);fast.update(high,0)
        slow.music.set_note(60,0);fast.music.set_note(60,0)
        slow.music.set_note(64,.25);fast.music.set_note(64,.25)
        self.assertEqual(slow.music.note_class,0)
        self.assertEqual(fast.music.note_class,4)
        before_slow=list(slow.coverage_targets)
        before_fast=list(fast.coverage_targets)
        low['decks'][0].update(bass=1,vocal=0,instru=0)
        high['decks'][0].update(bass=1,vocal=0,instru=0)
        slow.update(low,.2);fast.update(high,.2)
        self.assertEqual(slow.coverage_targets,before_slow)
        self.assertNotEqual(fast.coverage_targets,before_fast)
        self.assertLess(fast.music.timing()['grow'],slow.music.timing()['grow'])

    def test_color_fade_duration_stays_fixed_when_tempo_changes_mid_fade(self):
        effect=EnergyShowPixels();data=packet()
        data['decks'][0]['bpm']=70;effect.update(data,0)
        effect.music.set_note(60,0)
        before=effect.music.colors(.1)
        data['decks'][0]['bpm']=145;effect.update(data,.1)
        self.assertEqual(effect.music.colors(.1),before)
