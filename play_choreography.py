#!/usr/bin/env python3
"""Play a pre-analyzed track and its LED choreography sample-accurately."""

import argparse
import json
import queue
import threading
import time
from pathlib import Path

import numpy as np
import serial
import serial.tools.list_ports
import sounddevice as sd
import soundfile as sf


BAUD = 115200
OUTPUT_NAME = "AirPods Pro"
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def find_output_device(name):
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] >= 2 and name.lower() in device["name"].lower():
            return index
    raise RuntimeError(f"No stereo output device containing {name!r} was found")


def find_arduino_port():
    for port in serial.tools.list_ports.comports():
        if "arduino" in (port.description or "").lower() or "usbmodem" in port.device.lower():
            return port.device
    raise RuntimeError("Arduino was not found")


def byte(value):
    return int(np.clip(round(value * 255), 0, 255))


def serial_message(frame, bpm):
    hue = int(frame["note"] * 65535 / 12)
    payload = bytes((
        byte(frame["level"]), byte(frame["sub"]), byte(frame["bass"]),
        byte(frame["body"]), byte(frame["mid"]), byte(frame["high"]),
        byte(frame["air"]), hue & 0xFF, hue >> 8,
        frame["event"], frame["phrase"], int(round(bpm)),
        byte(frame["width"]), byte(frame["onset"]),
        byte(frame["harmonic"]), byte(frame["percussive"]),
    ))
    checksum = 0
    for value in payload:
        checksum = ((checksum << 1) | (checksum >> 7)) & 0xFF
        checksum ^= value
    return b"\xA5\x5A" + payload + bytes((checksum,))


def play(analysis_path, start, end, output_name, offset_ms, drop_offset_ms):
    analysis = json.loads(Path(analysis_path).read_text())
    source = Path(analysis["source"])
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    end = analysis["duration"] if end is None else min(end, analysis["duration"])
    if not 0 <= start < end:
        raise ValueError(f"Invalid playback range {start:.2f}..{end:.2f}")
    audio = audio[round(start * sample_rate):round(end * sample_rate)]
    frames = [f for f in analysis["timeline"] if start <= f["t"] < end]
    output_device = find_output_device(output_name)
    port = find_arduino_port()
    print(f"Audio: [{output_device}] {sd.query_devices(output_device)['name']}")
    print(f"LEDs:  {port}")
    print(f"Range: {start:.2f}s to {end:.2f}s; musical tempo {analysis['tempo_bpm']:.2f} BPM")

    ser = serial.Serial(port, BAUD, timeout=.1)
    # Opening an Uno resets it. A unique challenge-response proves that this
    # exact sketch instance is running; stale READY bytes cannot pass it.
    ser.reset_input_buffer()
    nonce = str(time.monotonic_ns())
    ping = f"PING,{nonce}\n".encode("ascii")
    expected_pong = f"PONG,{nonce}"
    ready_deadline = time.monotonic() + 6
    next_ping = 0.0
    while time.monotonic() < ready_deadline:
        now = time.monotonic()
        if now >= next_ping:
            ser.write(ping)
            next_ping = now + .2
        if ser.readline().decode(errors="replace").strip() == expected_pong:
            break
    else:
        ser.close()
        raise RuntimeError("Arduino did not report READY after reset")

    position = 0
    next_frame = 0
    transport_ready = threading.Event()
    transport_done = threading.Event()
    send_queue = queue.Queue()
    status_messages = []
    sent_event_counts = [0, 0, 0, 0]

    # Convert musical times to sample positions once. The callback then binds
    # every control frame to the Core Audio buffer that actually contains it.
    scheduled = []
    repeat_drop = 0
    for frame in frames:
        wire_frame = frame
        if frame["event"] == 3:
            repeat_drop = 2
        elif repeat_drop:
            wire_frame = dict(frame)
            wire_frame["event"] = 3
            wire_frame["phrase"] = 0
            repeat_drop -= 1
        event_offset = drop_offset_ms / 1000.0 if wire_frame["event"] == 3 else 0.0
        scheduled.append((
            round((frame["t"] - start + event_offset) * sample_rate),
            serial_message(wire_frame, analysis["tempo_bpm"]), wire_frame,
        ))
    scheduled.sort(key=lambda item: item[0])

    def serial_sender():
        last_status_second = -1
        while not transport_done.is_set() or not send_queue.empty():
            try:
                due, message, frame = send_queue.get(timeout=.05)
            except queue.Empty:
                continue
            remaining = due - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            ser.write(message)
            sent_event_counts[frame["event"]] += 1
            elapsed_second = int(frame["t"])
            if elapsed_second != last_status_second:
                last_status_second = elapsed_second
                event = ("", "BEAT", "SECTION", "DROP")[frame["event"]]
                print(
                    f"\r{frame['t']:6.1f}s  {NOTE_NAMES[frame['note']]:2s}  "
                    f"sub {byte(frame['sub']):3d}  mid {byte(frame['mid']):3d}  "
                    f"high {byte(frame['high']):3d}  {event:7s}",
                    end="", flush=True,
                )

    def callback(outdata, count, time_info, status):
        nonlocal position, next_frame
        if status:
            status_messages.append(str(status))
        block_start = position
        # Recalculate for every buffer. This follows Core Audio if its clock or
        # buffering jumps instead of letting later LEDs drift from the sound.
        dac_delay = max(0.0, time_info.outputBufferDacTime - time_info.currentTime)
        block_dac_time = time.monotonic() + dac_delay
        block_end = block_start + count
        while next_frame < len(scheduled) and scheduled[next_frame][0] < block_end:
            sample, message, frame = scheduled[next_frame]
            if sample >= block_start:
                within_block = (sample - block_start) / sample_rate
                send_queue.put((
                    block_dac_time + within_block + offset_ms / 1000.0,
                    message, frame,
                ))
            next_frame += 1
        transport_ready.set()
        available = min(count, len(audio) - position)
        if available > 0:
            outdata[:available] = audio[position:position + available]
            position += available
        if available < count:
            outdata[available:] = 0
            raise sd.CallbackStop

    try:
        sender_thread = threading.Thread(target=serial_sender, daemon=True)
        sender_thread.start()
        with sd.OutputStream(
            device=output_device, channels=2, samplerate=sample_rate,
            dtype="float32", blocksize=1024, latency="low", callback=callback,
        ) as stream:
            transport_ready.wait(timeout=3)
            if not transport_ready.is_set():
                raise RuntimeError("Audio output did not start")
            print(f"Measured output latency: {stream.latency * 1000:.1f} ms")
            print(f"LED calibration offset: {offset_ms:+.1f} ms")
            while stream.active:
                time.sleep(.02)
        transport_done.set()
        sender_thread.join(timeout=2)
        print("\nPlayback complete.")
        time.sleep(.1)
        acknowledgements = ser.read_all().decode(errors="replace").splitlines()
        drops_seen = acknowledgements.count("ACK_DROP")
        drops_off = acknowledgements.count("ACK_FLASH_OFF")
        print(f"Arduino drop acknowledgements: {drops_seen} on / {drops_off} off")
        print(
            "Host events sent: "
            f"beats={sent_event_counts[1]}, sections={sent_event_counts[2]}, "
            f"drops={sent_event_counts[3]}"
        )
        if status_messages:
            print("Audio status:", "; ".join(status_messages))
    finally:
        transport_done.set()
        try:
            zero_frame = {
                "level": 0, "sub": 0, "bass": 0, "body": 0, "mid": 0,
                "high": 0, "air": 0, "note": 0, "event": 0, "phrase": 0,
                "width": 0, "onset": 0, "harmonic": 0, "percussive": 0,
            }
            ser.write(serial_message(zero_frame, 90))
        finally:
            ser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float)
    parser.add_argument("--output", default=OUTPUT_NAME)
    parser.add_argument(
        "--offset-ms", type=float, default=0,
        help="LED offset relative to predicted acoustic output; positive delays LEDs",
    )
    parser.add_argument(
        "--drop-offset-ms", type=float, default=-450,
        help="Shift only major-drop markers; negative values move them earlier",
    )
    args = parser.parse_args()
    play(
        args.analysis, args.start, args.end, args.output,
        args.offset_ms, args.drop_offset_ms,
    )


if __name__ == "__main__":
    main()
