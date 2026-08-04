#!/usr/bin/env python3
"""Synthesise the default 'soft water pouring' cue into assets/water_pour.wav.

Generated rather than shipped as a downloaded clip so the repo stays
self-contained and the sound is tunable in one place. Two ingredients, which is
roughly what pouring water is acoustically:

  * a filtered noise bed — the stream itself, with a wandering filter so it
    moves instead of sitting there as flat hiss;
  * bubbles — each one a short sine whose pitch *rises* as it decays. That
    rising chirp is the giveaway your ear reads as "water" rather than "static".

Deterministic: the RNG is seeded, so rebuilding gives byte-identical output.

    python3 tools/make_water_sound.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _audio import RATE, apply_fades, high_pass, write_wav

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
TARGET = os.path.join(ASSETS, "water_pour.wav")

DURATION = 2.4
SEED = 20240804

PEAK = 0.34          # gentle on purpose — this interrupts people at their desk
FADE_IN = 0.18
FADE_OUT = 0.75
BUBBLES = 46


def stream_bed(rng, n):
    """Noise band-passed into the 'water' register, with a drifting cutoff."""
    noise = [rng.uniform(-1.0, 1.0) for _ in range(n)]

    # High-pass = signal minus its own low end. Kills the rumble that would make
    # the cue sound like wind rather than water.
    high = high_pass(noise, 320.0)

    # Wandering low-pass: two slow LFOs so the brightness breathes instead of
    # sitting still as flat hiss.
    out = [0.0] * n
    y = 0.0
    for i in range(n):
        t = i / RATE
        cutoff = (1500.0
                  + 700.0 * math.sin(2.0 * math.pi * 0.45 * t)
                  + 380.0 * math.sin(2.0 * math.pi * 1.30 * t + 1.1))
        a = 1.0 - math.exp(-2.0 * math.pi * max(200.0, cutoff) / RATE)
        y += a * (high[i] - y)
        out[i] = y
    return out


def add_bubble(buf_l, buf_r, n, start, f0, decay, gain, pan):
    """One bubble: decaying sine, pitch sweeping upward as it collapses."""
    length = int(RATE * min(0.42, 5.0 / decay))
    phase = 0.0
    for k in range(length):
        i = start + k
        if i >= n:
            break
        t = k / RATE
        freq = f0 * (1.0 + 2.6 * t)          # the rising chirp
        phase += 2.0 * math.pi * freq / RATE
        value = math.sin(phase) * math.exp(-decay * t) * gain
        buf_l[i] += value * (1.0 - pan)
        buf_r[i] += value * pan


def main():
    rng = random.Random(SEED)
    n = int(RATE * DURATION)

    bed = stream_bed(rng, n)
    left = [v * 0.55 for v in bed]
    right = [v * 0.55 for v in bed]

    # Decorrelate the channels slightly so the bed feels wide, not centred.
    shift = int(RATE * 0.011)
    right = right[shift:] + [0.0] * shift

    # Bubbles cluster toward the middle of the pour, thinning at both ends.
    for _ in range(BUBBLES):
        start = int(rng.betavariate(2.2, 2.2) * n)
        add_bubble(
            left, right, n, start,
            f0=rng.uniform(520.0, 2100.0),
            decay=rng.uniform(28.0, 74.0),
            gain=rng.uniform(0.05, 0.17),
            pan=rng.uniform(0.25, 0.75),
        )

    # Soft attack and a long tail so it never reads as an alarm.
    apply_fades([left, right], FADE_IN, FADE_OUT)

    size = write_wav(TARGET, left, right, peak=PEAK)
    print("wrote %s — %.1fs, %d Hz stereo, %.1f KB"
          % (TARGET, DURATION, RATE, size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
