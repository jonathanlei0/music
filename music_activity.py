"""Fixed-scale activity and relative change estimates for live lighting."""
from collections import deque
import math


def clamp(value):
    return max(0.0, min(1.0, value))


class ActivityDetector:
    def __init__(self):
        self.beats = deque()
        self.last_time = None
        self.activity = 0.0
        self.short_energy = None
        self.long_energy = None

    def update(self, frame, now):
        dt = 0 if self.last_time is None else max(0, now-self.last_time)
        self.last_time = now
        if dt > 0.5:
            self.beats.clear()  # Missing audio is not evidence of rhythm.
        if frame['event']:
            self.beats.append(now)
        while self.beats and now-self.beats[0] > 6:
            self.beats.popleft()
        density = clamp((len(self.beats)/6 - 0.25)/2.5)
        intervals = [b-a for a, b in zip(self.beats, list(self.beats)[1:])]
        confidence = 0.0
        if len(intervals) >= 3:
            mean = sum(intervals)/len(intervals)
            variance = sum((x-mean)**2 for x in intervals)/len(intervals)
            confidence = clamp(1-math.sqrt(variance)/max(mean, 0.001)/0.6)
        tempo = clamp((frame['bpm']-60)/120) * confidence
        energy = frame['level']/255
        # Loud sustained music alone must not produce fast foreground motion.
        target = (0.60*density + 0.25*tempo + 0.15*energy) * (0.15+0.85*density)
        if frame['db'] <= -48:
            target = 0.0
        dt = min(dt, 0.5)
        self.activity += (target-self.activity)*dt/(2.0+dt)
        if self.short_energy is None:
            self.short_energy = self.long_energy = energy
        self.short_energy += (energy-self.short_energy)*dt/(0.5+dt)
        self.long_energy += (energy-self.long_energy)*dt/(12.0+dt)
        # A relative change score for diagnostics/future effects, not a drop trigger.
        change = clamp(abs(self.short_energy-self.long_energy)*2)
        return round(clamp(self.activity)*255), round(change*255)
