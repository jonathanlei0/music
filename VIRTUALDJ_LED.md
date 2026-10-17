# VirtualDJ-native stem lighting

This version reads **VirtualDJ's own stem level meters**, not separated audio
waveforms. It performs no AI separation, audio capture, or playback. There is
no five-second buffer. Normal VirtualDJ, audio-device, UDP polling, serial, and
LED-rendering latency still applies; Bluetooth listening can lag behind lights.

## Start

1. Restart VirtualDJ after installing the bridge. Select **LED Stem Bridge** in
   an audio effect slot and activate it. Enable just one instance; it reads all
   four decks. This is a manual SoundEffect plugin, not an AutoStart plugin.
2. Set VirtualDJ's Master audio output directly to the speakers/headphones you
   listen through. Do not run `audio_led.py` alongside this version.
3. Load a song and prepare/enable its stems in VirtualDJ.
4. Check the bridge without touching the Arduino:

   ```sh
   python3 virtualdj_led.py --monitor
   ```

   You should see changing `vocal`, `instru`, `bass`, `kick`, and `hihat` readings.
   Quit with Ctrl+C, then run:

   ```sh
   python3 virtualdj_led.py 50
   ```

The brightness argument and `--darkness` retain the earlier meanings. No Arduino
firmware changes are required. Python uses the existing project venv if needed.

## What drives the lights

- VirtualDJ beat-grid crossings trigger beat pulses, including quieter beats.
- Bass stem level sets bass emphasis; hi-hat level sets sparkle intensity.
- Vocal entrances trigger phrase accents.
- Instrument and rhythm levels drive activity and slow color movement.
- Large sustained-context energy jumps can trigger a drop, with a 12-second cooldown.

These are meters, **not individual melody notes**. This version does not claim
note-following colors. Separate stem audio or a pitch-data source would be needed
for that. Queries are per deck; deck volume/crossfader gain weights their levels,
and the loudest audible deck supplies tempo. The master meter gates overall
brightness. Stem meter behavior under individual stem mutes/effects still needs
checking in the installed VirtualDJ version.

## Bridge

`virtualdj_bridge/StemBridge.cpp` is a read-only macOS plugin built against the
vendor SDK. While enabled, every 20 ms on the main run loop it queries four decks and sends a
bounded JSON packet to **127.0.0.1:19785**. It never changes audio or sends DJ
commands. Python discards packets older than 250 ms, drains queued snapshots,
and sends silence if updates stop. Beat events are not replayed after seeks.

Build/install on this Mac:

```sh
python3 virtualdj_bridge/build.py --install
```

The universal bundle is installed in the VirtualDJ user folder under
`PluginsMacArm/SoundEffect` and `Plugins64/SoundEffect`. Restart VirtualDJ after an
update. To uninstall, quit VirtualDJ and remove `LEDStemBridge.bundle` from those
two folders. The installer moves the previous AutoStart copies into
`LEDStemBridge-backups` inside the VirtualDJ user folder.

Validation: Python packet/mapping tests and a native mock-host test cover stale
messages, silent/muted decks, seeks, plugin loading/unloading, and UDP publishing.
A mock host is not a substitute for checking real VirtualDJ meter readings:
use `--monitor` after restarting. If the plugin does not load or queries are
unsupported, the monitor will report missing data instead of inventing stems.

References:
- https://virtualdj.com/manuals/virtualdj/appendix/vdjscriptverbs.html (`get_level`, `get_volume`, `get_beatpos`)
- https://virtualdj.com/wiki/Developers.html (native plugin SDK)
