"""Computer-rendered kick frames and acknowledged 500 kbaud transport."""
import binascii
import struct
import time

NUM_LEDS = 300
BLACK = bytes(NUM_LEDS * 3)


def frame_packet(pixels, sequence):
    if len(pixels) != len(BLACK):
        raise ValueError('Expected 300 RGB pixels')
    header = struct.pack('<HH', sequence, len(pixels))
    crc = binascii.crc_hqx(header + pixels, 0xffff)
    return b'RGB1' + header + pixels + struct.pack('<H', crc)


class PixelLink:
    def __init__(self, port):
        self.port = port
        self.sequence = 0

    def send(self, pixels):
        sequence = self.sequence
        self.sequence = (sequence + 1) & 0xffff
        self.port.write(frame_packet(pixels, sequence))
        deadline = time.monotonic() + .3
        expected = f'ACK,{sequence}'.encode()
        while time.monotonic() < deadline:
            line = self.port.readline().strip()
            if line == expected:
                return
            if line == f'NAK,{sequence}'.encode():
                raise RuntimeError('Arduino rejected RGB frame checksum; restart the sender.')
        raise RuntimeError('No RGB frame acknowledgement. Upload the new 500000-baud firmware and restart.')


class MusicalMotion:
    # Coordinated vocal/instrument colors; kick retains its orange identity.
    PALETTES = (((205,15,170),(0,155,230)),
                ((235,65,110),(15,205,150)),
                ((155,65,240),(235,150,20)),
                ((230,110,45),(40,135,245)))

    def __init__(self):
        self.energy = 0.0
        self.bpm = 90.0
        self.phase = 0.0
        self.last_time = None
        self.deck_id = None
        self.last_beat = None
        self.palette = 0
        self.from_colors = self.PALETTES[0]
        self.changed_at = float('-inf')
        self.note_class = None
        self.note_from = None
        self.note_target = None
        self.note_changed_at = 0.0
        self.note_fade_seconds = .45

    def timing(self):
        speed = KickPixels.ease((self.bpm-85)/55)
        return dict(color_hold=.70-.52*speed,color_fade=.50-.34*speed,
                    size_interval=.40-.28*speed,shrink=1.30-1.0*speed,
                    grow=1.80-1.40*speed)

    def set_note(self, note, now):
        import colorsys
        pitch_class = note%12
        if pitch_class == self.note_class:
            return
        if self.note_class is not None and now-self.note_changed_at < self.timing()["color_hold"]:
            return
        self.note_from = self.colors(now)
        hue = pitch_class/12
        self.note_target = tuple(tuple(c*230 for c in colorsys.hsv_to_rgb(h,.80,1))
                                 for h in (hue,(hue+.28)%1))
        self.note_class = pitch_class
        self.note_changed_at = now
        self.note_fade_seconds = self.timing()["color_fade"]

    def colors(self, now):
        if self.note_target is not None:
            blend = KickPixels.ease((now-self.note_changed_at)/self.note_fade_seconds)
            return tuple(tuple(a+(b-a)*blend for a,b in zip(old,new))
                         for old,new in zip(self.note_from,self.note_target))
        blend = KickPixels.ease((now-self.changed_at)/3)
        target = self.PALETTES[self.palette]
        return tuple(tuple(a+(b-a)*blend for a,b in zip(old,new))
                     for old,new in zip(self.from_colors,target))

    def update(self, data, now):
        import math
        dt = 0 if self.last_time is None else min(.25,max(0,now-self.last_time))
        self.last_time = now
        decks = [d for d in data['decks'] if d['valid'] and d['playing'] > 0
                 and d['volume'] > 0 and d['level'] > .001]
        candidate = max(decks,key=lambda d:d['level']*d['volume'],default=None)
        current = next((d for d in decks if d['id']==self.deck_id),None)
        # Keep the clock deck through small level fluctuations during a blend.
        if current and candidate['level']*candidate['volume'] < current['level']*current['volume']*1.4:
            candidate = current
        target = max((min(1, (d['kick']*2+d['bass']*1.5+d['hihat']+d['instru'])*d['volume'])
                      for d in decks),default=0)
        self.energy += (target-self.energy)*(1-math.exp(-dt/1.2))
        bpm = min(200,max(50,candidate['bpm'])) if candidate else 90
        if candidate:
            self.bpm = bpm
        # Integrating velocity prevents position jumps when energy changes.
        self.phase = (self.phase+dt*bpm/60*(.12+.38*self.energy)) % (2*math.pi)
        if candidate is None:
            self.deck_id = self.last_beat = None
            return
        beat = candidate['beatpos']
        continuous = (candidate['id']==self.deck_id and self.last_beat is not None
                      and 0 <= beat-self.last_beat < 2 and dt > 0)
        if (continuous and int(beat//32)>int(self.last_beat//32)
                and now-self.changed_at >= 8):
            self.from_colors = self.colors(now)
            self.palette = (self.palette+1)%len(self.PALETTES)
            self.changed_at = now
        self.deck_id, self.last_beat = candidate['id'], beat


class KickPixels:
    ATTACK_SECONDS = .080
    HOLD_SECONDS = .080
    RELEASE_SECONDS = .360

    def __init__(self, brightness=50):
        self.brightness = brightness
        self.baseline = 0.0
        self.armed = True
        self.last_update = None
        self.last_kick = float('-inf')
        self.audible = False
        self.pulse_start = 0.0
        self.kick_count = 0
        self.pulse_peak = 1.0
        self.tempo_deck = None
        self.tempo_beat = None
        self.last_strong_kick = float("-inf")
        self.beat_start = self.previous_beat_start = 120
        self.beat_position_index = 0
        self.beat_moved_at = None
        self.vocal_position_at = float("-inf")
        self.note_position_from = self.note_position_target = 120.0
        self.note_position_changed = 0.0

    def update(self, data, now):
        dt = .02 if self.last_update is None else min(.25, max(0, now-self.last_update))
        self.last_update = now
        decks = [d for d in data['decks'] if d['valid'] and d['playing'] > 0
                 and d['volume'] > 0 and d['level'] > .001]
        # Master VU can report zero with BlackHole routing even while deck
        # meters are active. Gate on playing, nonzero-volume deck levels.
        self.audible = bool(decks)
        # Preserve the previous detector's meter scaling and sensitivity.
        kick = min(1, sum(max(0, d['kick'])*min(1, d['volume']) for d in decks)*4)*255 if self.audible else 0
        rise = kick-self.baseline
        if rise <= 10:
            self.armed = True
        if self.armed and kick >= 85 and rise >= 35 and now-self.last_strong_kick >= .220:
            self.armed = False
            self.kick_count += 1
            self.start_pulse(now,1.0)
            self.last_strong_kick = now
        # Fill empty beat-grid crossings with a subtle orange pulse. A strong
        # kick near the crossing wins; never reduce an existing bright pulse.
        candidate = max(decks,key=lambda d:d['level']*d['volume'],default=None)
        current = next((d for d in decks if d['id']==self.tempo_deck),None)
        if current and candidate['level']*candidate['volume'] < current['level']*current['volume']*1.4:
            candidate = current
        if candidate:
            beat = candidate['beatpos']
            continuous = (candidate['id']==self.tempo_deck and self.tempo_beat is not None
                          and 0 <= beat-self.tempo_beat < .5)
            period = 60/max(50,min(200,candidate['bpm']))
            if (continuous and int(beat//1)>int(self.tempo_beat//1)
                    and now-self.last_strong_kick > period*.55
                    and now-self.last_kick >= .220
                    and not self.pulse_active(now)):
                self.start_pulse(now,.18)
            self.tempo_deck, self.tempo_beat = candidate['id'],beat
        else:
            self.tempo_deck = self.tempo_beat = None
        self.baseline += (kick-self.baseline)*dt/(.180+dt)
        if not self.audible:
            self.baseline = 0
            self.last_kick = float('-inf')

    def pulse_active(self, now):
        return now-self.last_kick < self.ATTACK_SECONDS+self.HOLD_SECONDS+self.RELEASE_SECONDS

    def start_pulse(self, now, peak):
        if self.pulse_active(now):
            return False # Skip overlapping onsets, without postponing them.
        self.pulse_start = 0.0
        self.pulse_peak = peak
        self.last_kick = now
        if self.beat_moved_at is None:
            self.beat_moved_at = now
        elif now-self.beat_moved_at >= 24 and now-self.vocal_position_at > 2:
            locations = (120,30,210,75,165,0,240)
            self.previous_beat_start = self.beat_start
            self.beat_position_index = (self.beat_position_index+1)%len(locations)
            self.beat_start = locations[self.beat_position_index]
            self.beat_moved_at = now

    def vocal_beat_position(self, now):
        blend = self.ease((now-self.note_position_changed)/1.0)
        return self.note_position_from+(self.note_position_target-self.note_position_from)*blend

    def set_vocal_position(self, note, now):
        # MIDI E3–E6 maps to the available start positions of a 60-pixel accent.
        target = max(24,min(216,24+(note-52)*192/36))
        if now-self.vocal_position_at > 2:
            self.note_position_from = float(self.beat_start)
            self.note_position_target = self.note_position_from
        self.vocal_position_at = now
        if target == self.note_position_target:
            return
        self.note_position_from = self.vocal_beat_position(now)
        self.note_position_target = target
        self.note_position_changed = now
        self.previous_beat_start = self.beat_start
        self.beat_start = round(target)
        self.beat_moved_at = now

    @staticmethod
    def ease(value):
        value = max(0.0, min(1.0, value))
        return value*value*(3-2*value)

    def envelope(self, now):
        age = now-self.last_kick
        if age < 0:
            return 0.0
        if age < self.ATTACK_SECONDS:
            return self.pulse_start + (self.pulse_peak-self.pulse_start)*self.ease(age/self.ATTACK_SECONDS)
        age -= self.ATTACK_SECONDS + self.HOLD_SECONDS
        if age <= 0:
            return self.pulse_peak
        return self.pulse_peak*(1-self.ease(age/self.RELEASE_SECONDS))

    def render(self, now):
        if not self.audible or self.last_update is None or now-self.last_update > .3:
            return BLACK
        level = round(255*self.brightness/100*self.envelope(now))
        pixels = bytearray(BLACK)
        beat_width = getattr(self,"bass_width",60)
        span = NUM_LEDS-beat_width
        if now-self.vocal_position_at <= 2:
            import math
            start = self.vocal_beat_position(now)/240*span
            for i in range(max(0,math.floor(start)),min(NUM_LEDS,math.ceil(start+beat_width))):
                coverage = max(0,min(i+1,start+beat_width)-max(i,start))
                pixels[i*3] = round(level*coverage)
                pixels[i*3+1] = round(level*40/255*coverage)
            return bytes(pixels)
        blend = 1 if self.beat_moved_at is None else self.ease((now-self.beat_moved_at)/.25)
        for start, weight in ((self.previous_beat_start,1-blend),(self.beat_start,blend)):
            red = round(level*weight)
            green = round(level*40/255*weight)
            start = round(start/240*span)
            for i in range(start,start+beat_width):
                pixels[i*3] = min(255,pixels[i*3]+red)
                pixels[i*3+1] = min(255,pixels[i*3+1]+green)
        return bytes(pixels)


class VocalKickPixels(KickPixels):
    """Soft vocal ribbons either side of the unchanged center kick accent."""
    def __init__(self, brightness=50):
        super().__init__(brightness)
        self.vocal_target = 0.0
        self.vocal_level = 0.0
        self.last_render = None

    def update(self, data, now):
        super().update(data, now)
        # Max avoids multiplying identical readings from duplicate bridge decks.
        vocal = max((max(0, d['vocal'])*min(1, d['volume'])
                     for d in data['decks'] if d['valid'] and d['playing'] > 0
                     and d['volume'] > 0 and d['level'] > .001), default=0)
        self.vocal_target = self.ease((vocal-.025)/.225)

    def render(self, now):
        import math
        dt = 1/30 if self.last_render is None else max(0, now-self.last_render)
        self.last_render = now
        if self.last_update is None or now-self.last_update > .3:
            self.vocal_level = 0.0
            return BLACK
        response = .100 if self.vocal_target > self.vocal_level else .450
        self.vocal_level += (self.vocal_target-self.vocal_level)*(1-math.exp(-dt/response))
        pixels = bytearray(super().render(now))
        # Slow continuous drift; vocal amplitude changes width and brightness.
        drift = 0  # Keep vocal regions anchored; amplitude still follows the music.
        width = 16+22*self.vocal_level+8*getattr(self,"motion_energy",0)
        gain = .65*self.vocal_level*self.brightness/100
        zones = getattr(self,"vocal_zones",((15,110,62+drift),(190,285,237-drift)))
        for start, stop, center in zones:
            zone_width = max(1,(stop-start)/2) if hasattr(self,"vocal_zones") else width
            for i in range(start,stop):
                shape = self.ease(1-abs(i-center)/zone_width)
                offset = i*3
                for channel,c in enumerate(getattr(self,"vocal_color",(205,15,170))):
                    value = round(c*gain*shape)
                    old = pixels[offset+channel]
                    pixels[offset+channel] = 255-round((255-old)*(255-value)/255)
        return bytes(pixels)


class StemPixels(VocalKickPixels):
    """Cyan instrumental washes around the magenta vocals and orange kick."""
    def __init__(self, brightness=50):
        super().__init__(brightness)
        self.instrument_target = 0.0
        self.instrument_level = 0.0
        self.instrument_render_at = None
        self.music = MusicalMotion()

    def update(self, data, now):
        super().update(data, now)
        self.music.update(data,now)
        instrument = max((max(0, d['instru'])*min(1, d['volume'])
                          for d in data['decks'] if d['valid'] and d['playing'] > 0
                          and d['volume'] > 0 and d['level'] > .001), default=0)
        self.instrument_target = self.ease((instrument-.005)/.115)

    def render(self, now):
        import math
        dt = 1/30 if self.instrument_render_at is None else max(0, now-self.instrument_render_at)
        self.instrument_render_at = now
        self.motion_energy = self.music.energy
        self.motion_phase = self.music.phase
        self.vocal_color, instrument_color = self.music.colors(now)
        pixels = bytearray(super().render(now))
        if self.last_update is None or now-self.last_update > .3:
            self.instrument_level = 0.0
            return BLACK
        response = .140 if self.instrument_target > self.instrument_level else .550
        self.instrument_level += (self.instrument_target-self.instrument_level)*(1-math.exp(-dt/response))
        gain = .55*self.instrument_level*self.brightness/100
        # Rounded washes at the ends and beside the center, with slow breathing.
        for start, stop in getattr(self,"instrument_zones",((0,40),(100,120),(180,200),(260,300))):
            for i in range(start,stop):
                position = (i-start+.5)/(stop-start)
                shape = math.sin(math.pi*position)**(2-self.music.energy)
                motion = .8+.2*math.sin(self.music.phase+i*.025)
                offset = i*3
                for channel, color in enumerate(instrument_color):
                    # Bounded screen blend softens overlaps with vocal ribbon edges.
                    value = round(color*gain*shape*motion)
                    old = pixels[offset+channel]
                    pixels[offset+channel] = 255-round((255-old)*(255-value)/255)
        return bytes(pixels)


class MelodyPixels(StemPixels):
    """Small traveling particles colored by confirmed dominant pitch class."""
    def __init__(self, brightness=50):
        super().__init__(brightness)
        self.particles = []
        self.melody_note = None
        self.last_spawn = float('-inf')
        self.spawn_count = 0
        self.instrument_baseline = None
        self.instrument_variation = 0.0
        self.beat_hops = {}

    def update(self, data, now):
        import math
        previous = self.last_update
        old_kicks = self.kick_count
        super().update(data,now)
        if self.kick_count != old_kicks:
            for index,particle in enumerate(self.particles):
                if not 0 <= now-particle[0] < particle[8]:
                    continue
                key = particle[:2]
                current = self.hop_offset(particle,now)
                direction = 1 if (self.kick_count+index)%2 else -1
                distance = 3+(index%3)
                self.beat_hops[key] = (current,current+direction*distance,now)
        dt = .02 if previous is None else min(.25,max(0,now-previous))
        level = self.instrument_target
        if self.instrument_baseline is None or previous is None or now-previous > .3:
            self.instrument_baseline = level
            self.instrument_variation = 0.0
            return
        # Contrast against a slow local baseline measures instrumental variation,
        # rather than treating loud, steady instruments or drums as busy melody.
        contrast = max(0,abs(level-self.instrument_baseline)-.025)
        response = .18 if contrast > self.instrument_variation else .9
        self.instrument_variation += (contrast-self.instrument_variation)*(1-math.exp(-dt/response))
        self.instrument_baseline += (level-self.instrument_baseline)*(1-math.exp(-dt/1.5))

    def set_melody(self, reading, now):
        import colorsys
        if (reading.note is None or now-reading.at > .35 or reading.strength <= 0
                or not self.audible or self.last_update is None or now-self.last_update > .3):
            return
        # Section colors follow confirmed notes even when sparkles are suppressed.
        self.music.set_note(reading.note,now)
        if self.vocal_target > .10:
            self.set_vocal_position(reading.note,now)
        import math
        # Note changes alone must not turn a quiet arrangement into a busy show.
        activity_gate = self.ease((self.instrument_variation-.04)/.35)*self.ease(self.instrument_target/.2)
        if activity_gate <= 0:
            return
        density = activity_gate
        changed = reading.note != self.melody_note
        spawn_gap = (1.2-.85*density) if changed else (2.2-1.55*density)
        if now-self.last_spawn < spawn_gap:
            return
        interval = 0 if self.melody_note is None else reading.note-self.melody_note
        direction = -1 if interval < 0 else 1
        speed = 0  # Sparkles stay anchored between strong-beat hops.
        color = colorsys.hsv_to_rgb((reading.note%12)/12,.55,1)
        # Quiet passages stay sparse; energetic/note-dense passages form clusters.
        count = 1+int(density*2)
        for _ in range(count):
            seed = self.spawn_count
            position = (seed*73+31)%NUM_LEDS
            phase = (seed*2.39996)%(2*math.pi)
            amplitude = 0
            frequency = 0
            lifetime = 1.1+.8*density
            self.particles.append((now,position,speed,color,reading.strength*(.25+.75*activity_gate),
                                   phase,amplitude,frequency,lifetime))
            self.spawn_count += 1
        self.particles = self.particles[-12:]
        self.melody_note = reading.note
        self.last_spawn = now

    def hop_offset(self, particle, now):
        old,target,at = self.beat_hops.get(particle[:2],(0,0,now))
        return old+(target-old)*self.ease((now-at)/.050)

    def particle_position(self, particle, now):
        import math
        born, position, speed, _, _, phase, amplitude, frequency, _ = particle
        age = now-born
        # Drift plus a continuous back-and-forth hop; no position teleports.
        hop = amplitude*(math.sin(phase+age*2*math.pi*frequency)-math.sin(phase))
        return (position+speed*age+hop+self.hop_offset(particle,now))%NUM_LEDS

    def render(self, now):
        import math
        pixels = bytearray(super().render(now))
        if self.last_update is None or now-self.last_update > .3 or not self.audible:
            self.particles.clear()
            self.beat_hops.clear()
            return bytes(pixels)
        self.particles = [p for p in self.particles if 0 <= now-p[0] < p[8]]
        live = {p[:2] for p in self.particles}
        self.beat_hops = {key:value for key,value in self.beat_hops.items() if key in live}
        for particle in self.particles:
            born, position, speed, color, strength, phase, amplitude, frequency, lifetime = particle
            age = now-born
            fade = math.sin(math.pi*age/lifetime)**2
            center = self.particle_position(particle,now)
            gain = .55*strength*fade*self.brightness/100
            for offset in range(-3,4):
                unwrapped = math.floor(center)+offset
                shape = self.ease(1-abs(unwrapped-center)/3)
                pixel = (unwrapped%NUM_LEDS)*3
                for channel in range(3):
                    value = round(255*color[channel]*gain*shape)
                    old = pixels[pixel+channel]
                    pixels[pixel+channel] = 255-round((255-old)*(255-value)/255)
        return bytes(pixels)


class DropDetector:
    """Confirm percussion returning after a sustained quiet, playing passage."""
    def __init__(self):
        self.last_time = None
        self.deck = self.beat = None
        self.started = None
        self.quiet_since = None
        self.armed_until = float('-inf')
        self.baseline = 0.0
        self.previous_energy = 0.0
        self.last_drop = float('-inf')
        self.break_bass = 0.0
        self.break_energy = 0.0
        self.return_since = None

    def update(self, data, now):
        decks = [d for d in data['decks'] if d['valid'] and d['playing'] > 0
                 and d['volume'] > 0 and d['level'] > .001]
        deck = max(decks,key=lambda d:d['level']*d['volume'],default=None)
        current = next((d for d in decks if d['id']==self.deck),None)
        if current and deck['level']*deck['volume'] < current['level']*current['volume']*1.4:
            deck = current
        dt = 0 if self.last_time is None else now-self.last_time
        self.last_time = now
        discontinuity = (deck is None or self.deck != deck['id'] or dt > .3 or dt < 0
                         or (self.beat is not None and
                             not 0 <= deck['beatpos']-self.beat <= max(1,dt*4+.4)))
        if discontinuity:
            self.started = now if deck else None
            self.quiet_since = None
            self.armed_until = float('-inf')
            self.baseline = self.previous_energy = 0.0
            self.return_since = None
        self.deck = deck['id'] if deck else None
        self.beat = deck['beatpos'] if deck else None
        if deck is None:
            return False
        gain = min(1,deck['volume'])
        kick, bass = max(0,deck['kick'])*gain,max(0,deck['bass'])*gain
        energy = min(1,kick*3+bass*2+max(0,deck['instru'])*gain)
        # Builds often retain kick/snare energy while the deep bass is absent.
        # Recognize that bass-thin context too, rather than requiring near-silence.
        if energy < .22 or bass < .10:
            if self.quiet_since is None:
                self.quiet_since = now
            if now-self.quiet_since >= 1:
                self.armed_until = now+12
                self.break_bass = bass
                self.break_energy = energy
        else:
            self.quiet_since = None
        phase = deck['beatpos']%1
        returned = (kick >= .08 and bass >= .07 and energy >= .50
                    and (bass-self.break_bass >= .07 or energy-self.break_energy >= .25))
        if returned:
            if self.return_since is None:
                self.return_since = now
        else:
            self.return_since = None
        # Allow a short sustained return; don't demand a single-frame jump or
        # a 2.2x rise in a meter already saturated by a loud build.
        established = (returned and (energy-self.previous_energy >= .20
                       or now-self.return_since >= .08))
        drop = (not discontinuity and self.started is not None and now-self.started >= 2
                and now <= self.armed_until and now-self.last_drop >= 12
                and established and (phase <= .35 or phase >= .85))
        if drop:
            self.last_drop = now
            self.armed_until = float('-inf')
        self.baseline += (energy-self.baseline)*min(.25,max(0,dt))/(1.5+max(0,dt))
        self.previous_energy = energy
        return drop


class ShowPixels(MelodyPixels):
    """A rare, smooth full-strip spectacle on confirmed drops."""
    def __init__(self, brightness=50):
        super().__init__(brightness)
        self.drops = DropDetector()
        self.drop_at = float('-inf')
        self.drop_count = 0
        self.action_level = 0.0
        self.action_render_at = None

    def update(self, data, now):
        super().update(data,now)
        if self.drops.update(data,now):
            self.drop_at = now
            self.drop_count += 1

    def render(self, now):
        import math
        import colorsys
        pixels = bytearray(super().render(now))
        age = now-self.drop_at
        cap = round(255*self.brightness/100)
        dt = 1/30 if self.action_render_at is None else max(0,now-self.action_render_at)
        self.action_render_at = now
        fresh = self.audible and self.last_update is not None and now-self.last_update <= .3
        target = self.ease((self.music.energy-.40)/.50) if fresh else 0
        self.action_level += (target-self.action_level)*(1-math.exp(-dt/.45))
        if fresh and self.action_level > .001:
            colors = self.music.colors(now)
            # Broad traveling bands, distinct from the intentionally sparse sparkles.
            for band in range(4):
                center = band*75+37  # Anchored pools instead of traveling bands.
                width = 5+13*self.action_level
                color = colors[band%2]
                for offset in range(-19,20):
                    location = math.floor(center)+offset
                    shape = self.ease(1-abs(location-center)/width)
                    for channel in range(3):
                        index = location%NUM_LEDS*3+channel
                        value = round(color[channel]*self.brightness/100*.65*self.action_level*shape)
                        pixels[index] = min(cap,255-round((255-pixels[index])*(255-value)/255))
        if (0 <= age < 3 and self.audible and self.last_update is not None
                and now-self.last_update <= .3):
            # Broad violet/cyan sweep changes the whole scene, without strobes.
            envelope = self.ease(age/.12)*(1-self.ease((age-1.5)/1.5))
            for i in range(NUM_LEDS):
                radius = abs(i-(NUM_LEDS-1)/2)
                wave = 0.0
                for delay in (0,.30,.60):
                    t = age-delay
                    if t >= 0:
                        wave = max(wave,self.ease(1-abs(radius-t*150)/28))
                hue = (.58+.18*i/NUM_LEDS+.07*age)%1
                color = colorsys.hsv_to_rgb(hue,.75,1)
                gain = envelope*(.22+.78*wave)
                for channel in range(3):
                    index = i*3+channel
                    value = round(cap*color[channel]*gain)
                    pixels[index] = min(cap,255-round((255-pixels[index])*(255-value)/255))
            # Eighteen soft comet accents, exclusive to the three-second drop show.
            for seed in range(18):
                center = (seed*73+age*(28 if seed%2 else -28))%NUM_LEDS
                color = (1,.55,.12) if seed%2 else (.2,.85,1)
                for offset in range(-3,4):
                    location = math.floor(center)+offset
                    shape = self.ease(1-abs(location-center)/3)
                    for channel in range(3):
                        index = location%NUM_LEDS*3+channel
                        pixels[index] = min(cap,pixels[index]+round(cap*.5*color[channel]*shape*envelope))
        return bytes(min(cap,c) for c in pixels)


class IdleShowPixels(ShowPixels):
    """Dim random gradients between songs while the sender stays connected."""
    IDLE_PAIRS = (((255,160,0),(255,0,0)),
                  ((0,40,255),(0,220,65)),
                  ((170,0,255),(0,130,255)),
                  ((255,35,90),(255,140,0)))

    def __init__(self, brightness=50):
        super().__init__(brightness)
        self.idle = None
        self.idle_pair = None
        self.idle_changed_at = None
        self.last_signal_at = None
        self.displayed = BLACK
        self.transition_from = BLACK

    def render(self, now):
        import random
        active = super().render(now)
        fresh = self.audible and self.last_update is not None and now-self.last_update <= .3
        if fresh:
            self.last_signal_at = now
        idle = self.last_signal_at is None or now-self.last_signal_at >= .7
        if idle != self.idle:
            self.idle = idle
            self.idle_changed_at = now
            self.transition_from = self.displayed
            if idle:
                choices = [pair for pair in self.IDLE_PAIRS if pair != self.idle_pair]
                self.idle_pair = random.choice(choices)
        if idle:
            left,right = self.idle_pair
            # 8% of the user's brightness cap: 4% full scale at brightness 50.
            gain = .08*self.brightness/100
            target = bytes(round((a+(b-a)*i/(NUM_LEDS-1))*gain)
                           for i in range(NUM_LEDS) for a,b in zip(left,right))
        else:
            target = active
        blend = self.ease((now-self.idle_changed_at)/.9)
        self.displayed = bytes(round(a+(b-a)*blend) for a,b in zip(self.transition_from,target))
        return self.displayed


class EnergyShowPixels(IdleShowPixels):
    """Smoothly divide strip coverage between bass, vocal and instrumental energy."""
    def __init__(self, brightness=50):
        super().__init__(brightness)
        self.section_shares = [0.0,0.0,0.0]
        self.share_updated_at = None
        self.coverage_targets = None
        self.coverage_checked_at = float("-inf")

    def update(self, data, now):
        import math
        previous = self.share_updated_at
        super().update(data,now)
        self.share_updated_at = now
        dt = .02 if previous is None else min(.25,max(0,now-previous))
        decks = [d for d in data['decks'] if d['valid'] and d['playing']>0
                 and d['volume']>0 and d['level']>.001]
        levels = [max((max(0,d[name])*min(1,d['volume']) for d in decks),default=0)
                  for name in ('bass','vocal','instru')]
        kick = max((max(0,d['kick'])*min(1,d['volume']) for d in decks),default=0)
        levels[0] = max(levels[0],kick*.5)
        # Suppress leakage and emphasize strong stems instead of weak leftovers.
        normalized = [max(0,min(1,(level/full-.05)/.95))
                      for level,full in zip(levels,(.4,.25,.12))]
        weights = [level**2 for level in normalized]
        total = sum(weights)
        coverage = self.ease(max(normalized,default=0)/.65)
        targets = [coverage*weight/total for weight in weights] if total > 1e-9 else [0,0,0]
        timing = self.music.timing()
        if self.coverage_targets is None:
            self.coverage_targets = targets
            self.coverage_checked_at = now
        elif now-self.coverage_checked_at >= timing["size_interval"]:
            self.coverage_checked_at = now
            if sum(targets)==0 or max(abs(a-b) for a,b in zip(targets,self.coverage_targets)) >= .02:
                self.coverage_targets = targets
        updated = []
        for old,target in zip(self.section_shares,self.coverage_targets):
            response = timing["shrink"] if target < old else timing["grow"]
            value = old+(target-old)*(1-math.exp(-dt/response))
            updated.append(0.0 if target == 0 and value < 1/600 else value)
        self.section_shares = updated

    def render(self, now):
        bass,vocal,instrument = self.section_shares
        self.bass_width = round(NUM_LEDS*bass)
        span = NUM_LEDS-self.bass_width
        if now-self.vocal_position_at <= 2:
            start = round(self.vocal_beat_position(now)/240*span)
        else:
            start = round(self.beat_start/240*span)
        end = start+self.bass_width
        fraction = vocal/max(.000001,vocal+instrument)
        occupancy = min(1,(vocal+instrument)/max(.000001,1-bass))
        self.vocal_zones = []
        self.instrument_zones = []
        for left,right in ((0,start),(end,NUM_LEDS)):
            used = round((right-left)*occupancy)
            vocal_count = round(used*fraction)
            instrumental_count = used-vocal_count
            # Unused space stays dark instead of being donated to a weak stem.
            if left == 0:
                boundary = right-used
                split = boundary+vocal_count
                self.vocal_zones.append((boundary,split,(boundary+split-1)/2))
                self.instrument_zones.append((split,right))
            else:
                split = left+instrumental_count
                self.instrument_zones.append((left,split))
                self.vocal_zones.append((split,left+used,(split+left+used-1)/2))
        pixels = bytearray(super().render(now))
        # LEDs 1–4 are a reserved steady orange marker, even during idle fades.
        level = min(round(255*.05),round(255*self.brightness/100))
        pixels[:12] = bytes((level,round(level*40/255),0))*4
        return bytes(pixels)
