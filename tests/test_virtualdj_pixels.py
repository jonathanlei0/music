import binascii
import struct
import unittest
from virtualdj_pixels import BLACK, KickPixels, VocalKickPixels, StemPixels, PixelLink, frame_packet
from test_virtualdj_led import packet


class PixelTests(unittest.TestCase):
    def test_packet_roundtrip_and_checksum(self):
        pixels = bytes(range(256))*3 + bytes(132)
        raw = frame_packet(pixels, 65535)
        self.assertEqual(raw[:8], b'RGB1' + struct.pack('<HH',65535,900))
        self.assertEqual(raw[8:-2], pixels)
        self.assertEqual(struct.unpack('<H',raw[-2:])[0], binascii.crc_hqx(raw[4:-2],0xffff))
        with self.assertRaises(ValueError):
            frame_packet(b'bad',0)

    def test_kick_held_level_does_not_retrigger_and_stale_is_black(self):
        effect = KickPixels(50)
        data = packet()
        effect.update(data, 0)
        pixels=effect.render(.01)
        self.assertEqual(pixels[:360], bytes(360))
        self.assertEqual(sum(any(pixels[i:i+3]) for i in range(0,900,3)),60)
        for i in range(1,31):
            effect.update(data, i*.02)
        self.assertEqual(effect.render(.6),BLACK)
        data['decks'][0]['kick']=0
        effect.update(data,.65)
        data['decks'][0]['kick']=.2
        effect.update(data,.9)
        self.assertNotEqual(effect.render(.91),BLACK)
        self.assertEqual(effect.render(1.3),BLACK)

    def test_envelope_rises_and_falls_smoothly(self):
        effect = KickPixels()
        effect.update(packet(), 0)
        attack = [effect.envelope(i/1000) for i in range(51)]
        release = [effect.envelope(.11+i/1000) for i in range(201)]
        self.assertEqual(attack[0], 0)
        self.assertEqual(attack[-1], 1)
        self.assertEqual(release[0], 1)
        self.assertAlmostEqual(release[-1], 0)
        self.assertTrue(all(a <= b for a,b in zip(attack,attack[1:])))
        self.assertTrue(all(a >= b for a,b in zip(release,release[1:])))
        self.assertLess(max(abs(effect.envelope(t+1/30)-effect.envelope(t))
                            for t in (i/1000 for i in range(311))), .86)

    def test_retrigger_preserves_current_brightness(self):
        effect = KickPixels()
        data = packet()
        effect.update(data, 0)
        data['decks'][0]['kick'] = 0
        effect.update(data, .2)
        before = effect.envelope(.3)
        data['decks'][0]['kick'] = .2
        effect.update(data, .3)
        self.assertAlmostEqual(effect.envelope(.3), before)
        self.assertGreater(effect.envelope(.34), before)

    def test_muted_or_stopped_decks_are_black(self):
        for data in (packet(volume=0),packet(playing=0)):
            effect=KickPixels()
            effect.update(data,0)
            self.assertEqual(effect.render(.01),BLACK)

    def test_zero_master_does_not_suppress_active_deck(self):
        effect = KickPixels()
        effect.update(dict(packet(),master=0),0)
        self.assertNotEqual(effect.render(.1),BLACK)

    def test_small_kick_does_not_trigger(self):
        effect = KickPixels()
        data = packet()
        data['decks'][0]['kick'] = .075  # Scales to 76.5, below the new threshold.
        effect.update(data, 0)
        self.assertEqual(effect.kick_count, 0)
        self.assertEqual(effect.render(.05), BLACK)
        data['decks'][0]['kick'] = .1
        effect.update(data, .02)
        self.assertEqual(effect.kick_count, 1)

    def test_link_ignores_wrong_sequence_and_rejects_nak(self):
        class Port:
            def __init__(self, lines): self.lines=iter(lines)
            def write(self, raw): self.raw=raw
            def readline(self): return next(self.lines)
        port=Port([b'ACK,99\n',b'ACK,0\n'])
        link=PixelLink(port);link.send(BLACK)
        self.assertEqual(link.sequence,1)
        with self.assertRaises(RuntimeError):
            PixelLink(Port([b'NAK,0\n'])).send(BLACK)


class VocalTests(unittest.TestCase):
    def test_vocals_work_without_kicks_and_preserve_center(self):
        effect=VocalKickPixels(50)
        data=packet()
        data['decks'][0].update(kick=0,vocal=.25)
        effect.update(data,0)
        first=effect.render(0)
        effect.update(data,.1)
        later=effect.render(.1)
        self.assertGreater(sum(later),sum(first))
        self.assertEqual(later[360:540],bytes(180))
        self.assertGreater(sum(later[:360]),0)
        self.assertGreater(sum(later[540:]),0)
        self.assertEqual(effect.render(.5),BLACK)

    def test_vocal_release_is_gradual_and_mute_stops_target(self):
        effect=VocalKickPixels()
        data=packet();data['decks'][0].update(kick=0,vocal=.25)
        effect.update(data,0);effect.render(0)
        effect.update(data,.2);effect.render(.2)
        previous=effect.vocal_level
        data['decks'][0]['volume']=0
        effect.update(data,.21);effect.render(.21)
        self.assertEqual(effect.vocal_target,0)
        self.assertGreater(effect.vocal_level,previous*.9)
        self.assertLess(effect.vocal_level,previous)

    def test_duplicate_decks_do_not_double_vocals(self):
        data=packet();other=dict(data['decks'][0],id=2)
        one=VocalKickPixels();one.update(data,0)
        data['decks'].append(other)
        two=VocalKickPixels();two.update(data,0)
        self.assertEqual(one.vocal_target,two.vocal_target)


class InstrumentTests(unittest.TestCase):
    def test_instrument_only_is_cyan_and_leaves_kick_black(self):
        effect=StemPixels()
        data=packet();data['decks'][0].update(kick=0,vocal=0,instru=.12)
        effect.update(data,0);pixels=effect.render(.1)
        self.assertEqual(pixels[360:540],bytes(180))
        self.assertGreater(pixels[20*3+1],0)
        self.assertGreater(pixels[20*3+2],pixels[20*3+1])
        self.assertEqual(pixels[20*3],0)
        self.assertEqual(effect.render(.5),BLACK)

    def test_release_is_smooth_and_duplicate_decks_do_not_amplify(self):
        effect=StemPixels()
        data=packet();data['decks'][0]['instru']=.12
        effect.update(data,0);effect.render(0)
        target=effect.instrument_target
        data['decks'].append(dict(data['decks'][0],id=2))
        effect.update(data,.1);effect.render(.1)
        self.assertEqual(effect.instrument_target,target)
        before=effect.instrument_level
        for deck in data['decks']:deck['volume']=0
        effect.update(data,.11);effect.render(.11)
        self.assertEqual(effect.instrument_target,0)
        self.assertGreater(effect.instrument_level,before*.95)
        self.assertLess(effect.instrument_level,before)

    def test_instrument_layer_preserves_kick_pixels(self):
        data=packet();data['decks'][0]['instru']=.12
        layered=StemPixels();kick=KickPixels()
        layered.update(data,0);kick.update(data,0)
        self.assertEqual(layered.render(.05)[360:540],kick.render(.05)[360:540])


class MusicalTests(unittest.TestCase):
    def test_phrase_change_crossfades_and_seek_does_not_change_palette(self):
        effect=StemPixels();data=packet(31.9)
        effect.update(data,0)
        before=effect.music.colors(.02)
        data['decks'][0]['beatpos']=32.01
        effect.update(data,.02)
        self.assertEqual(effect.music.palette,1)
        self.assertEqual(effect.music.colors(.02),before)
        self.assertNotEqual(effect.music.colors(1.52),before)
        self.assertEqual(effect.music.colors(3.02),effect.music.PALETTES[1])
        data['decks'][0]['beatpos']=100
        effect.update(data,10)
        self.assertEqual(effect.music.palette,1)
        data['decks'][0]['beatpos']=2
        effect.update(data,10.02)
        self.assertEqual(effect.music.palette,1)

    def test_busy_music_moves_faster_but_energy_ramps(self):
        quiet=StemPixels();busy=StemPixels()
        soft=packet();loud=packet()
        soft['decks'][0].update(kick=0,bass=0,hihat=0,instru=.01)
        loud['decks'][0].update(kick=.3,bass=.3,hihat=.3,instru=.3)
        for i in range(31):
            quiet.update(soft,i*.02);busy.update(loud,i*.02)
        self.assertGreater(busy.music.energy,quiet.music.energy)
        self.assertLess(busy.music.energy,.5)
        self.assertGreater(busy.music.phase,quiet.music.phase)

    def test_clock_deck_does_not_flap_or_change_palette_on_switch(self):
        effect=StemPixels();data=packet(31.9)
        effect.update(data,0)
        data['decks'].append(dict(data['decks'][0],id=2,level=.22,beatpos=64.01))
        effect.update(data,.02)
        self.assertEqual(effect.music.deck_id,1)
        data['decks'][1]['level']=.5
        effect.update(data,.04)
        self.assertEqual(effect.music.deck_id,2)
        self.assertEqual(effect.music.palette,0)


class TempoFallbackTests(unittest.TestCase):
    def test_missing_kick_gets_dim_orange_on_grid(self):
        effect=KickPixels(50);data=packet(1.9)
        data['decks'][0].update(kick=0,bass=0)
        effect.update(data,0)
        self.assertEqual(effect.render(.01),BLACK)
        data['decks'][0]['beatpos']=2.01
        effect.update(data,.05)
        pixels=effect.render(.10)
        self.assertGreater(pixels[360],0)
        self.assertLessEqual(pixels[360],24)
        self.assertEqual(effect.kick_count,0)
        self.assertEqual(effect.pulse_peak,.18)

    def test_strong_kick_wins_and_upgrades_dim_pulse_smoothly(self):
        effect=KickPixels();data=packet(1.9)
        data['decks'][0]['kick']=0
        effect.update(data,0)
        data['decks'][0]['beatpos']=2.01
        effect.update(data,.05)
        before=effect.envelope(.08)
        data['decks'][0]['kick']=.2
        effect.update(data,.08)
        self.assertEqual(effect.pulse_peak,1)
        self.assertAlmostEqual(effect.envelope(.08),before)
        self.assertEqual(effect.kick_count,1)

    def test_seek_and_deck_switch_do_not_generate_fallback(self):
        effect=KickPixels();data=packet(1.9)
        data['decks'][0]['kick']=0
        effect.update(data,0)
        data['decks'][0]['beatpos']=40.01
        effect.update(data,.02)
        self.assertEqual(effect.render(.05),BLACK)
        data['decks'][0].update(id=2,beatpos=41.01)
        effect.update(data,.06)
        self.assertEqual(effect.render(.1),BLACK)


class BeatPositionTests(unittest.TestCase):
    def test_positions_change_on_a_pulse_after_twenty_four_seconds(self):
        effect=KickPixels()
        effect.start_pulse(0,1)
        effect.start_pulse(7,1)
        self.assertEqual(effect.beat_start,120)
        effect.start_pulse(24,1)
        self.assertEqual(effect.beat_start,30)
        self.assertEqual(effect.previous_beat_start,120)
        self.assertEqual(effect.envelope(24),0)
        effect.audible=True;effect.last_update=24
        pixels=effect.render(24.1)
        self.assertGreater(pixels[30*3],0)
        self.assertGreater(pixels[120*3],0)
        effect.last_update=24.25
        pixels=effect.render(24.25)
        self.assertGreater(pixels[30*3],0)
        self.assertEqual(pixels[120*3],0)

    def test_all_beat_locations_fit_and_vocals_do_not_erase_them(self):
        effect=VocalKickPixels()
        data=packet();data['decks'][0].update(kick=0,vocal=0)
        locations=set()
        for i in range(8):
            now=i*24
            effect.update(data,now)
            effect.start_pulse(now,.18)
            locations.add(effect.beat_start)
            effect.last_update=now+.25
            pixels=effect.render(now+.25)
            self.assertGreater(pixels[effect.beat_start*3],0)
            self.assertLessEqual(effect.beat_start+60,300)
        self.assertGreater(len(locations),4)
