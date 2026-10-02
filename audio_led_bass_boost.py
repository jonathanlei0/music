import argparse

import queue

import select

import sys

import termios

import threading

import time

import tty

from collections import deque



import numpy as np

import serial

import serial.tools.list_ports

import sounddevice as sd
from scipy.signal import sosfilt, tf2sos



from led_effects import layers_from_analysis

from section_palette import SectionPalette

from music_activity import ActivityDetector





# -----------------------------

# AUDIO AND HARDWARE

# -----------------------------



BAUD = 115200

SAMPLE_RATE = 48000

BLOCK_SIZE = 1024

SEND_RATE = 50



AUDIO_DEVICE_NAME = "BlackHole"

AUDIO_DEVICE_FALLBACK = 0

AUDIO_OUTPUT_DEVICE_NAME = "AirPods Pro"



# Audio is analyzed immediately, but heard five seconds later. LED events are

# scheduled for the same delayed instant, including output-device latency.

AUDIO_DELAY_MS = 5000.0

# Bass EQ applied only to audio sent to AirPods Pro. The analyzer continues
# to receive the original, un-EQ'd BlackHole signal.
BASS_BOOST_DB = 8.0
BASS_SHELF_HZ = 140.0
BASS_SHELF_Q = 0.707
OUTPUT_PREAMP_DB = -4.0



NUM_LEDS = 300

BASS_BAND = (80, 180)

BASS_FULL_SCALE = 35.0



# Estimate the dominant melodic pitch for foreground color. Full mixes can

# contain several notes; retain the last reliable note when confidence is low.

PITCH_LOW_MIDI = 52

PITCH_HIGH_MIDI = 88

PITCH_WINDOW_SIZE = 4096

PITCH_UPDATE_BLOCKS = 2

PITCH_MIN_CONFIDENCE = 1.45



SILENCE_DB = -48.0

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F",

              "F#", "G", "G#", "A", "A#", "B")





# -----------------------------

# SHARED STATE

# -----------------------------



running = threading.Event()

running.set()



analysis_queue = queue.Queue(maxsize=32)

visual_schedule = deque()

schedule_lock = threading.Lock()



# Updated from the live PortAudio stream once its true output latency is known.

visual_delay_seconds = AUDIO_DELAY_MS / 1000.0 + 0.2





# -----------------------------

# AUDIO DEVICES AND DELAY

# -----------------------------



def brightness_percent(value):

    try:

        percent = int(value)

    except ValueError:

        raise argparse.ArgumentTypeError("brightness must be an integer from 0 to 100")

    if not 0 <= percent <= 100:

        raise argparse.ArgumentTypeError("brightness must be from 0 to 100")

    return percent





def parse_args(argv=None):

    parser = argparse.ArgumentParser(description="Play audio with five-second LED look-ahead.")

    parser.add_argument(

        "brightness", nargs="?", type=brightness_percent, default=100,

        help="maximum LED brightness percentage, 0–100 (default: 100)",

    )

    parser.add_argument("--darkness", type=brightness_percent, default=65,

                        help="background dark-gap amount, 0–100 (default: 65)")

    return parser.parse_args(argv)





def configure_brightness(ser, percent):

    """Require acknowledgement so an ignored cap cannot go unnoticed."""

    configure_effect(ser, "BRIGHTNESS", percent)





def configure_effect(ser, name, value):

    command = f"{name},{value}\n".encode("ascii")

    expected = f"ACK_{name},{value}".encode("ascii")

    ser.reset_input_buffer()

    deadline = time.monotonic() + 5.0

    while time.monotonic() < deadline:

        # Discard partial commands if an LED transmission interrupted reception.

        ser.write(b"\n" + command)

        if ser.readline().strip() == expected:

            return

    raise RuntimeError(

        f"Arduino did not acknowledge {name.lower()} configuration. "

        "Upload the updated arduino/arduino.ino firmware and try again."

    )





def find_audio_device():

    for index, device in enumerate(sd.query_devices()):

        if device["max_input_channels"] < 1:

            continue

        if AUDIO_DEVICE_NAME.lower() in device["name"].lower():

            return index

    return AUDIO_DEVICE_FALLBACK





def find_audio_output_device():

    for index, device in enumerate(sd.query_devices()):

        if device["max_output_channels"] < 2:

            continue

        if AUDIO_OUTPUT_DEVICE_NAME.lower() in device["name"].lower():

            return index



    available = [

        device["name"]

        for device in sd.query_devices()

        if device["max_output_channels"] >= 2

    ]

    choices = "\n  ".join(available)

    raise RuntimeError(

        f"Audio output containing {AUDIO_OUTPUT_DEVICE_NAME!r} was not found.\n"

        f"Available stereo outputs:\n  {choices}"

    )





def require_blackhole_system_output():

    default_output = sd.default.device[1]

    if default_output is None or default_output < 0:

        raise RuntimeError("macOS has no default audio output")



    name = sd.query_devices(default_output)["name"]

    if AUDIO_DEVICE_NAME.lower() not in name.lower():

        raise RuntimeError(

            "Set System Settings -> Sound -> Output to 'BlackHole 2ch' "

            "before starting.\n"

            f"The current system output is {name!r}; leaving it selected would "

            "play both the direct and delayed audio."

        )





class BassBoost:
    """Stateful stereo low-shelf EQ for real-time AirPods playback."""

    def __init__(
        self,
        gain_db=BASS_BOOST_DB,
        frequency=BASS_SHELF_HZ,
        q=BASS_SHELF_Q,
        channels=2,
    ):
        # RBJ Audio EQ Cookbook low-shelf biquad.
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * np.pi * frequency / SAMPLE_RATE
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2.0 * q)
        sqrt_A = np.sqrt(A)

        b0 = A * ((A + 1.0) - (A - 1.0) * cos_w0 + 2.0 * sqrt_A * alpha)
        b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0)
        b2 = A * ((A + 1.0) - (A - 1.0) * cos_w0 - 2.0 * sqrt_A * alpha)
        a0 = (A + 1.0) + (A - 1.0) * cos_w0 + 2.0 * sqrt_A * alpha
        a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0)
        a2 = (A + 1.0) + (A - 1.0) * cos_w0 - 2.0 * sqrt_A * alpha

        b = np.array([b0, b1, b2], dtype=np.float64) / a0
        a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
        self.sos = tf2sos(b, a)

        # scipy.signal.sosfilt expects zi shaped as
        # (n_sections, 2, n_channels) when filtering axis 0.
        self.zi = np.zeros((self.sos.shape[0], 2, channels), dtype=np.float64)
        self.preamp = 10.0 ** (OUTPUT_PREAMP_DB / 20.0)

    def process(self, audio):
        processed, self.zi = sosfilt(
            self.sos,
            audio,
            axis=0,
            zi=self.zi,
        )
        processed *= self.preamp

        # Soft-limit peaks rather than allowing boosted bass to hard-clip.
        processed = np.tanh(processed)
        return processed.astype(np.float32, copy=False)


class AudioDelayLine:

    """Fixed sample-accurate audio delay without blocking the callback."""



    def __init__(self, delay_ms, channels=2):

        self.delay_frames = max(0, round(SAMPLE_RATE * delay_ms / 1000.0))

        self.blocks = deque()

        self.head_offset = 0



        if self.delay_frames:

            self.blocks.append(

                np.zeros((self.delay_frames, channels), dtype=np.float32)

            )



    def process(self, indata, outdata):

        if self.delay_frames == 0:

            outdata[:] = indata

            return



        self.blocks.append(indata.copy())

        written = 0



        while written < len(outdata):

            block = self.blocks[0]

            available = len(block) - self.head_offset

            count = min(available, len(outdata) - written)

            outdata[written:written + count] = block[

                self.head_offset:self.head_offset + count

            ]

            written += count

            self.head_offset += count



            if self.head_offset == len(block):

                self.blocks.popleft()

                self.head_offset = 0





# -----------------------------

# ARDUINO

# -----------------------------



def find_arduino_port():

    for port in serial.tools.list_ports.comports():

        description = (port.description or "").lower()

        device = port.device.lower()

        if "arduino" in description or "usbmodem" in device:

            return port.device

    return None





# -----------------------------

# MUSICAL ANALYSIS

# -----------------------------



def midi_frequency(midi_note):

    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))





def midi_name(midi_note):

    return f"{NOTE_NAMES[midi_note % 12]}{midi_note // 12 - 1}"





def band_mean(spectrum, frequencies, low, high):

    mask = (frequencies >= low) & (frequencies <= high)

    if not np.any(mask):

        return 0.0

    return float(np.mean(spectrum[mask]))





def detect_harmonic_note(samples):

    """Estimate dominant melodic pitch; this is not source separation."""

    centered = samples - np.mean(samples)

    windowed = centered * np.hanning(len(centered))

    spectrum = np.abs(np.fft.rfft(windowed))

    frequencies = np.fft.rfftfreq(len(windowed), 1.0 / SAMPLE_RATE)



    useful = spectrum[(frequencies >= 140.0) & (frequencies <= 6000.0)]

    # Suppress spectral leakage instead of awarding many weak harmonics enough

    # logarithmic weight to beat the actual melody's fundamental.

    noise_floor = max(float(np.percentile(useful, 40)),

                      float(np.max(useful)) * 0.015, 1e-9)

    candidates = np.arange(PITCH_LOW_MIDI, PITCH_HIGH_MIDI + 1)

    scores = np.zeros(len(candidates), dtype=np.float64)



    for candidate_index, midi_note in enumerate(candidates):

        fundamental = midi_frequency(int(midi_note))

        for harmonic in range(1, 6):

            harmonic_frequency = fundamental * harmonic

            if harmonic_frequency > 6000.0:

                break

            amplitude = float(np.interp(

                harmonic_frequency, frequencies, spectrum

            ))

            scores[candidate_index] += harmonic ** -1.2 * np.log1p(

                max(0.0, amplitude - noise_floor) / noise_floor

            )



    best_index = int(np.argmax(scores))

    best_midi = int(candidates[best_index])

    confidence = float(

        (scores[best_index] - np.median(scores))

        / (np.std(scores) + 1e-9)

    )

    return best_midi, confidence





def massive_drop(beat, db, bass, onset, threshold, db_history, bass_history, since_drop):

    if len(db_history) < 94 or len(bass_history) < 94:

        return False

    reference_db = float(np.mean(db_history[-94:]))

    reference_bass = float(np.mean(bass_history[-94:]))

    return bool(

        beat and since_drop >= 12.0

        and reference_db > SILENCE_DB  # Starting playback is not a drop.

        and db > -18.0 and db >= reference_db + 8.0

        and bass >= 0.94 and bass >= reference_bass + 0.25

        and onset >= max(1.8, threshold * 2.5)

    )





class MusicalAnalyzer:

    """Extracts mood and structural events from a continuous full mix."""



    def __init__(self):

        self.previous_db = -90.0

        self.previous_bass = 0.0

        self.previous_mid = 0.0

        self.previous_spectrum = None



        self.energy_history = deque(maxlen=1400)   # about 30 seconds

        self.onset_history = deque(maxlen=375)     # about 8 seconds

        self.bass_history = deque(maxlen=375)

        self.centroid_history = deque(maxlen=375)

        self.beat_times = deque(maxlen=64)



        self.last_beat_time = -10.0

        self.last_drop_time = -10.0

        self.last_phrase_time = -10.0

        self.beat_count = 0

        self.bpm = 90.0

        self.mood = 0.15



        self.pitch_buffer = np.zeros(PITCH_WINDOW_SIZE, dtype=np.float32)

        self.pitch_samples = 0

        self.pitch_blocks = 0

        self.note_midi = 64

        self.pending_note = None

        self.pending_count = 0

        self.pitch_confidence = 0.0



    def _update_pitch(self, audio, level):

        count = min(len(audio), PITCH_WINDOW_SIZE)

        self.pitch_buffer[:-count] = self.pitch_buffer[count:]

        self.pitch_buffer[-count:] = audio[-count:]

        self.pitch_samples = min(PITCH_WINDOW_SIZE, self.pitch_samples + count)

        self.pitch_blocks += 1



        if (level <= 0.03

                or self.pitch_samples < PITCH_WINDOW_SIZE

                or self.pitch_blocks < PITCH_UPDATE_BLOCKS):

            return



        self.pitch_blocks = 0

        note, confidence = detect_harmonic_note(self.pitch_buffer)

        self.pitch_confidence = confidence



        if confidence < PITCH_MIN_CONFIDENCE:

            self.pending_note = None

            self.pending_count = 0

            return



        if note == self.note_midi:

            self.pending_note = None

            self.pending_count = 0

        elif note == self.pending_note:

            self.pending_count += 1

        else:

            self.pending_note = note

            self.pending_count = 1



        if self.pending_count >= 2:

            self.note_midi = note

            self.pending_note = None

            self.pending_count = 0



    def _update_tempo(self):

        if len(self.beat_times) < 4:

            return



        intervals = np.diff(np.asarray(self.beat_times, dtype=np.float64))

        intervals = intervals[(intervals >= 0.28) & (intervals <= 1.2)]

        if len(intervals) < 3:

            return



        bpm = 60.0 / float(np.median(intervals[-12:]))

        while bpm < 70.0:

            bpm *= 2.0

        while bpm > 175.0:

            bpm *= 0.5

        self.bpm += (bpm - self.bpm) * 0.18



    def _update_mood(self, centroid):

        recent_seconds = 8.0

        beat_density = sum(

            1 for beat_time in self.beat_times

            if self.current_time - beat_time <= recent_seconds

        ) / recent_seconds



        tempo_score = np.clip((self.bpm - 82.0) / 58.0, 0.0, 1.0)

        rhythm_score = np.clip((beat_density - 0.55) / 1.55, 0.0, 1.0)

        bass_score = np.clip(np.mean(self.bass_history) if self.bass_history else 0, 0, 1)

        brightness_score = np.clip((centroid - 700.0) / 2400.0, 0.0, 1.0)



        target = (0.34 * tempo_score

                  + 0.34 * rhythm_score

                  + 0.20 * bass_score

                  + 0.12 * brightness_score)



        # About an eight-second time constant prevents a single drum fill from

        # changing the scene, while still allowing verse/chorus transitions.

        self.mood += (float(target) - self.mood) * 0.0027



    def process(self, audio, captured_at):

        self.current_time = captured_at

        audio = audio.astype(np.float32, copy=False)

        audio = audio - np.mean(audio)



        rms = max(float(np.sqrt(np.mean(audio ** 2))), 1e-9)

        db = 20.0 * np.log10(rms)

        level = float(np.clip((db - SILENCE_DB) / 36.0, 0.0, 1.0))

        level = level ** 0.72



        windowed = audio * np.hanning(len(audio))

        spectrum = np.abs(np.fft.rfft(windowed))

        frequencies = np.fft.rfftfreq(len(audio), 1.0 / SAMPLE_RATE)



        raw_bass = band_mean(spectrum, frequencies, *BASS_BAND)

        bass = float(np.clip(raw_bass / BASS_FULL_SCALE, 0.0, 1.0)) ** 0.5

        mid = band_mean(spectrum, frequencies, 180, 2200)



        total_spectrum = float(np.sum(spectrum)) + 1e-9

        treble_share = float(np.sum(spectrum[frequencies >= 3500])) / total_spectrum

        treble = int(np.clip(treble_share * 3.0, 0, 1) * level * 255)

        centroid = float(np.sum(frequencies * spectrum) / total_spectrum)



        normalized_spectrum = spectrum / total_spectrum

        if self.previous_spectrum is None:

            spectral_flux = 0.0

        else:

            spectral_flux = float(np.sum(np.maximum(

                normalized_spectrum - self.previous_spectrum, 0.0

            )))



        db_rise = max(0.0, db - self.previous_db)

        bass_rise = max(0.0, bass - self.previous_bass)

        mid_rise = max(0.0, mid - self.previous_mid)

        onset = db_rise / 10.0 + bass_rise * 1.7 + spectral_flux * 2.2



        onset_array = np.asarray(self.onset_history, dtype=np.float64)

        if len(onset_array) >= 32:

            onset_threshold = max(

                0.24,

                float(np.median(onset_array) + 1.45 * np.std(onset_array))

            )

        else:

            onset_threshold = 0.35



        beat = (onset > onset_threshold

                and level > 0.10

                and captured_at - self.last_beat_time >= 0.24)



        event = 0

        if beat:

            self.last_beat_time = captured_at

            self.beat_times.append(captured_at)

            self.beat_count += 1

            self._update_tempo()

            event = 2 if self.beat_count % 4 == 1 else 1



        recent_db = np.asarray(self.energy_history, dtype=np.float64)

        # Require an established musical context, a large loudness jump and

        # genuinely new bass energy. Ordinary kicks/downbeats are not drops.

        recent_bass = np.asarray(self.bass_history, dtype=np.float64)

        major_drop = massive_drop(

            beat, db, bass, onset, onset_threshold, recent_db, recent_bass,

            captured_at - self.last_drop_time,

        )

        if major_drop:

            self.last_drop_time = captured_at

            event = 3



        # Phrase accents catch large broadband/vocal entrances following a dip.

        # They are separate from beats, which is what allows a slower emphatic

        # lyric to flare once and fade before the next phrase.

        phrase = (

            db_rise >= 3.2

            and mid_rise > 0.20 * max(self.previous_mid, 1e-6)

            and captured_at - self.last_phrase_time >= 0.65

        )

        if phrase:

            self.last_phrase_time = captured_at



        self.energy_history.append(db)

        self.onset_history.append(onset)

        self.bass_history.append(bass)

        self.centroid_history.append(centroid)

        self._update_pitch(audio, level)

        self._update_mood(centroid)



        self.previous_db = db

        self.previous_bass = bass

        self.previous_mid = mid

        self.previous_spectrum = normalized_spectrum



        # Pitch class chooses foreground colors; the background palette is fixed.

        # Octave changes do not recolor the foreground.

        note_hue = int((self.note_midi % 12) * 65535 / 12)



        return {

            "level": int(level * 255),

            "bass": int(bass * 255),

            "treble": treble,

            "hue": note_hue,

            "mood": int(np.clip(self.mood, 0.0, 1.0) * 255),

            "event": event,

            "phrase": 1 if phrase else 0,

            "bpm": int(round(self.bpm)),

            "db": db,

            "note": midi_name(self.note_midi),

            "confidence": self.pitch_confidence,

            "onset": onset,

        }





# -----------------------------

# THREADS

# -----------------------------



def make_audio_callback(delay_line, bass_boost):

    def callback(indata, outdata, frames, time_info, status):

        if status:

            print(status, file=sys.stderr)



        delay_line.process(indata, outdata)

        # Boost bass only on the delayed signal sent to AirPods Pro.
        # Musical analysis below intentionally uses the original `indata`.
        outdata[:] = bass_boost.process(outdata)

        captured_at = time.monotonic()

        mono = np.mean(indata, axis=1).astype(np.float32)



        try:

            analysis_queue.put_nowait((captured_at, mono))

        except queue.Full:

            # Preserve real-time audio: discard stale analysis rather than

            # ever blocking the PortAudio callback.

            try:

                analysis_queue.get_nowait()

            except queue.Empty:

                pass

            try:

                analysis_queue.put_nowait((captured_at, mono))

            except queue.Full:

                pass



    return callback





def analysis_loop():

    analyzer = MusicalAnalyzer()

    palettes = SectionPalette()

    activity = ActivityDetector()



    while running.is_set() or not analysis_queue.empty():

        try:

            captured_at, audio = analysis_queue.get(timeout=0.1)

        except queue.Empty:

            continue



        frame = analyzer.process(audio, captured_at)

        frame["activity"], frame["change"] = activity.update(frame, captured_at)

        change = palettes.update(frame, captured_at)

        frame["palette"] = palettes.palette

        frame["palette_fast"] = palettes.fast

        due_at = captured_at + visual_delay_seconds



        with schedule_lock:

            if change is not None:

                start, palette, fast = change

                # Confirmation happens inside the five-second look-ahead.

                # Revise still-buffered frames so the fade starts at the audible

                # section boundary, not 2.5 seconds after it.

                transition_due = start + visual_delay_seconds

                for queued_due, queued_frame in visual_schedule:

                    if queued_due >= transition_due:

                        queued_frame["palette"] = palette

                        queued_frame["palette_fast"] = fast

            visual_schedule.append((due_at, frame))



            # Bound memory if an output device stalls.

            while len(visual_schedule) > 2000:

                visual_schedule.popleft()





def serial_sender_loop(ser):

    interval = 1.0 / SEND_RATE

    next_send = time.monotonic()

    current = {

        "level": 0, "bass": 0, "hue": 0, "mood": 0,

        "event": 0, "phrase": 0, "bpm": 90,

        "db": -99.0, "note": "--", "confidence": 0.0, "onset": 0.0,

    }

    last_print = 0.0



    while running.is_set():

        now = time.monotonic()

        event = 0

        phrase = 0



        with schedule_lock:

            while visual_schedule and visual_schedule[0][0] <= now:

                _, due_frame = visual_schedule.popleft()

                event = max(event, due_frame["event"])

                phrase = max(phrase, due_frame["phrase"])

                current = due_frame



        # Compose controls only from frames whose delayed playback time is due.

        message = layers_from_analysis(current, event, phrase).packet()



        try:

            ser.write(message)

        except serial.SerialException as error:

            print("\nSerial error:", error, file=sys.stderr)

            running.clear()

            return



        if now - last_print >= 0.1:

            last_print = now

            mood_name = (

                "romantic" if current.get("activity", 0) < 95

                else "hybrid" if current.get("activity", 0) <= 160

                else "club"

            )

            event_name = ("", "BEAT", "DOWNBEAT", "DROP")[event]

            print(

                f"\r{mood_name:8s}  "

                f"BPM {current['bpm']:3d}  "

                f"dB {current['db']:6.1f}  "

                f"Bass {current['bass']:3d}  "

                f"Note {current['note']:3s}  "

                f"Mood {current['mood']:3d}  "

                f"Activity {current.get('activity', 0)*100//255:3d}%  "

                f"Change {current.get('change', 0)*100//255:3d}%  "

                f"{event_name:8s} "

                f"{'PHRASE' if phrase else '      '}    ",

                end="",

                flush=True,

            )



        next_send += interval

        time.sleep(max(0.0, next_send - time.monotonic()))





def keyboard_loop():

    if not sys.stdin.isatty():

        return



    fd = sys.stdin.fileno()

    original = termios.tcgetattr(fd)



    try:

        tty.setcbreak(fd)

        while running.is_set():

            if not select.select([sys.stdin], [], [], 0.2)[0]:

                continue

            if sys.stdin.read(1).lower() == "q":

                running.clear()

    finally:

        termios.tcsetattr(fd, termios.TCSADRAIN, original)





# -----------------------------

# START

# -----------------------------



def main():

    global visual_delay_seconds



    args = parse_args()

    require_blackhole_system_output()

    input_device = find_audio_device()

    output_device = find_audio_output_device()

    port = find_arduino_port()



    if port is None:

        raise RuntimeError("Arduino not found")



    print("Connecting to:", port)

    print(f"Listening to: [{input_device}] {sd.query_devices(input_device)['name']}")

    print(f"Playing through: [{output_device}] {sd.query_devices(output_device)['name']}")

    print(f"Audio lookahead/delay: {AUDIO_DELAY_MS:.0f} ms")
    print(
        f"AirPods bass EQ: +{BASS_BOOST_DB:.1f} dB low shelf at "
        f"{BASS_SHELF_HZ:.0f} Hz; preamp {OUTPUT_PREAMP_DB:.1f} dB"
    )



    ser = serial.Serial(port, BAUD, timeout=1)

    delay_line = AudioDelayLine(AUDIO_DELAY_MS)
    bass_boost = BassBoost()



    analyzer_thread = threading.Thread(target=analysis_loop, daemon=True)

    sender_thread = threading.Thread(

        target=serial_sender_loop, args=(ser,), daemon=True

    )

    keyboard_thread = threading.Thread(target=keyboard_loop, daemon=True)



    # Start analysis before PortAudio so the initial five seconds are not

    # dropped while the Arduino completes its reset delay.

    analyzer_thread.start()



    try:

        with sd.Stream(

            device=(input_device, output_device),

            channels=(2, 2),

            samplerate=SAMPLE_RATE,

            blocksize=BLOCK_SIZE,

            dtype="float32",

            latency="low",

            callback=make_audio_callback(delay_line, bass_boost),

        ) as stream:

            visual_delay_seconds = (

                AUDIO_DELAY_MS / 1000.0 + stream.latency[1]

            )



            # Opening an Uno's serial port resets it. Keep delayed audio and

            # analysis flowing during its boot interval.

            time.sleep(2)



            configure_brightness(ser, args.brightness)

            configure_effect(ser, "DARKNESS", args.darkness)

            configure_effect(ser, "PALETTES", 1)

            configure_effect(ser, "ACTIVITY", 1)

            print(f"Maximum LED brightness: {args.brightness}% (also current-limited)")

            print(f"Layers: gradient background + foreground accents; darkness {args.darkness}%")





            print(f"Output-device latency: {stream.latency[1] * 1000:.1f} ms")

            print(f"LED schedule delay: {visual_delay_seconds * 1000:.1f} ms")

            print("Press 'q' to quit.")



            sender_thread.start()

            keyboard_thread.start()



            while running.is_set():

                time.sleep(0.1)



    except KeyboardInterrupt:

        pass

    finally:

        running.clear()

        analyzer_thread.join(timeout=1.0)

        if sender_thread.is_alive():

            sender_thread.join(timeout=1.0)

        if keyboard_thread.is_alive():

            keyboard_thread.join(timeout=1.0)



        try:

            ser.write(b"0,0,0,0,0,0,90,0\n")

            ser.flush()

            time.sleep(0.1)

        except serial.SerialException:

            pass



        ser.close()

        print("\nStopped.")





if __name__ == "__main__":

    main()
