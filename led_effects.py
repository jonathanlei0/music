"""Layer controls carried on the existing 16-byte LED feature protocol.

Build these from analysis ahead of playback; transmit only when the frame is due.
Pixel rendering and foreground-over-background composition live on the Arduino.
"""
from dataclasses import dataclass


def byte(value):
    return max(0, min(255, round(value)))


@dataclass(frozen=True)
class Background:
    level: int
    palette: int = 0
    fast: bool = False


@dataclass(frozen=True)
class Foreground:
    hue: int
    sparkle: int
    bass: int
    onset: int
    event: int = 0
    phrase: int = 0


@dataclass(frozen=True)
class LayerFrame:
    background: Background
    foreground: Foreground
    bpm: int
    activity: int

    def packet(self):
        bg, fg = self.background, self.foreground
        hue = fg.hue % 65536
        payload = bytes((
            byte(bg.level), byte(fg.bass), byte(fg.bass),
            byte(bg.level), byte(bg.level), byte(fg.sparkle), byte(fg.sparkle),
            hue & 255, hue >> 8, max(0, min(3, fg.event)), int(bool(fg.phrase)),
            max(50, min(200, self.bpm)),
            0xA0 | (int(bg.fast) << 3) | max(0, min(3, bg.palette)),
            byte(fg.onset), 180, byte(self.activity),
        ))
        checksum = 0
        for value in payload:
            checksum = ((checksum << 1) | (checksum >> 7)) & 255
            checksum ^= value
        return b"\xA5\x5A" + payload + bytes((checksum,))


def layers_from_analysis(frame, event=0, phrase=0):
    return LayerFrame(
        Background(frame["level"], frame.get("palette", 0), frame.get("palette_fast", False)),
        Foreground(frame["hue"], frame.get("treble", 0), frame["bass"],
                   byte(frame["onset"] * 255), event, phrase),
        frame["bpm"], frame.get("activity", frame["mood"]),
    )
