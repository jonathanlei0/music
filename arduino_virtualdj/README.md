# Computer-rendered VirtualDJ LEDs

The computer renders all 300 RGB pixels. Arduino only receives frames, checks CRC,
applies an electrical current cap, and displays the same frame on four strips.

## Run

Open arduino_virtualdj.ino in Arduino IDE, select Arduino Uno and its USB port,
and upload. Requires Adafruit NeoPixel and mirrored_neopixel.h in this folder.
Stop virtualdj_led.py and close Serial Monitor before uploading.

From the project root:

```sh
python3 virtualdj_led.py 50
```

Enable LED Stem Bridge in VirtualDJ. The brightness argument is a percentage.
`--monitor` shows meters without opening the Arduino. `--darkness` is accepted
for command compatibility but has no effect on this kick-only renderer.
Old 115200-baud feature senders, including audio_led.py, cannot drive this firmware.

## Current effect

Python's KickPixels in virtualdj_pixels.py flashes zero-based pixels 120–179
orange/red with a 50 ms eased rise, 60 ms hold and 200 ms eased fade.
A new kick starts from the current pulse brightness so it does not cut to black
between overlapping pulses. The kick must rise at least 35 above its 180 ms baseline,
reach 85 on the scaled 0–255 meter, and be 220 ms after the previous kick. It must
fall back toward baseline before rearming. These settings preserve the last
kick-only effect. A vocal layer adds two soft magenta ribbons on pixels 15–109 and 190–284.
Their width and brightness follow vocal intensity with 100 ms smoothing on rises
and 450 ms smoothing on falls. A raw vocal floor of .025 suppresses quiet leakage;
.25 reaches full vocal intensity. The layer peaks at 65% of the user brightness,
drifts over a nine-second cycle, and leaves the center kick pixels unchanged.
A cyan-blue instrumental wash fills pixels 0–39, 100–119, 180–199 and
260–299. It follows the instru meter with 140 ms rise smoothing and 550 ms
release smoothing, soft edges and a slow 12-second breathing motion. Raw
instru levels .005–.12 map to the intensity range; its maximum gain is 55% of
user brightness. Screen blending softens overlaps with vocal edges, while
center kick pixels are unchanged. Duplicate deck readings do not amplify it.
No tempo layer is active. Stale bridge data goes black
after 300 ms; the firmware also clears output if frames stop arriving.

Each strip has 300 GRB pixels; pins D5/D6/D7/D4 mirror the same image. Change the
Python renderer for effects. Keep NUM_LEDS consistent in Python and firmware.
MAX_MILLIAMPS in firmware is the combined power budget, currently inherited as
60000 mA. Match it to actual supply/wiring before expanding the lit area.

The kick renderer gates on playing decks with nonzero volume and deck level.
It does not use master VU as a blackout gate, because that reading has been
observed at zero while stems are active with the current audio routing.
Master-only muting is consequently not represented in this gate.

## Transport

500000 baud, 8N1, target 30 FPS; latest meter snapshots replace queued snapshots.
Frame: ASCII RGB1, little-endian uint16 sequence, uint16 payload length (900),
900 RGB bytes, uint16 CRC-16/CCITT (initial FFFF, poly 1021) over sequence, length,
and pixels. The firmware writes into the existing 900-byte GRB pixel buffer,
so no second framebuffer is needed. Incomplete or corrupt frames are not shown.

After display completes, Arduino replies ACK,<sequence> followed by newline;
CRC errors reply NAK,<sequence>. Python sends only one frame at a time and waits
for the matching acknowledgement before sending another, avoiding reception
while NeoPixel interrupts are disabled. Lost acknowledgements stop the sender;
restart it to reset the connection. Firmware resets partial packets after a
100 ms receive gap and blacks out after 700 ms of firmware clock time without
a valid frame (wall time can be longer due to interrupt-disabled LED output).

Verified on the connected Uno: 60 acknowledged 900-byte black frames at about
30.5 FPS maximum throughput, deliberate CRC corruption rejected, next valid
frame accepted. Live scheduling/USB can reduce achieved FPS. Forty-seven Python tests
cover mapping, trigger behavior, packet integrity and acknowledgement handling.

## Optional audio delay

Route VirtualDJ Master only to BlackHole 2ch, channels 1 & 2, removing the direct
Master output to avoid doubled audio. Keep a separate physical cue output if
needed. In a second terminal:

```sh
python3 virtualdj_audio_delay.py --delay-ms 50
```

Defaults to AirPods. For built-in speakers add
`--output-device "MacBook Pro Speakers"`. This is 50 ms added sample delay on top
of audio device latency, which can be substantial with Bluetooth. The relay is
independent of LED rendering and does not open the Arduino port.


## Musical phrases and motion

The renderer now cycles four coordinated vocal/instrument palettes at 32-beat
boundaries (8 bars in 4/4), crossfading for 3 seconds. These are beat-grid phrase
estimates, not detected song sections or melodic pitch. The kick remains orange.
Seeks, backwards loops, starting playback and clock-deck switches do not count
as phrase transitions; an 8-second guard prevents rapid palette cycling.

A stable dominant audible deck supplies BPM/beat position, with 40% level
hysteresis before changing to another deck. Smoothed kick/bass/hi-hat/instrument
energy gradually increases movement speed, vocal ribbon width/travel and
instrument wash coverage. Velocity is integrated so changing intensity does not
jump positions. Vocal and instrumental amplitude fades remain unchanged.

No Arduino upload is required. Restart virtualdj_led.py to apply Python changes.


## Missing kick fallback

Beat-grid crossings from the stable dominant deck now fill missing kick accents
with an orange pulse at 18% of normal kick brightness (before the user brightness
cap). They use the same 50 ms rise, 60 ms hold and 200 ms fade. A nearby strong
kick suppresses the fallback, and a strong kick arriving just after a fallback
smoothly upgrades it. Seeks/deck switches do not create artificial pulses.
The fallback requires fresh, audible deck data and beat position; it follows
VirtualDJ's beat grid instead of a free-running guessed clock.


## Melody sparkles

The default sender now opens a separate read-only BlackHole 2ch audio input.
Keep VirtualDJ Master routed to BlackHole and keep virtualdj_audio_delay.py
running to hear that audio. Melody capture does not replace or change playback.
Use `--no-melody` to disable it, or `--melody-device "name"` for another stereo
input. Failure to open capture leaves the existing stem layers active.

virtualdj_melody.py analyzes rolling 4096-sample windows at 48 kHz outside the
audio callback. Two consistent confident estimates confirm a note; quiet or
uncertain input creates no new sparkles. This estimates dominant pitch from the
full mix, not isolated instrumental melody, and chords/percussion can confuse it.
Its analysis window and confirmation add roughly 100–150 ms before device and
render latency; it is not guaranteed to align with the separate 50 ms audio delay.

Small, soft particles travel around the strip, colored by pitch class. Rising
notes move forward, falling notes backward; larger intervals and stronger song
energy increase speed. Confidence/audio level set brightness. Held notes seed a
few particles at 350 ms intervals; confirmed changes may seed them at 120 ms
intervals, with density adapted to song energy and recent note changes, capped at 48 particles.
Each fades in/out over 1.1–1.9 seconds and blends
with the existing layers. Stale audio stops spawning and stale deck data clears
particles. Terminal output includes the detected note (`--` when uncertain).

Tests include synthesized notes, confidence/silence gating, callback signature,
bounded buffering, pitch-direction changes and stale/muted input handling.


### Sparkle hopping and density

Sparkles now combine forward/backward drift with continuous oscillating hops.
Each has its own phase and bounce rate so they reverse direction independently
while traveling. Stronger song energy and recent confirmed note changes create
larger clusters (up to 5 particles per spawn), faster spawning, wider hops and
longer fades. Calm passages stay sparse. The 48-particle cap bounds rendering
work. Existing stem layers, brightness controls and kick settings are unchanged.


### Calm-section suppression

Sparkles are now disabled below 0.30 smoothed musical energy. Above that, an
energy gate ramps toward full activity at 0.80; note changes cannot bypass it.
Moderate passages get dim individual particles, with approximately 0.9 seconds
between note-change spawns and 1.8 seconds for held notes. Only high-energy
passages approach the fast spawning and large clusters. Sparkle brightness also
ramps with this energy gate. Existing particles finish their soft fades when
activity falls. These settings supersede the earlier always-active spawn rates.


## Moving beat accents

Orange kick and dim tempo-fallback pulses now share a 60-pixel region that
relocates on the next pulse after at least eight seconds. Seven positions cover
the strip; the change crossfades over 250 ms instead of jumping mid-frame.
Vocal ribbons screen-blend with the beat so relocated accents remain visible.
Kick threshold, pulse timing and user brightness cap remain unchanged.


## Note-driven section colors

Confirmed BlackHole pitch classes now steer both vocal ribbon and instrumental
wash colors, using coordinated hues 0.28 turns apart and a 300 ms eased RGB
transition. Repeated notes and octave changes do not restart a transition.
Uncertain or stale pitch holds the last confirmed color. This runs even in calm
sections where sparkles are suppressed. The full mix supplies one shared pitch
estimate; each stem is not separately pitch-tracked. Orange beat accents stay
orange. Before the first confirmed note (or with --no-melody), phrase palettes
still supply the section colors; confirmed note colors take priority afterward.


### Slower sparkles driven by instrumental variation

Sparkle drift and hopping rates are now roughly halved. Density follows changes
in the normalized instrumental meter against a 1.5-second local baseline,
with a noise deadband and smoothed 180 ms rise / 900 ms fall. Steady instruments,
kick and bass alone no longer create sparkle clusters. Quiet instrumental
readings suppress new particles. Confirmed pitch still supplies color and drift
direction, while instrumental variation supplies activity, cluster size and
motion intensity. This supersedes overall-energy/note-change density gating.


### Reduced sparkle count

Sparkle clusters are now limited to 1–3 particles per spawn, with a maximum of
12 active particles. Note-change spawns are spaced 350–1200 ms apart and held-note
spawns 650–2200 ms apart, depending on instrumental variation. This supersedes
the earlier 48-particle cap and faster spawning. Slower motion remains unchanged.


## Drop spectacle

ShowPixels adds a three-second cyan/violet full-strip sweep with three broad
center-out waves and eighteen gold/cyan comets. The scene eases in over 120 ms
and fades back over the final 1.5 seconds without repeated whole-strip flashes.
This rare show is independent of pitch confidence, so it works without melody
capture. It temporarily exceeds the normal 12-sparkle population; ordinary
sparkle density is unchanged. Every final channel is capped by user brightness,
and Arduino still applies its configured combined current budget.

The dedicated detector uses the dominant playing deck's stem meters, not the
unreliable master VU. It requires at least a second of quiet percussion, followed
within eight seconds by a strong kick/bass energy rise near a beat-grid boundary,
after at least two seconds of continuous deck observation. A 12-second cooldown
prevents repeated shows. Playback starts, seeks, deck switches and stale input
reset the detector. Terminal drops= counts accepted triggers. This is a meter
heuristic and may miss gradual drops; physical/music validation is still needed.
Restart virtualdj_led.py to apply; no Arduino upload is necessary.


## Loud builds, sustained bass and visible note color changes

Drop detection now also arms during a sustained bass-thin build, even if kicks
keep the total energy meter high. Bass/percussion returning can be confirmed over
80 ms instead of requiring a single-frame jump. A qualifying return stays armed
for 12 seconds; cooldown and playback/seek/stale protections remain. This is not
a song-specific transcription and has not been verified against a recording of
Calling. Meter traces around the relevant drop are needed for song tuning.

Sustained high energy now adds four broad traveling color bands, ramping in and
out smoothly. They use the note-driven vocal/instrument colors and speed up with
tempo/energy. Sparse ordinary sparkles and their instrumental gating are unchanged.
Note-color transitions are now 180 ms to make confirmed changes more apparent.

Terminal audio= reports BlackHole RMS, pitch_conf= reports pitch confidence,
and energy= reports smoothed stem energy. audio near zero with note=-- means
there is no useful capture for note analysis: check VirtualDJ Master routing to
BlackHole 2ch. Active stem meters do not prove that BlackHole receives audio.
If audio is present but note remains --, pitch may be uncertain in the full mix.

### Dense-mix note confirmation

In addition to two consecutive matching notes, three matching pitch classes in
the last five estimates (within 300 ms) now confirm a note. This allows octave
alternation and intervening chord tones without simply lowering confidence.
Silence clears the voting history; unrelated pitches remain unconfirmed.
49 VirtualDJ tests pass, including chord-tone alternation cases.

## Idle gradient

While virtualdj_led.py is running, no fresh audible deck signal for 700 ms now
selects a random dim gradient: yellow/red, blue/green, purple/blue or pink/orange.
The choice stays fixed during that idle period and changes on the next one.
Music/idle transitions ease over 900 ms. The gradient peaks at 8% of the user's
brightness cap (4% full brightness when running with 50). Brightness 0 stays off.
Fresh playing-deck level/volume data determines signal presence, independently
of the unreliable master meter and BlackHole pitch confidence.

Restart the Python sender; no Arduino upload is needed. Stopping the sender or
losing the USB connection still clears output via the firmware watchdog; the
idle gradient requires the computer to continue sending frames. 53 VirtualDJ
tests pass, including idle gradients and transitions.

## Beat-driven sparkle hops

Each detected strong kick now nudges existing sparkles forward/back by 3–5 LEDs,
alternating direction by particle and beat. Each hop eases over 50 ms, retaining
normal drift, bounce, color, lifetime and brightness. Dim tempo fallback pulses
and repeated held kick readings do not trigger hops. No new sparkles are spawned
by beats; instrumental variation still controls density. Hop state is discarded
when particles expire or bridge data goes stale. 55 VirtualDJ tests pass.

## Vocal-present pitch controls orange position

When the vocal stem exceeds the existing vocal gate, confirmed full-mix MIDI
notes E3–E6 (52–88) map monotonically to orange accent start positions 0–240.
Low notes are near pixel 0 and high notes near pixel 299; out-of-range notes
clamp to the ends. Position glides over 250 ms with fractional-pixel edges.
Repeated notes do not restart movement. Strong and dim fallback beat pulses
use this location; pitch alone does not light the orange accent.

This is a vocal-present dominant-pitch approximation: meters do not carry vocal
notes, and BlackHole contains the full mix. Instruments can influence the pitch
while vocals are active; isolated vocal audio would be needed for true vocal
pitch tracking. Automatic eight-second position changes pause until two seconds
after the last qualifying pitch reading. 57 VirtualDJ tests pass.

## Energy-proportional section coverage

The sender now uses EnergyShowPixels. Bass, vocal and instrumental meters share
300 pixels in proportion to their calibrated levels (full scales .4, .25, .12).
The strongest audible reading per stem avoids duplicate-deck amplification;
kick can support bass allocation when bass alone is absent. Shares ease over a
600 ms time constant instead of jumping on meter fluctuations.

Bass controls the orange pulse region's width, which can grow from nearly zero
to almost the whole strip. Its pitch-selected position adapts to the available
space so the expanded region stays in bounds. Remaining pixels split into vocal
and instrumental regions on either side. This replaces fixed section widths;
pulse timing, pitch colors and fades remain. Sparkles, high-energy traveling
bands and the rare drop show still overlay the layout. Idle gradients are unchanged.
60 VirtualDJ tests pass, including dominant stems, smooth allocation, duplicate
meters, and conservation of the 300-pixel layout.

### Stronger suppression of weak stems

Allocation now removes the lowest 5% of each calibrated meter range and squares
the remaining normalized level before dividing space. A weak stem therefore
gets dramatically less coverage alongside a strong one. Coverage shrinks with a
250 ms time constant and expands over 600 ms; absent stems reach zero allocation.
When all stems are weak, total coverage also contracts, leaving unused space
dark rather than filling it with a quiet stem. Special overlays (sparkles, busy
bands and drop shows) remain independent. 63 VirtualDJ tests pass.

## Calmer movement

Vocal regions and high-energy color pools are now anchored instead of traveling.
Stem coverage resizes over 800 ms on falls and 1 second on rises. Vocal-present
pitch moves the orange region over 1 second, using a slightly narrower position
range (24–216 in the original 60-pixel coordinate system). Automatic relocation
without vocal pitch now waits 24 seconds. Sparkle drift is reduced again, with
only a gentle 1–2.5 pixel sway at 0.2–0.34 Hz; strong-beat hops remain 3–5 LEDs.
Drop spectacles remain occasional and keep their deliberate wave motion.
63 VirtualDJ tests pass with the calmer movement settings.

### Beat-only sparkle movement

Ordinary melody sparkles now stay stationary between strong beats: continuous
travel and oscillating sway are disabled. Existing particles still hop forward
or backward 3–5 LEDs on strong kicks, eased over 50 ms. Spawning, soft fades,
colors and instrumental-variation density remain unchanged. Drop-show comets
are a separate rare effect. 63 VirtualDJ tests pass.
