# Four-strip wiring

Upload `arduino.ino` to the Arduino Uno. Connect each strip's data input
(DIN, at the start of the strip) to a separate digital pin:

| Strip | Arduino pin |
| --- | --- |
| 1 | D6 |
| 2 | D7 |
| 3 | D8 |
| 4 | D9 |

Power the strips from an external supply appropriate for their voltage and
connect the supply ground, strip grounds, and Arduino GND together. The Arduino
pins carry data only. Each strip connects directly to its assigned pin.

The sketch assumes 300 LEDs per strip (`NUM_LEDS`). All four strips mirror the
same complete frame: background, sparkle positions and colors, pulses and drop
accents. The Arduino renders and applies brightness/current limits once, then
sends that unchanged pixel buffer to D6/D7 simultaneously, then D8/D9 simultaneously. This removes three of
four rendering passes. The two pairs latch sequentially, but the two strips within each pair latch together. Wiring and the binary music protocol are unchanged.

`MAX_MILLIAMPS` is a combined estimated current budget for all four strips,
currently 60000 mA for the 5 V / 60 A supply. This is a software estimate,
not protection for wiring or connectors; power distribution must support the load.
Brightness is limited before each frame is sent.
Two mirrored 300-LED transmissions take about 18 ms total, plus rendering and
6 ms of receive windows. Actual frame rate is measured with `--led-stats`. Physical playback and serial reception should
be checked after upload.

The separate `led_count_probe` sketch remains a single-strip diagnostic on D6.

## Live brightness control

Upload the updated sketch before using the live player's brightness argument:

```sh
python3 audio_led.py 50
```

The optional integer sets a maximum LED brightness percentage from 0 to 100.
Omitting it selects 100; 0 turns the LEDs off while audio continues. This is a
linear LED output cap, not a percentage of perceived room brightness or audio
volume. It applies to all music effects and the standalone gradient after the
Arduino acknowledges the setting, until reset or another brightness command.
The player stops with an upload instruction if the firmware does not acknowledge
the cap. During Arduino boot, before configuration, its default cap is 100%.

Final brightness is the lower of the user cap and the electrical current limit.
Setting 100 does not bypass the combined 60 A limit.

## Background and foreground

```sh
python3 audio_led.py 50 --darkness 80
```

Cross-strip brightness modulation remains removed; `--wave-period` and
`--no-wave` are not options. The live background has a section-based palette,
with bright pools drifting along each strip on an activity-dependent 12–36-second cycle. All strips
show the same moving pattern. This is calculated per frame without waits.
Standalone mode retains its static background.

`--darkness` accepts 0–100 (default 65), controlling the width of moving dark gaps.
Selected dark areas retain a faint colored glow. Audio level still controls the
background's overall brightness with a fast fade.

Twelve persistent foreground sparkles travel at 0.125–12 LEDs per detected beat,
depending on the fixed-scale activity score. They bounce at the ends and never randomly respawn.
BPM changes ease into the new speed over roughly half a second without resetting
positions. Adjacent pixels crossfade for fractional-pixel motion. Treble changes
sparkle intensity instead of spawning probability, and silence fades their
visibility. Foreground note colors remain independent of the background section palette.
Tempo controls speed; particles are not snapped to individual beat onsets.

`led_effects.py` separates background level from foreground note hue, treble and
music events on the five-second delayed timeline. `led_layers.h` provides color
composition and persistent tempo motion. The complete image is rendered once
and copied unchanged to all four outputs, including the moving foreground and
background. Brightness and current limits apply after composition.

Upload this sketch before running the updated player. Existing offline packets
remain compatible with the layered renderer. Audio samples are unchanged.

## Refresh timing

The live player schedules frames from PortAudio's callback playback timestamp,
plus the five-second sample delay, minus an adjustable LED lead. The default
lead is 320 ms, selected for listening calibration.
This is not a measured hardware calibration. Audio playback remains unchanged.

```sh
python3 audio_led.py 50 --led-lead-ms 320
```

Increase the lead if lights still look late; decrease it if they look early.
The accepted range is 0–500 ms. This Python-only timing adjustment requires a
player restart, not a firmware upload. Sequential strip output, dropped serial
packets and inferred BPM drift can still cause timing variation that a fixed
lead cannot eliminate. Section look-ahead confirmation follows the same lead.

Ordinary beats, downbeats and phrase entrances do not launch large traveling pulses or blooms.
Sparkles retain fast motion, but their note colors follow detected melody notes with an approximately
160 ms time constant; background level uses an 180 ms rise and 300 ms fall time
constant, updated continuously without waiting. Large pulses and segmented flashes are reserved for drop events.
The live detector requires two seconds of non-silent history, an 8 dB lift,
strong new bass energy and a strong onset, with a 12-second cooldown. These are
conservative heuristics, not semantic recognition of song structure. The firmware
also enforces a 12-second cooldown so repeated drop packets cannot retrigger.

Foreground rendering visits only the pixels
covered by each effect; background color calculations use interpolated
12-pixel anchors. UART input is drained between background segments, with a 3 ms
nonblocking reception window after each strip rather than an 8 ms busy-wait.
Each loop transmits one pair; rendering and animation advancement occur
only before the first pair of each complete frame. Serial input continues
between transmissions, and new controls appear in the next rendered frame.
The 19-byte music packet
needs about 1.65 ms at 115200 baud; reception during LED transmission can still
lose packets, so physical timing remains approximate.

The Uno's Timer1 is reserved as a free-running clock to measure time lost by
`millis()` during NeoPixel transmission. Animation and packet timeout clocks
include this correction. Do not combine this sketch with Servo or other Timer1
users. Two paired 300-LED transmissions require roughly 18 ms,
plus 6 ms of receive windows and rendering. Actual frame rate must be measured on the device.

## Section palettes

The live player uses `section_palette.py` to combine sustained audio level, bass,
treble and rhythmic activity into a section-energy estimate. A new section must
persist for 2.5 seconds, with hysteresis at the quiet/energetic thresholds. Changes
are held for 12–45 seconds depending on activity; confirmed massive drops override the hold.
This is a heuristic and may need tuning for different music.

Palettes: calm purple/red, energetic red/orange, drop magenta/gold, and breakdown
dark blue/purple. Ordinary changes crossfade over four seconds, drops over 400 ms.
Particle positions and background motion continue through every palette change.

The five-second look-ahead allows confirmation before playback. Once confirmed,
still-buffered frames are annotated from the detected section boundary, so the
crossfade starts at that boundary in the delayed audio rather than after the
confirmation interval. The boundary is estimated from smoothed audio features.

Upload the updated firmware and restart `audio_led.py`. The player requires a
`PALETTES,1` acknowledgement. With this feature enabled, the formerly nominal
stereo-width byte carries a tagged palette ID and transition-speed flag. Packet
size stays 19 bytes; repeated palette states do not restart the fade. Offline
senders retain legacy width semantics when run after an Arduino reset.

## Fixed-scale musical activity

### Club middle layer

Above activity 160/255 (about 63%), eight colored bands gradually appear between
the background and foreground. They circulate along the strip at six LEDs per
beat, with tapered edges and gaps between bands. Their opacity smoothly rises
to 90/255 at full activity, keeping the foreground prominent. The band colors
span the hue wheel relative to the smoothed note color. Silence fades the bands
out. Sparkles and drop accents are composed afterward and retain higher priority.

The terminal's `club` label now uses the same activity threshold. All four strips
mirror the same middle layer; it is rendered once per shared frame without
additional framebuffers or waits. Overall brightness and current caps still apply.

Activity also widens the lit background regions and boosts their audio-driven
brightness by up to roughly 25%. At zero activity, `--darkness` applies unchanged;
at full activity its effective value is about one quarter (65 becomes 16), so
more LEDs light up while some contrast remains. These controls use the smoothed
activity score, and never bypass the user brightness cap or current budget.
Already-saturated levels cannot become brighter, and silence stays dark.

`music_activity.py` measures detected onset density over six seconds, tempo with
rhythmic-regularity confidence, and energy. Fixed thresholds distinguish genuinely
busy passages from sparse ones; scores are not normalized to make every track
look equally active. Loudness alone has little influence without rhythmic events.
The score eases over two seconds and travels on the same delayed timeline as the
music. This is feature-based, not genre recognition: lively classical passages
can score high, and sparse electronic passages can score low.

At minimum activity, foreground motion is 0.125 LEDs/beat (about one LED every
5.3 seconds at 90 BPM). At maximum it is 12 LEDs/beat. Motion uses a quadratic
activity curve, retains fractional position, and eases speed changes. Foreground
note-color response stays at 160 ms regardless of activity; calm music slows
particle movement rather than delaying melody colors.
Background drift takes 36 seconds when calm and 12 seconds when busy. Palette
holds range from 45 seconds to 12 seconds; a real section change is still required,
and massive drops can override the hold. Large accent rules are unchanged.

Terminal output now includes `Activity` and `Change` percentages. `Change`
compares recent energy (0.5-second smoothing) with a 12-second baseline; it is
currently diagnostic, not another flash trigger. The firmware requires an
`ACTIVITY,1` acknowledgement and then interprets payload byte 15 as activity.
Offline senders retain their earlier speed behavior after an Arduino reset.

## Melody colors

Both foreground sparkle sets use `FOREGROUND_PARTICLE_GAIN=31` (about 12% of
their previous RGB intensity), keeping particles subdued relative to the lit
background. Beat envelopes, widths and movement are unchanged. This color gain
is applied before foreground composition and the overall brightness cap.

There are now two foreground sets. The original twelve bass-sensitive sparkles
have a full-opacity core at least four LEDs wide, plus soft edges that expand
with bass. Their centers stay away from the strip ends so the core is not clipped.
Eight additional sparkles pulse once per BPM beat regardless of onset or bass
strength: 120 BPM gives two pulses per second. The second set follows melody
colors and has its own offset positions, with a shorter fade that finishes before
the next scheduled beat. Its clock anchors to the first detected beat, then runs
from the estimated BPM; it is a tempo prediction, not guaranteed beat tracking.
Silence pauses this second set. Quiet audible music still triggers it at full
pulse strength, subject to the user's brightness and electrical limits.

All twelve foreground sparkles are visible only during detected beat/downbeat
pulses, with no steady baseline between beats. Their persistent positions keep
moving internally, and their colors still follow the melody. Bass increases beat
brightness and width; each pulse lasts 550–850 ms with a 80 ms fade-in, a short peak hold, and
a smooth release. Ordinary beat peaks start at 180/255 opacity and strong bass
reaches 255/255, with wider footprints and brighter, less saturated note colors.
Retriggering during a fade continues from its current brightness; fast beats can
therefore keep the foreground lit until the final pulse fades away. Other foreground
accents are gated by the same beat window. The background and club middle layer
continue independently, with all four strips synchronized and brightness/current
limits unchanged.

Foreground sparkle colors follow the detected melodic pitch class (octaves share
colors) with a 160 ms color response, independently of particle speed/activity.
Pitch analysis uses a 4096-sample window and checks every two audio blocks, with
two confident detections required for a note change. Spectral leakage is filtered
to reduce spurious harmonic/subharmonic choices. Uncertain detections retain the
last reliable note. This estimates dominant pitch in a full mix, not an isolated
vocal or instrument; dense chords can still lead to the wrong melody choice.
Background palettes remain driven by sections, not individual notes.

## Smoother transitions

Foreground pulse widths now change at fractional-LED resolution instead of
jumping between integer radii. Their edges use a rounded curve, and beat fade-in
uses an 80 ms eased ramp. Background level follows a 180 ms rise / 300 ms fall;
melody colors use a 160 ms response. The 320 ms host lead is unchanged. These
changes soften transitions without adding waits or extra framebuffers; they do
not raise the physical refresh rate of the Uno's paired outputs.

## Paired output and actual frame-rate measurements

`mirrored_neopixel.h` uses the protected AVR pin mask in Adafruit_NeoPixel to
send identical bits to both pins on the same hardware port. D6/D7 use PORTD;
D8/D9 use PORTB. This reuses the library's existing timed transmission routine.
No rewiring is needed. The sketch checks the Uno ATmega328P target and fixed pin
layout at compile time. Other pins/boards require revisiting this implementation.

After uploading, run:

```sh
python3 audio_led.py 50 --led-stats
```

Every two seconds the terminal reports measured complete-frame FPS, average
render/update time, and average transmission time in milliseconds. The latter
uses Timer1 to include interrupt-disabled output time. These are Arduino-side
measurements, not host send-rate estimates. The rendering metric includes
animation and brightness limiting; FPS also includes receive windows and loop
work. No pulse/fade durations were changed for this output optimization.

## Render optimization and frame pacing

The renderer now uses exact 16-bit divide-by-255 arithmetic for RGB blending,
a 256-byte easing table stored in flash, direct unscaled GRB-buffer composition,
and a precomputed reciprocal for sparkle edges. Repeated drop-color conversion
was also moved out of the pixel loop. Blend results are exact; reciprocal edge
opacity differs by at most two levels before the 12% foreground color gain.

Completed frames target publication every 50 ms (20 FPS). The next image is
rendered into the shared buffer ahead of its deadline while the LED chips retain
the last displayed image. Early frames wait without blocking serial service.
Overruns are published late and the schedule resumes from there, without bursts
of catch-up frames. This is a target, not a verified real-device frame rate.

`--led-stats` now also reports `late frames` (over two ms past the deadline) and
`max render` for each reporting window. Steady 20 FPS with zero late frames means
the target is sustainable; late frames mean the target still exceeds the measured
budget. Rendering itself can vary with active effects even when display cadence
is fixed. Paired transmission, visual settings and the configured LED lead remain
unchanged. Upload and restart to measure the new firmware.
