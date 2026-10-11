"""Room acoustics, mic distance and device frequency responses.

Reverb and mic distance matter more than people expect: a caller on a
speakerphone two metres away in a tiled room is the single most common
"why did the bot mishear me" condition in production, and it is not
reproducible by adding hiss.
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sps

from ..audio import Audio, EPS, bandpass, db_to_lin, highpass, lowpass


def synth_rir(sr: int, rt60: float = 0.45, room_size: float = 4.0,
              drr_db: float = 6.0, rng: np.random.Generator | None = None,
              hf_damping: float = 0.55) -> np.ndarray:
    """Synthesise a plausible room impulse response.

    direct + sparse early reflections (delays from a shoebox of the given
    size) + exponentially decaying noise tail with faster HF decay.
    `drr_db` is the direct-to-reverberant ratio, which is what actually
    encodes "how far away is the talker".
    """
    rng = rng or np.random.default_rng(0)
    rt60 = max(0.05, float(rt60))
    n = int(sr * min(rt60 * 1.5 + 0.05, 3.0))
    rir = np.zeros(n, dtype=np.float32)
    rir[0] = 1.0

    c = 343.0
    # early reflections: first-order-ish image sources of a shoebox
    n_early = 12
    for _ in range(n_early):
        d = room_size * rng.uniform(0.35, 2.0)
        idx = int(d / c * sr)
        if 0 < idx < n:
            rir[idx] += rng.choice([-1.0, 1.0]) * (0.7 / (1.0 + d)) * rng.uniform(0.4, 1.0)

    # late tail: decaying noise, band-split so highs die first
    t = np.arange(n) / sr
    tail = rng.standard_normal(n).astype(np.float32)
    lf = lowpass(tail, sr, 1000, order=2) * np.exp(-6.9078 * t / rt60)
    hf = highpass(tail, sr, 1000, order=2) * np.exp(-6.9078 * t / (rt60 * hf_damping))
    late = (lf + hf).astype(np.float32)
    mix_start = int(sr * min(0.008 + room_size * 0.002, 0.05))
    ramp = np.clip((np.arange(n) - mix_start) / max(1, sr * 0.01), 0, 1).astype(np.float32)
    late *= ramp

    direct_e = float(np.sum(rir[:mix_start] ** 2)) + EPS
    late_e = float(np.sum(late ** 2)) + EPS
    scale = np.sqrt(direct_e / late_e / db_to_lin(drr_db) ** 2)
    rir = rir + late * scale
    return (rir / (np.max(np.abs(rir)) + EPS)).astype(np.float32)


def reverb(audio: Audio, rt60: float = 0.45, room_size: float = 4.0,
           drr_db: float = 6.0, wet: float = 1.0, seed: int = 0,
           preserve_length: bool = True) -> Audio:
    rng = np.random.default_rng(seed)
    rir = synth_rir(audio.sr, rt60, room_size, drr_db, rng)
    y = sps.fftconvolve(audio.samples, rir)[: len(audio) if preserve_length else None]
    y = y.astype(np.float32)
    # keep the level where it was; reverb should change timbre, not loudness
    ref = float(np.sqrt(np.mean(audio.samples.astype(np.float64) ** 2)) + EPS)
    cur = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)) + EPS)
    y *= ref / cur
    if wet < 1.0:
        m = min(len(y), len(audio))
        out = np.zeros(max(len(y), len(audio)), dtype=np.float32)
        out[:len(audio)] += audio.samples * (1 - wet)
        out[:len(y)] += y * wet
        y = out
    return audio.with_samples(y)


def mic_distance(audio: Audio, meters: float = 1.0, rt60: float = 0.45,
                 room_size: float = 4.0, seed: int = 0,
                 ref_m: float = 0.05) -> Audio:
    """Move the talker away from the mic.

    Two things happen together in a real room and only together do they
    sound right: the direct sound drops 6 dB per doubling of distance while
    the reverberant field stays put (so DRR falls), and the proximity bass
    boost of a close mic disappears.
    """
    meters = max(0.02, float(meters))
    drr = 6.0 - 20.0 * np.log10(meters / ref_m) * 0.5 + 8.0
    y = reverb(audio, rt60=rt60, room_size=room_size, drr_db=float(drr), seed=seed)
    x = y.samples
    if meters > 0.3:                          # lose the proximity effect
        prox = np.clip((meters - 0.3) / 1.5, 0, 1)
        x = (1 - 0.6 * prox) * x + 0.6 * prox * highpass(x, audio.sr, 150, order=1)
    # air absorption over distance
    if meters > 1.5:
        x = lowpass(x, audio.sr, max(4000, 12000 - meters * 900), order=1)
    gain = db_to_lin(-20.0 * np.log10(meters / ref_m) * 0.25)   # partial, keeps it audible
    return audio.with_samples((x * gain).astype(np.float32))


# --------------------------------------------------------------------------
# device / handset responses
# --------------------------------------------------------------------------
def _shape(x, sr, peaks, band=None, order=2):
    y = x
    if band:
        y = bandpass(y, sr, band[0], min(band[1], sr / 2 * 0.98), order=order)
    for f, q, g_db in peaks:
        if f >= sr / 2 * 0.98:
            continue
        w0 = f / (sr / 2)
        b, a = sps.iirpeak(w0, q) if g_db > 0 else sps.iirnotch(w0, q)
        filt = sps.lfilter(b, a, y).astype(np.float32)
        g = db_to_lin(abs(g_db))
        y = (y + (filt - y) * min(g - 1, 6)).astype(np.float32) if g_db > 0 else filt
    return y.astype(np.float32)


def device(audio: Audio, kind: str = "handset", seed: int = 0) -> Audio:
    """Colour the signal like a specific capture device."""
    x, sr = audio.samples, audio.sr
    rng = np.random.default_rng(seed)
    k = kind.lower()
    if k in ("handset", "phone"):
        y = _shape(x, sr, [(800, 1.0, 3), (3000, 2.0, 2)], band=(250, 3600))
    elif k in ("earbud", "headset"):
        y = _shape(x, sr, [(2500, 1.5, 3)], band=(120, 7500))
    elif k in ("cheap_headset", "usb_cheap"):
        y = _shape(x, sr, [(1200, 2.5, 4), (4500, 3.0, -6)], band=(200, 6000))
        y = y + highpass(rng.standard_normal(len(y)).astype(np.float32), sr, 1500, 2) * 0.0015
    elif k == "laptop":
        y = _shape(x, sr, [(500, 1.2, -4), (1800, 1.5, 3)], band=(150, 8000))
    elif k == "speakerphone":
        # boxy resonances + a comb from the enclosure + no bass at all
        y = bandpass(x, sr, 350, min(4500, sr / 2 * .98), order=2)
        d = int(sr * 0.0022)
        comb = np.zeros_like(y)
        comb[d:] = y[:-d]
        y = (y + 0.55 * comb).astype(np.float32)
        y = _shape(y, sr, [(900, 3.0, 5), (2200, 4.0, 4)])
    elif k in ("bluetooth", "hfp"):
        y = bandpass(x, sr, 200, min(3700, sr / 2 * .98), order=4)
        y = _shape(y, sr, [(1500, 1.5, 3)])
    elif k in ("conference", "room_mic"):
        y = _shape(x, sr, [(250, 1.0, -5), (3000, 1.5, 2)], band=(180, 7000))
    elif k in ("none", "flat", ""):
        return audio.copy()
    else:
        raise KeyError(f"unknown device '{kind}'")
    return audio.with_samples(y)


DEVICES = ["handset", "earbud", "cheap_headset", "laptop", "speakerphone",
           "bluetooth", "conference", "none"]
