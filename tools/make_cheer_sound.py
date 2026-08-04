#!/usr/bin/env python3
"""Synthesise the celebration cue into assets/cheer.wav.

Plays when you log a glass and the buddy jumps. Aiming for "warm little
well-done", not "you cleared a level":

  * a rising pentatonic arpeggio of soft bell tones — pentatonic because any
    subset of it consonates, so the notes never clash as their tails overlap;
  * each tone is a fundamental plus a few partials, the higher ones decaying
    faster, which is what separates a struck bell from a bare sine beep;
  * a breath of high sparkle noise on the attacks, and a low dyad left ringing
    underneath so the ending resolves instead of stopping.

Deterministic: the RNG is seeded, so rebuilding gives byte-identical output.

    python3 tools/make_cheer_sound.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _audio import RATE, apply_fades, high_pass, write_wav

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "assets", "cheer.wav")

DURATION = 1.9
SEED = 20240804
PEAK = 0.32          # matched to the water cue so neither startles after the other

# C major pentatonic, climbing. The last note lands an octave up and rings on.
ARPEGGIO = (
    (0.000, 523.25, 0.85, 0.42),      # start seconds, Hz, gain, pan
    (0.080, 659.25, 0.85, 0.58),
    (0.160, 783.99, 0.90, 0.40),
    (0.250, 1046.50, 1.00, 0.60),
)
# Held under the landing note so the tail resolves rather than cutting off.
FINAL_DYAD = ((0.250, 261.63, 0.30, 0.5), (0.255, 392.00, 0.24, 0.5))

# Struck-bell timbre: partial ratio, relative level, extra decay multiplier.
PARTIALS = ((1.00, 1.00, 1.0), (2.00, 0.42, 1.5), (3.01, 0.20, 2.1), (4.18, 0.09, 2.8))


def add_tone(left, right, n, start_s, freq, gain, pan, decay=4.4):
    """One struck-bell note: partials decaying at their own rates."""
    start = int(start_s * RATE)
    length = min(n - start, int(RATE * 1.6))
    if length <= 0:
        return
    for ratio, level, decay_mult in PARTIALS:
        omega = 2.0 * math.pi * freq * ratio / RATE
        rate = decay * decay_mult
        for k in range(length):
            t = k / RATE
            envelope = math.exp(-rate * t)
            if envelope < 0.0008:
                break
            # 4 ms attack ramp keeps the onset from clicking.
            attack = min(1.0, t / 0.004)
            value = math.sin(omega * k) * envelope * attack * level * gain
            left[start + k] += value * (1.0 - pan)
            right[start + k] += value * pan


def add_sparkle(left, right, rng, n, start_s, gain=0.16, length_s=0.30):
    """Airy noise burst riding an attack — the glitter over the bell."""
    start = int(start_s * RATE)
    length = min(n - start, int(RATE * length_s))
    if length <= 0:
        return
    noise = [rng.uniform(-1.0, 1.0) for _ in range(length)]
    bright = high_pass(noise, 4200.0)
    for k in range(length):
        t = k / RATE
        value = bright[k] * math.exp(-16.0 * t) * gain
        pan = 0.5 + 0.35 * math.sin(2.0 * math.pi * 3.0 * t)
        left[start + k] += value * (1.0 - pan)
        right[start + k] += value * pan


def main():
    rng = random.Random(SEED)
    n = int(RATE * DURATION)
    left = [0.0] * n
    right = [0.0] * n

    for start, freq, gain, pan in ARPEGGIO:
        add_tone(left, right, n, start, freq, gain, pan)
        add_sparkle(left, right, rng, n, start)

    for start, freq, gain, pan in FINAL_DYAD:
        add_tone(left, right, n, start, freq, gain, pan, decay=2.1)

    apply_fades([left, right], fade_in=0.002, fade_out=0.55,
                in_shape=1.0, out_shape=1.4)

    size = write_wav(TARGET, left, right, peak=PEAK)
    print("wrote %s — %.1fs, %d Hz stereo, %.1f KB"
          % (TARGET, DURATION, RATE, size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
