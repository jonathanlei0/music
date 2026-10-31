import unittest
from unittest.mock import patch
import numpy as np
from audio_led import SAMPLE_RATE, PITCH_WINDOW_SIZE
from virtualdj_melody import MelodyReading, MelodyInput, NoteTracker
from virtualdj_pixels import MelodyPixels, StemPixels
from test_virtualdj_led import packet


class MelodyTests(unittest.TestCase):
    def test_synthetic_notes_need_confirmation_and_silence_has_no_note(self):
        tracker=NoteTracker()
        t=np.arange(PITCH_WINDOW_SIZE)/SAMPLE_RATE
        for note in (60,69,76):
            frequency=440*2**((note-69)/12)
            samples=(.12*np.sin(2*np.pi*frequency*t)).astype(np.float32)
            first=tracker.analyze(samples,1)
            second=tracker.analyze(samples,1.05)
            self.assertIsNone(first.note)
            self.assertEqual(second.note,note)
            self.assertGreater(second.strength,0)
        self.assertIsNone(tracker.analyze(np.zeros(PITCH_WINDOW_SIZE),2).note)

    def test_uncertain_pitch_does_not_publish_a_note(self):
        with patch('virtualdj_melody.detect_harmonic_note',return_value=(69,.5)):
            tracker=NoteTracker()
            for _ in range(3):
                self.assertIsNone(tracker.analyze(np.ones(PITCH_WINDOW_SIZE)*.1,0).note)

    def test_input_callback_four_arguments_and_bounded_queue(self):
        capture=MelodyInput()
        audio=np.ones((1024,2),dtype=np.float32)
        for _ in range(20):capture.callback(audio,1024,None,None)
        self.assertEqual(len(capture.blocks),8)
        audio[:]=0
        self.assertEqual(capture.blocks[-1][1][0,0],1)

    def test_sparkles_move_with_notes_and_fade_without_stale_audio_spawning(self):
        effect=MelodyPixels();base=StemPixels();data=packet()
        effect.update(data,0);base.update(data,0)
        effect.instrument_variation=.12
        effect.instrument_target=1
        effect.set_melody(MelodyReading(69,3,.8,0),0)
        self.assertEqual(effect.spawn_count,1)
        self.assertNotEqual(effect.render(.2),base.render(.2))
        effect.update(data,1.2)
        effect.instrument_variation=.12
        effect.instrument_target=1
        effect.set_melody(MelodyReading(67,3,.8,1.2),1.2)
        self.assertEqual(effect.spawn_count,2)
        self.assertEqual(effect.particles[-1][2],0)
        effect.set_melody(MelodyReading(72,3,1,0),1.3)
        self.assertEqual(effect.spawn_count,2)
        effect.render(1.7)
        self.assertEqual(effect.particles,[])

    def test_no_sparkles_from_silence_or_muted_decks(self):
        effect=MelodyPixels();effect.update(packet(volume=0),0)
        effect.set_melody(MelodyReading(69,3,1,0),0)
        self.assertEqual(effect.spawn_count,0)

    def test_particles_stay_stationary_between_beat_hops(self):
        effect=MelodyPixels();effect.update(packet(),0)
        effect.instrument_variation=.12
        effect.instrument_target=1
        effect.set_melody(MelodyReading(69,3,.8,0),0)
        particle=effect.particles[0]
        positions=[effect.particle_position(particle,i*.01) for i in range(101)]
        steps=[b-a for a,b in zip(positions,positions[1:])]
        self.assertTrue(all(step == 0 for step in steps))

    def test_energetic_sections_spawn_clusters_and_cap_population(self):
        quiet=MelodyPixels();busy=MelodyPixels();data=packet()
        quiet.update(data,0);busy.update(data,0)
        busy.instrument_variation=1
        busy.instrument_target=1
        reading=MelodyReading(69,3,1,0)
        quiet.set_melody(reading,0);busy.set_melody(reading,0)
        self.assertGreater(len(busy.particles),len(quiet.particles))
        for i in range(1,40):
            now=i*.15
            busy.update(data,now)
            busy.instrument_variation=1
            busy.instrument_target=1
            busy.set_melody(MelodyReading(69+i%5,3,1,now),now)
            pixels=busy.render(now)
            self.assertLessEqual(len(busy.particles),12)
            self.assertEqual(len(pixels),900)
        self.assertGreater(len(busy.particles),3)

    def test_quiet_section_stays_sparkle_free_despite_changing_notes(self):
        effect=MelodyPixels();data=packet()
        for i in range(100):
            now=i*.05
            effect.update(data,now)
            effect.instrument_variation=.02
            effect.set_melody(MelodyReading(60+i%12,3,1,now),now)
        self.assertEqual(effect.spawn_count,0)

    def test_moderate_held_note_does_not_spawn_continuously(self):
        effect=MelodyPixels();data=packet()
        for i in range(21):
            now=i*.05
            effect.update(data,now)
            effect.instrument_variation=.12
            effect.instrument_target=1
            effect.set_melody(MelodyReading(69,3,1,now),now)
        self.assertEqual(effect.spawn_count,1)
        self.assertLess(effect.particles[0][4],.5)

    def test_section_colors_follow_notes_even_when_sparkles_are_quiet(self):
        effect=MelodyPixels();effect.update(packet(),0)
        before=effect.music.colors(0)
        effect.set_melody(MelodyReading(60,3,1,0),0)
        self.assertEqual(effect.spawn_count,0)
        self.assertEqual(effect.music.colors(0),before)
        first=effect.music.colors(.8)
        self.assertNotEqual(first,before)
        effect.update(packet(),.8)
        effect.set_melody(MelodyReading(64,3,1,.8),.8)
        self.assertEqual(effect.music.colors(.8),first)
        self.assertNotEqual(effect.music.colors(1.3),first)

    def test_octaves_do_not_restart_transition_and_uncertainty_holds_color(self):
        effect=MelodyPixels();effect.update(packet(),0)
        effect.set_melody(MelodyReading(60,3,1,0),0)
        effect.set_melody(MelodyReading(72,3,1,.1),.1)
        self.assertEqual(effect.music.note_changed_at,0)
        color=effect.music.colors(.5)
        effect.set_melody(MelodyReading(at=.3),.3)
        self.assertEqual(effect.music.colors(.5),color)
        effect.set_melody(MelodyReading(67,3,1,0),.5)
        self.assertEqual(effect.music.note_class,0)

    def test_steady_instruments_and_busy_drums_do_not_create_sparkles(self):
        effect=MelodyPixels();data=packet()
        data['decks'][0].update(instru=.12,kick=1,bass=1,hihat=1)
        for i in range(120):
            now=i*.05
            effect.update(data,now)
            effect.set_melody(MelodyReading(60+i%12,3,1,now),now)
        self.assertEqual(effect.spawn_count,0)

    def test_instrument_variation_drives_activity_and_slower_motion(self):
        effect=MelodyPixels();data=packet()
        for i in range(100):
            now=i*.05
            data['decks'][0].update(instru=.12 if i%12<6 else .006,kick=0,bass=0,hihat=0)
            effect.update(data,now)
            effect.set_melody(MelodyReading(69,3,1,now),now)
        self.assertGreater(effect.spawn_count,0)
        self.assertLess(abs(effect.particles[-1][2]),18)
        self.assertLess(effect.particles[-1][7],1)

    def test_repeated_pitch_class_confirms_despite_intervening_chord_tones(self):
        tracker=NoteTracker()
        samples=np.ones(PITCH_WINDOW_SIZE)*.1
        with patch('virtualdj_melody.detect_harmonic_note',side_effect=[(60,1.7),(64,1.7),(72,1.7),(67,1.7),(60,1.7)]):
            readings=[tracker.analyze(samples,i*.045) for i in range(5)]
        self.assertIsNone(readings[0].note)
        self.assertEqual(readings[-1].note,60)

    def test_inconsistent_chord_tones_stay_unconfirmed(self):
        tracker=NoteTracker()
        with patch('virtualdj_melody.detect_harmonic_note',side_effect=[(60,1.7),(64,1.7),(67,1.7),(62,1.7),(65,1.7)]):
            for i in range(5):
                self.assertIsNone(tracker.analyze(np.ones(PITCH_WINDOW_SIZE)*.1,i*.045).note)

    def test_strong_beats_hop_existing_sparkles_without_spawning_more(self):
        effect=MelodyPixels();data=packet()
        data['decks'][0]['kick']=0
        effect.update(data,0)
        effect.instrument_variation=1;effect.instrument_target=1
        effect.set_melody(MelodyReading(69,3,1,0),0)
        particles=list(effect.particles)
        data['decks'][0]['kick']=.2
        effect.update(data,.1)
        self.assertEqual(len(effect.particles),len(particles))
        for index,particle in enumerate(particles):
            self.assertEqual(effect.hop_offset(particle,.1),0)
            offset=effect.hop_offset(particle,.15)
            self.assertAlmostEqual(abs(offset),3+index%3)
        self.assertLess(effect.hop_offset(particles[0],.15)*effect.hop_offset(particles[1],.15),0)
        hops=dict(effect.beat_hops)
        effect.update(data,.12) # held meter isn't another strong beat
        self.assertEqual(effect.beat_hops,hops)
        effect.render(.6)
        self.assertEqual(effect.beat_hops,{})

    def test_dim_grid_fallback_does_not_hop_sparkles(self):
        effect=MelodyPixels();data=packet(1.9)
        data['decks'][0]['kick']=0
        effect.update(data,0)
        effect.instrument_variation=1;effect.instrument_target=1
        effect.set_melody(MelodyReading(69,3,1,0),0)
        data['decks'][0]['beatpos']=2.01
        effect.update(data,.05)
        self.assertEqual(effect.pulse_peak,.18)
        self.assertEqual(effect.beat_hops,{})

    def test_vocal_pitch_moves_orange_position_low_to_high_smoothly(self):
        effect=MelodyPixels();data=packet()
        data['decks'][0]['vocal']=.25
        effect.update(data,0)
        effect.set_melody(MelodyReading(52,3,1,0),0)
        self.assertEqual(effect.vocal_beat_position(0),120)
        self.assertEqual(effect.vocal_beat_position(1),24)
        effect.update(data,1.1)
        effect.set_melody(MelodyReading(88,3,1,1.1),1.1)
        self.assertEqual(effect.vocal_beat_position(1.1),24)
        self.assertEqual(effect.vocal_beat_position(2.1),216)
        effect.set_melody(MelodyReading(88,3,1,1.2),1.2)
        self.assertEqual(effect.note_position_changed,1.1)

    def test_instrument_only_notes_do_not_move_orange(self):
        effect=MelodyPixels();data=packet()
        data['decks'][0]['vocal']=0
        effect.update(data,0)
        effect.set_melody(MelodyReading(88,3,1,0),0)
        self.assertEqual(effect.beat_start,120)
        self.assertEqual(effect.vocal_position_at,float('-inf'))

    def test_section_color_changes_are_limited_to_one_per_six_hundred_ms(self):
        effect=MelodyPixels();data=packet();effect.update(data,0)
        effect.set_melody(MelodyReading(60,3,1,0),0)
        effect.set_melody(MelodyReading(64,3,1,.2),.2)
        self.assertEqual(effect.music.note_class,0)
        effect.update(data,.7)
        effect.set_melody(MelodyReading(64,3,1,.7),.7)
        self.assertEqual(effect.music.note_class,4)
