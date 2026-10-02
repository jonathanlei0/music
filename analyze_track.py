#!/usr/bin/env python3
"""Deep offline music analysis for deterministic LED choreography."""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy.ndimage import gaussian_filter1d, median_filter
from scipy.signal import find_peaks


HOP = 512
N_FFT = 4096
BANDS = {
    "sub": (25, 70), "bass": (70, 180), "body": (180, 500),
    "mid": (500, 2000), "high": (2000, 6000), "air": (6000, 16000),
}
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def robust01(x, low=5, high=95):
    lo, hi = np.percentile(x, (low, high))
    return np.clip((x - lo) / max(hi - lo, 1e-9), 0, 1)


def frame_audio(y, frame_length=N_FFT, hop=HOP):
    count = 1 + (len(y) - frame_length) // hop
    return np.lib.stride_tricks.as_strided(
        y, shape=(count, frame_length),
        strides=(y.strides[0] * hop, y.strides[0]),
    )


def beat_track(onset, rate):
    """Estimate stable tempo and snap a regular grid to nearby transients."""
    centered = onset - np.mean(onset)
    correlation = np.correlate(centered, centered, mode="full")[len(centered) - 1:]
    min_lag, max_lag = int(rate * 60 / 190), int(rate * 60 / 60)
    lags = np.arange(min_lag, max_lag + 1)
    bpm_candidates = 60 * rate / lags
    prior = np.exp(-0.5 * (np.log2(bpm_candidates / 122) / 0.72) ** 2)
    lag = int(lags[np.argmax(correlation[lags] * prior)])
    bpm = float(60 * rate / lag)
    phase = int(np.argmax([np.sum(onset[p::lag]) for p in range(lag)]))
    radius = max(1, int(0.10 * rate))
    beats = []
    for prediction in np.arange(phase, len(onset), lag):
        a, b = max(0, prediction - radius), min(len(onset), prediction + radius + 1)
        beats.append(a + int(np.argmax(onset[a:b])))
    beats = np.unique(beats)
    return bpm, beats[onset[beats] > np.percentile(onset, 35)]


def section_map(features, frame_rate, duration):
    """Find persistent changes by contrasting four-second windows."""
    seconds = int(np.ceil(duration))
    x = np.zeros((seconds, features.shape[1]))
    for second in range(seconds):
        a, b = int(second * frame_rate), int((second + 1) * frame_rate)
        x[second] = np.mean(features[a:min(b, len(features))], axis=0)
    x = (x - np.mean(x, axis=0)) / (np.std(x, axis=0) + 1e-9)
    novelty = np.zeros(seconds)
    for i in range(4, seconds - 4):
        novelty[i] = np.linalg.norm(
            np.mean(x[i:i + 4], axis=0) - np.mean(x[i - 4:i], axis=0)
        )
    novelty = gaussian_filter1d(novelty, 1.1)
    # Absolute prominence is intentional here: percentile-based prominence
    # suppresses valid boundaries in consistently busy/mastered tracks.
    peaks, _ = find_peaks(novelty, distance=7, prominence=.38)
    if len(peaks) > 14:
        peaks = np.sort(peaks[np.argsort(novelty[peaks])[-14:]])
    return np.unique(np.r_[0, peaks, seconds]).astype(int), novelty


def label_section(mean):
    level, sub, bass, _body, mid, high, _air, onset, harmonic, _width = mean[:10]
    if level < 0.10:
        return "break/silence"
    if onset > 0.62 and sub + bass > 1.05:
        return "drop / peak"
    if onset > 0.48 and high > 0.48:
        return "driving"
    if harmonic > 0.60 and mid > bass:
        return "melodic"
    if level < 0.40:
        return "sparse / build"
    return "full groove"


def analyze(input_path, output_path, plot_path):
    stereo, sample_rate = sf.read(input_path, dtype="float32", always_2d=True)
    mono_full = np.mean(stereo, axis=1)
    gate = frame_audio(mono_full, 2048, 1024)
    gate_rms = np.sqrt(np.mean(gate ** 2, axis=1) + 1e-12)
    active = np.flatnonzero(gate_rms > 10 ** (-50 / 20))
    # Retain one second after the last real signal; never remove interior gaps.
    last_sample = min(len(mono_full), active[-1] * 1024 + 2048 + sample_rate)
    stereo = stereo[:last_sample]
    mono = np.mean(stereo, axis=1)
    duration = len(mono) / sample_rate

    frames = frame_audio(mono)
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(N_FFT), axis=1)).T + 1e-10
    power = spectrum ** 2
    freqs = np.fft.rfftfreq(N_FFT, 1 / sample_rate)
    times = (np.arange(spectrum.shape[1]) * HOP + N_FFT / 2) / sample_rate
    frame_rate = sample_rate / HOP

    # Harmonic/percussive source estimates using 2-D median filtering.
    harmonic_basis = median_filter(spectrum, size=(1, 17), mode="nearest")
    percussive_basis = median_filter(spectrum, size=(17, 1), mode="nearest")
    denominator = harmonic_basis ** 2 + percussive_basis ** 2 + 1e-12
    harmonic_mask = harmonic_basis ** 2 / denominator
    percussive_mask = percussive_basis ** 2 / denominator
    total_power = np.sum(power, axis=0) + 1e-12
    harmonicity = np.sum(power * harmonic_mask, axis=0) / total_power
    percussiveness = np.sum(power * percussive_mask, axis=0) / total_power

    band_norm = {}
    for name, (low, high) in BANDS.items():
        mask = (freqs >= low) & (freqs < high)
        raw = np.sqrt(np.mean(power[mask], axis=0))
        band_norm[name] = robust01(np.log1p(raw), 8, 97)

    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms)
    level = robust01(rms_db, 3, 97)
    log_spectrum = np.log1p(spectrum)
    positive_change = np.maximum(
        np.diff(log_spectrum, axis=1, prepend=log_spectrum[:, :1]), 0
    )
    flux = np.sqrt(np.mean(positive_change ** 2, axis=0))
    onset = robust01(gaussian_filter1d(flux, 1), 20, 99)

    left, right = frame_audio(stereo[:, 0]), frame_audio(stereo[:, 1])
    side = np.sqrt(np.mean(((left - right) * .5) ** 2, axis=1))
    middle = np.sqrt(np.mean(((left + right) * .5) ** 2, axis=1))
    width = np.clip(side / (middle + side + 1e-9), 0, 1)

    # Harmonic chroma: octaves reinforce pitch class while percussion is masked.
    chroma = np.zeros((12, spectrum.shape[1]))
    valid = (freqs >= 65) & (freqs <= 5000)
    midi = np.rint(69 + 12 * np.log2(freqs[valid] / 440)).astype(int)
    harmonic_power = power[valid] * harmonic_mask[valid]
    for pitch_class in range(12):
        chroma[pitch_class] = np.sum(
            harmonic_power[midi % 12 == pitch_class], axis=0
        )
    chroma /= np.sum(chroma, axis=0, keepdims=True) + 1e-9
    chroma = gaussian_filter1d(chroma, frame_rate * .18, axis=1)
    dominant_note = np.argmax(chroma, axis=0)
    chroma_certainty = np.max(chroma, axis=0)

    bpm, beat_frames = beat_track(onset, frame_rate)
    phrase_curve = np.maximum(np.diff(gaussian_filter1d(
        band_norm["mid"] * harmonicity, frame_rate * .12
    ), prepend=0), 0)
    phrases, _ = find_peaks(
        phrase_curve, distance=int(frame_rate * .55),
        prominence=np.percentile(phrase_curve, 76),
    )

    features = np.column_stack([
        level, band_norm["sub"], band_norm["bass"], band_norm["body"],
        band_norm["mid"], band_norm["high"], band_norm["air"], onset,
        harmonicity, width, chroma.T,
    ])
    boundaries, novelty = section_map(features, frame_rate, duration)
    # Long interior near-silence is an authored structural event. Preserve both
    # its entrance and its exit even if a novelty window blurs either edge.
    quiet = rms_db < -48
    changes = np.diff(np.r_[False, quiet, False].astype(np.int8))
    quiet_starts = np.flatnonzero(changes == 1)
    quiet_ends = np.flatnonzero(changes == -1)
    silence_edges = []
    for start, end in zip(quiet_starts, quiet_ends):
        if (end - start) / frame_rate >= .75 and times[start] > 1 and times[end - 1] < duration - 1:
            silence_edges.extend((round(times[start]), round(times[end - 1])))
    boundaries = np.unique(np.r_[boundaries, silence_edges]).astype(int)

    # A drop is an arrangement-level arrival, not merely a loud kick. Compare
    # several seconds on either side of each independently detected boundary,
    # then snap the event to the strongest nearby transient.
    bass_combo = .55 * band_norm["sub"] + .45 * band_norm["bass"]
    drops = []
    for boundary in boundaries[1:-1]:
        center = int(boundary * frame_rate)
        pre = slice(max(0, center - int(4 * frame_rate)), max(1, center - int(.3 * frame_rate)))
        post = slice(center, min(len(level), center + int(3 * frame_rate)))
        arrival = (
            np.mean(level[post]) - np.mean(level[pre])
            + .55 * (np.mean(bass_combo[post]) - np.mean(bass_combo[pre]))
            + .25 * (np.mean(onset[post]) - np.mean(onset[pre]))
        )
        # A structural boundary is deliberately coarse (one-second grid). A
        # drop belongs to the first strong arrival after it, never to the last
        # fill or vocal transient immediately before it.
        a, b = center, min(len(onset), center + int(.8 * frame_rate))
        transient = a + int(np.argmax(onset[a:b]))
        if arrival > .16 and onset[transient] > .25:
            drops.append(transient)
    drops = np.asarray(drops, dtype=int)
    sections = []
    for a, b in zip(boundaries[:-1], boundaries[1:]):
        ia, ib = int(a * frame_rate), min(len(times), int(b * frame_rate))
        mean = np.mean(features[ia:ib], axis=0)
        sections.append({
            "start": float(a), "end": round(float(min(b, duration)), 3),
            "label": label_section(mean), "level": round(float(mean[0]), 3),
            "bass": round(float((mean[1] + mean[2]) / 2), 3),
            "activity": round(float(mean[7]), 3),
            "harmonicity": round(float(mean[8]), 3),
            "width": round(float(mean[9]), 3),
            "dominant_pitch_class": NOTE_NAMES[int(np.argmax(
                np.mean(chroma[:, ia:ib], axis=1)
            ))],
        })

    timeline_rate = 20
    out_times = np.arange(0, duration, 1 / timeline_rate)
    indices = np.minimum((out_times * frame_rate).astype(int), len(times) - 1)
    beat_set = set(np.floor(times[beat_frames] * timeline_rate).astype(int))
    drop_set = set(np.floor(times[drops] * timeline_rate).astype(int))
    phrase_set = set(np.floor(times[phrases] * timeline_rate).astype(int))
    raw_boundary_set = set((boundaries[1:-1] * timeline_rate).astype(int))
    # Avoid a misleading double accent just before/after the same transition.
    boundary_set = {
        boundary for boundary in raw_boundary_set
        if all(abs(boundary - drop) > timeline_rate for drop in drop_set)
    }
    timeline = []
    for j, i in enumerate(indices):
        event = 3 if j in drop_set else 2 if j in boundary_set else 1 if j in beat_set else 0
        timeline.append({
            "t": round(float(out_times[j]), 3), "level": round(float(level[i]), 3),
            "sub": round(float(band_norm["sub"][i]), 3),
            "bass": round(float(band_norm["bass"][i]), 3),
            "body": round(float(band_norm["body"][i]), 3),
            "mid": round(float(band_norm["mid"][i]), 3),
            "high": round(float(band_norm["high"][i]), 3),
            "air": round(float(band_norm["air"][i]), 3),
            "onset": round(float(onset[i]), 3),
            "percussive": round(float(percussiveness[i]), 3),
            "harmonic": round(float(harmonicity[i]), 3),
            "width": round(float(width[i]), 3), "note": int(dominant_note[i]),
            "certainty": round(float(chroma_certainty[i]), 3),
            "event": event, "phrase": 1 if j in phrase_set else 0,
        })

    result = {
        "version": 1, "source": str(Path(input_path).resolve()),
        "sample_rate": sample_rate, "duration": round(duration, 3),
        "timeline_rate": timeline_rate, "tempo_bpm": round(bpm, 2),
        "beat_times": np.round(times[beat_frames], 3).tolist(),
        "drop_times": np.round(times[drops], 3).tolist(),
        "phrase_times": np.round(times[phrases], 3).tolist(),
        "sections": sections, "timeline": timeline,
    }
    Path(output_path).write_text(json.dumps(result, separators=(",", ":")))

    fig, axes = plt.subplots(5, 1, figsize=(16, 11), sharex=True)
    axes[0].plot(times, level, color="white", lw=.8); axes[0].set_ylabel("loudness")
    axes[1].plot(times, band_norm["sub"], label="sub", color="#ff5b36")
    axes[1].plot(times, band_norm["bass"], label="bass", color="#ffad33")
    axes[1].plot(times, band_norm["mid"], label="mid", color="#52e06f", alpha=.8)
    axes[1].legend(ncol=3, loc="upper right")
    axes[2].plot(times, onset, color="#35bfff", lw=.7)
    axes[2].vlines(times[beat_frames], 0, .5, color="white", alpha=.25, lw=.4)
    axes[2].vlines(times[drops], 0, 1, color="#ff2a72", lw=1.5)
    axes[2].set_ylabel("onsets")
    axes[3].imshow(chroma, aspect="auto", origin="lower", extent=[0, duration, 0, 12], cmap="hsv")
    axes[3].set_yticks(np.arange(12) + .5, NOTE_NAMES); axes[3].set_ylabel("harmony")
    axes[4].plot(np.arange(len(novelty)), novelty, color="#d685ff")
    axes[4].set_ylabel("section novelty"); axes[4].set_xlabel("seconds")
    for boundary in boundaries[1:-1]:
        for ax in axes:
            ax.axvline(boundary, color="#ffe263", alpha=.45, lw=.8)
    fig.patch.set_facecolor("#111111")
    for ax in axes:
        ax.set_facecolor("#111111"); ax.tick_params(colors="#dddddd")
        ax.yaxis.label.set_color("#dddddd"); ax.xaxis.label.set_color("#dddddd")
        for spine in ax.spines.values(): spine.set_color("#444444")
    fig.tight_layout(); fig.savefig(plot_path, dpi=150, facecolor=fig.get_facecolor())

    print(f"Analyzed {duration:.2f}s at {bpm:.2f} BPM")
    for section in sections:
        print(f"{section['start']:6.1f}-{section['end']:6.1f}  {section['label']:14s}  {section['dominant_pitch_class']}")
    print("Major drops:", ", ".join(f"{times[i]:.2f}" for i in drops))
    print(f"Wrote {output_path} and {plot_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", default="track_analysis.json")
    parser.add_argument("--plot", default="track_analysis.png")
    args = parser.parse_args()
    analyze(args.input, args.output, args.plot)


if __name__ == "__main__":
    main()
