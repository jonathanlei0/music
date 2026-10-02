"""Confirm section moods ahead of playback; return their original start times."""
class SectionPalette:
    CONFIRM_SECONDS = 2.5
    HOLD_SECONDS = 12.0

    def __init__(self):
        self.palette = 0  # calm, energetic, drop, breakdown
        self.fast = False
        self.changed_at = float('-inf')
        self.pending = None
        self.pending_at = 0.0
        self.previous_time = None
        self.energy = None

    def update(self, frame, now):
        dt = 0 if self.previous_time is None else max(0, now-self.previous_time)
        if dt > 0.3:
            self.pending = None
        self.previous_time = now
        score = (0.5*frame['level'] + 0.25*frame['bass']
                 + 0.15*frame.get('treble', 0) + 0.1*frame['mood']) / 255
        self.energy = score if self.energy is None else self.energy + (score-self.energy)*dt/(0.5+dt)
        if frame['event'] == 3:
            self.palette, self.fast, self.changed_at = 2, True, now
            self.pending = None
            return now, 2, True
        # Hysteresis keeps borderline sections from flickering between palettes.
        quiet = self.energy < (0.36 if self.palette == 3 else 0.28)
        energetic = self.energy > (0.56 if self.palette == 1 else 0.68)
        candidate = 3 if quiet else 1 if energetic else 0
        # Sparse music holds its palette much longer. Busy music still requires
        # a real section change; activity does not force arbitrary cycling.
        activity = max(0, min(255, frame.get('activity', 255))) / 255
        hold = self.HOLD_SECONDS + 33*(1-activity)
        if candidate == self.palette or now-self.changed_at < hold:
            self.pending = None
            return None
        if candidate != self.pending:
            self.pending, self.pending_at = candidate, now
            return None
        if now-self.pending_at < self.CONFIRM_SECONDS:
            return None
        start = self.pending_at
        self.palette, self.fast, self.changed_at = candidate, False, start
        self.pending = None
        return start, candidate, False
