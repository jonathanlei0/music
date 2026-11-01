# Independent audio playback and LED rendering

Music playback and lighting now run as **two separate processes**. Restarting or
changing LED effect code does not close the audio device or interrupt the music.
Both processes use the project venv automatically when needed.

## Terminal 1: start music once

Set macOS Sound output to **BlackHole 2ch**, then run:

```sh
python3 audio_playback.py
```

This captures BlackHole, adds 50 ms of sample delay, plays to the configured output (currently matching "Digital"),
and estimates spectral stems. It never opens the Arduino. For Mac speakers:

```sh
python3 audio_playback.py --output-device "MacBook Pro Speakers"
```

Keep this process running while you work on LEDs. Do not run the old combined
process or virtualdj_audio_delay.py at the same time; those would duplicate audio.
A lock prevents two copies of this audio service from playing simultaneously.

## Terminal 2: start or restart LEDs as often as needed

```sh
python3 audio_led.py 50
```

This process only receives timestamped analysis and sends RGB frames at 500000
baud to the existing arduino_virtualdj firmware. It opens no audio stream. After
editing virtualdj_pixels.py, Ctrl+C **only in Terminal 2**, then run the command
again. Music in Terminal 1 keeps playing. LED startup may take a few seconds to
reset the Uno and refill the timing buffer. No Arduino upload is required.

Brightness is a percentage. Optional controls:

```sh
python3 audio_led.py 50 --led-lead-ms 30 --led-stats
```

LED lead is applied by the receiver, so changing it does not require restarting
music. Frame timestamps preserve alignment with the delayed audio. `--led-stats`
reports acknowledged host send FPS. The legacy `--darkness` option is ignored.
Use `--serial-port` to override Uno discovery.

If the audio service is absent, LEDs show the dim idle gradient. If the LED
process crashes or stops, music continues and Arduino's watchdog clears lights.
Restarted LEDs reconnect automatically to analysis broadcast on localhost UDP
19786; both commands accept `--analysis-port` to use another matching port.

## Delay and routing

Audio delay defaults to 50 ms added, plus input/output device latency,
especially Bluetooth. To change the delay, restart the **audio service** with
`--delay-ms 1000` (50–5000). Changes to audio device/capture/analysis code also
require restarting that service; normal LED effect edits do not.

Applications must send their output to BlackHole. Apps explicitly using physical
speakers bypass capture. Avoid direct/Multi-Output playback duplicates. The audio
service's `--audio-source virtualdj` bypasses the system-output check when an app
is explicitly routed to BlackHole; this still uses spectral estimates, not the
VirtualDJ plugin. Restore macOS output to physical speakers after stopping the
audio service if you want normal undelayed playback.

## Analysis and rendering

system_audio_stems.py estimates harmonic/percussive content using short-time
spectra and median masks. Low harmonics estimate bass; percussive low/high bands
estimate kick/hi-hat; centered harmonic midrange estimates vocals; remaining
harmonics estimate instruments. These are **spectral estimates, not AI-isolated
stems or VirtualDJ's native stem meters**. Centered instruments can look vocal,
off-center vocals can be missed, and dense mixes can confuse pitch. Playback
always uses the original stereo mix; analysis affects only LEDs.

The shared EnergyShowPixels renderer retains energy-based coverage, orange kick
and fallback beats, vocal-present note positioning, note colors, sparse beat-only
sparkle hops, sustained-energy pools, drop spectacles and idle gradients. Four
strips still mirror 300 pixels.

Audio callback work is limited to the sample delay and bounded capture queue.
The analysis worker broadcasts future control frames and note estimates with
monotonic playback timestamps and a service session/sequence. LED rendering,
serial I/O and acknowledgements live entirely in the other process. The LED
receiver validates and buffers snapshots, drops stale frames rather than replaying
old beats, and resets its queue on an audio-service restart.

Validation: all 89 Python tests pass, including a service-loop test that closes
and reconnects a UDP LED receiver while audio callbacks continue. Timing tests
verify exactly 48000 samples of added delay, callback timestamps, receiver lead,
sequence/session handling, stale-frame rejection and bounded queues. A synthetic
analysis benchmark measured about 1.9 ms per 21.3 ms audio block on this Mac;
this is not a live Bluetooth/USB timing measurement.

## Older Intel Mac / Python before 3.10

Audio-to-LED packets now use Unix playback timestamps, converted to the receiving
process's local monotonic clock. Older macOS Python versions can give different
monotonic clock origins to separate processes, causing the previous protocol's
valid analysis to be rejected. Both system_audio_led.py and system_led_receiver.py
must be updated; restart both processes once to use the new timestamps. No Arduino
upload is required. LED status now reports packets/accepted/rejected/expired to
distinguish missing UDP data from timing/validation rejection. 98 tests pass,
including a simulated process-clock offset.


For reduced music latency use `python3 audio_playback.py --delay-ms 50`.
This is 50 ms added sample delay, plus device latency. At short delays the LED
analysis/serial pipeline has less scheduling headroom: if late= rises or lighting
misses beats, try 100–200 ms or reduce the LED receiver's --led-lead-ms. Copy the
updated audio_led.py to other machines so their argument parser accepts 50 ms.

The default added audio delay is now 50 ms. Run audio_playback.py without a
delay argument to use it; an explicit --delay-ms value still overrides the default.
