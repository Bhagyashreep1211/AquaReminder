"""Shared synthesis helpers for the sound generators in this folder.

Build-time only — app.py never imports this. See make_water_sound.py and
make_cheer_sound.py for the actual cues.
"""

import array
import math
import os
import wave

RATE = 44100


def one_pole(samples, cutoff_hz, rate=RATE):
    """Simple one-pole low-pass filter."""
    out = [0.0] * len(samples)
    a = 1.0 - math.exp(-2.0 * math.pi * cutoff_hz / rate)
    y = 0.0
    for i, x in enumerate(samples):
        y += a * (x - y)
        out[i] = y
    return out


def high_pass(samples, cutoff_hz, rate=RATE):
    """Signal minus its own low end."""
    low = one_pole(samples, cutoff_hz, rate)
    return [samples[i] - low[i] for i in range(len(samples))]


def apply_fades(channels, fade_in, fade_out, rate=RATE, in_shape=1.7, out_shape=1.5):
    """Shape the head and tail so a cue never lands like an alarm."""
    n = len(channels[0])
    head = max(1, int(rate * fade_in))
    tail = max(1, int(rate * fade_out))
    for i in range(n):
        env = 1.0
        if i < head:
            env *= (i / head) ** in_shape
        if i > n - tail:
            env *= ((n - i) / tail) ** out_shape
        for channel in channels:
            channel[i] *= env


def write_wav(path, left, right, peak=0.34, rate=RATE):
    """Normalise to `peak` and write 16-bit stereo PCM."""
    loudest = max(max(abs(v) for v in left), max(abs(v) for v in right), 1e-9)
    scale = peak / loudest

    frames = array.array("h")
    for i in range(len(left)):
        for value in (left[i], right[i]):
            clipped = max(-1.0, min(1.0, value * scale))
            frames.append(int(clipped * 32767))

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(frames.tobytes())

    return os.path.getsize(path)
