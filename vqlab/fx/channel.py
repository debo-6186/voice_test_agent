"""Electrical / transmission-side impairments: level, AGC, clipping, hum,
band limiting, echo, AEC ducking, dropouts and spectral damage."""
from __future__ import annotations

import numpy as np
from scipy import signal as sps

from ..audio import (Audio, EPS, bandpass, db_to_lin, highpass, lin_to_db,
                     lowpass, rms_dbfs, set_active_level, speech_mask)


def gain(audio: Audio, db: float) -> Audio:
    return audio.with_samples(audio.samples * db_to_lin(db))


def level(audio: Audio, target_dbov: float = -26.0) -> Audio:
    """Normalise active speech level (ITU-T P.56). -26 dBov is the telephony norm."""
    return set_active_level(audio, target_dbov)


def quiet_talker(audio: Audio, target_dbov: float = -42.0) -> Audio:
    """The 'I can barely hear you' case: real low level, not just low gain."""
    return set_active_level(audio, target_dbov)


def bandlimit(audio: Audio, lo: float = 300.0, hi: float = 3400.0,
              order: int = 6) -> Audio:
    """Classic PSTN passband. Applied before codecs, like a real handset."""
    return audio.with_samples(bandpass(audio.samples, audio.sr, lo, hi, order=order))


def clip(audio: Audio, headroom_db: float = -3.0, soft: bool = False,
         drive_db: float = 0.0) -> Audio:
    """Overload the input stage. `drive_db` pushes into the ceiling."""
    x = audio.samples * db_to_lin(drive_db)
    ceil = db_to_lin(headroom_db)
    if soft:
        y = np.tanh(x / max(ceil, EPS)) * ceil
    else:
        y = np.clip(x, -ceil, ceil)
    return audio.with_samples(y.astype(np.float32))


def dc_offset(audio: Audio, offset: float = 0.02) -> Audio:
    return audio.with_samples((audio.samples + offset).astype(np.float32))


def hum(audio: Audio, freq: float = 50.0, level_db: float = -40.0,
        harmonics: int = 4) -> Audio:
    """Mains hum + harmonics, relative to the speech level."""
    sr = audio.sr
    t = np.arange(len(audio)) / sr
    y = np.zeros(len(audio), dtype=np.float32)
    for h in range(1, harmonics + 1):
        f = freq * h
        if f >= sr / 2:
            break
        y += (np.sin(2 * np.pi * f * t) / h).astype(np.float32)
    ref = rms_dbfs(audio.samples)
    y *= db_to_lin(ref + level_db - rms_dbfs(y))
    return audio.with_samples(audio.samples + y)


def agc(audio: Audio, target_dbfs: float = -18.0, attack_ms: float = 8.0,
        release_ms: float = 400.0, max_gain_db: float = 30.0,
        pumping: float = 0.0) -> Audio:
    """A mediocre automatic gain control.

    Slow release + high max gain is exactly how phone AGCs pump the room
    noise up between words, which is a failure mode worth reproducing:
    the noise floor rises during pauses and the VAD starts hallucinating.
    """
    sr = audio.sr
    x = audio.samples.astype(np.float64)
    a_a = np.exp(-1.0 / (sr * attack_ms / 1000.0))
    a_r = np.exp(-1.0 / (sr * release_ms / 1000.0))
    env = np.empty_like(x)
    e = 0.0
    ax = np.abs(x)
    for i in range(len(x)):
        c = a_a if ax[i] > e else a_r
        e = c * e + (1 - c) * ax[i]
        env[i] = e
    target = db_to_lin(target_dbfs)
    g = target / np.maximum(env, EPS)
    g = np.clip(g, 0.0, db_to_lin(max_gain_db))
    if pumping > 0:                          # extra gain wobble
        g *= 1.0 + pumping * np.sin(2 * np.pi * 1.7 * np.arange(len(x)) / sr)
    return audio.with_samples((x * g).astype(np.float32))


def compressor(audio: Audio, threshold_db: float = -24.0, ratio: float = 6.0,
               attack_ms: float = 5.0, release_ms: float = 120.0,
               makeup_db: float = 0.0) -> Audio:
    sr = audio.sr
    x = audio.samples.astype(np.float64)
    a_a = np.exp(-1.0 / (sr * attack_ms / 1000.0))
    a_r = np.exp(-1.0 / (sr * release_ms / 1000.0))
    ax = np.abs(x)
    env = np.empty_like(x)
    e = 0.0
    for i in range(len(x)):
        c = a_a if ax[i] > e else a_r
        e = c * e + (1 - c) * ax[i]
        env[i] = e
    lvl = 20 * np.log10(np.maximum(env, EPS))
    over = np.maximum(lvl - threshold_db, 0.0)
    g_db = -over * (1 - 1.0 / max(ratio, 1.0)) + makeup_db
    return audio.with_samples((x * 10 ** (g_db / 20)).astype(np.float32))


def echo(audio: Audio, delay_ms: float = 120.0, atten_db: float = -14.0,
         taps: int = 3, decay_db: float = -6.0, lowpass_hz: float = 2500.0) -> Audio:
    """Residual acoustic echo - the far-end voice leaking back, as it does
    when AEC fails on a speakerphone. Modelled here as delayed, filtered
    copies of the signal itself (near-end echo), which is what a loopback
    test hears."""
    sr = audio.sr
    y = audio.samples.astype(np.float32).copy()
    for k in range(1, taps + 1):
        d = int(sr * delay_ms / 1000.0 * k)
        if d >= len(y):
            break
        g = db_to_lin(atten_db + decay_db * (k - 1))
        tap = np.zeros_like(y)
        tap[d:] = audio.samples[:-d]
        if lowpass_hz:
            tap = lowpass(tap, sr, lowpass_hz, order=2)
        y += tap * g
    return audio.with_samples(y)


def half_duplex(audio: Audio, period_s: float = 1.7, duty: float = 0.35,
                duck_db: float = -22.0, ramp_ms: float = 25.0,
                seed: int = 0) -> Audio:
    """Aggressive echo suppression clipping the near-end talker.

    Reproduces the 'the bot talks over me and my words vanish' failure:
    chunks of speech are ducked hard while the far end is assumed active.
    """
    sr, n = audio.sr, len(audio)
    rng = np.random.default_rng(seed)
    g = np.ones(n, dtype=np.float32)
    t = 0.0
    while t < n / sr:
        if rng.random() < duty:
            d = rng.uniform(0.2, 0.9)
            i, j = int(t * sr), min(n, int((t + d) * sr))
            g[i:j] = db_to_lin(duck_db)
        t += period_s * rng.uniform(0.6, 1.4)
    k = max(1, int(sr * ramp_ms / 1000.0))
    g = np.convolve(g, np.ones(k, dtype=np.float32) / k, mode="same")
    return audio.with_samples(audio.samples * g)


def dropouts(audio: Audio, rate_per_min: float = 12.0, min_ms: float = 80.0,
             max_ms: float = 700.0, depth_db: float = -60.0,
             seed: int = 0, ramp_ms: float = 6.0) -> Audio:
    """Connection drop-outs: chunks of the call simply gone."""
    sr, n = audio.sr, len(audio)
    rng = np.random.default_rng(seed)
    g = np.ones(n, dtype=np.float32)
    count = int(round(rate_per_min * (n / sr) / 60.0))
    for _ in range(max(count, 0)):
        d = rng.uniform(min_ms, max_ms) / 1000.0
        i = int(rng.uniform(0, max(1, n - d * sr)))
        j = min(n, i + int(d * sr))
        g[i:j] = db_to_lin(depth_db)
    k = max(1, int(sr * ramp_ms / 1000.0))
    g = np.convolve(g, np.ones(k, dtype=np.float32) / k, mode="same")
    return audio.with_samples(audio.samples * g)


def spectral_holes(audio: Audio, n_notches: int = 3, depth_db: float = -25.0,
                   seed: int = 0) -> Audio:
    """Narrow notches - multipath / bad DSP / aggressive noise suppression."""
    rng = np.random.default_rng(seed)
    x = audio.samples
    sr = audio.sr
    for _ in range(n_notches):
        f = rng.uniform(300, min(3400, sr / 2 * 0.8))
        q = rng.uniform(3, 12)
        b, a = sps.iirnotch(f / (sr / 2), q)
        notched = sps.lfilter(b, a, x).astype(np.float32)
        w = min(1.0, db_to_lin(-depth_db) / 10)
        x = (x * (1 - w) + notched * w).astype(np.float32)
    return audio.with_samples(x)


def noise_gate(audio: Audio, threshold_db: float = -45.0, hold_ms: float = 60.0,
               ramp_ms: float = 12.0) -> Audio:
    """An over-eager noise gate that eats word beginnings and soft endings."""
    sr = audio.sr
    frame = max(1, int(sr * 0.010))
    x = audio.samples
    pad = (-len(x)) % frame
    f = np.concatenate([x, np.zeros(pad, dtype=np.float32)]).reshape(-1, frame)
    e = 10 * np.log10(np.mean(f.astype(np.float64) ** 2, axis=1) + EPS)
    open_ = e > threshold_db
    hold = max(1, int(hold_ms / 10))
    for i in range(len(open_)):
        if open_[i]:
            open_[i:i + hold] = True
    g = np.repeat(open_.astype(np.float32), frame)[: len(x)]
    k = max(1, int(sr * ramp_ms / 1000.0))
    g = np.convolve(g, np.ones(k, dtype=np.float32) / k, mode="same")
    return audio.with_samples(x * g)


def overcooked_ns(audio: Audio, strength: float = 0.7, seed: int = 0) -> Audio:
    """Over-aggressive noise suppression: musical noise + smeared consonants.

    Spectral-subtraction style, deliberately tuned too hot. This is what a
    lot of WebRTC stacks do to a noisy caller, and the resulting warbling
    artefacts confuse ASR more than the noise did.
    """
    sr = audio.sr
    n_fft, hop = 512, 128
    f, t, Z = sps.stft(audio.samples, sr, nperseg=n_fft, noverlap=n_fft - hop)
    mag, ph = np.abs(Z), np.angle(Z)
    noise_est = np.percentile(mag, 20, axis=1, keepdims=True)
    over = 1.0 + 4.0 * strength
    sub = np.maximum(mag - over * noise_est, 0.02 * mag)
    rng = np.random.default_rng(seed)
    flicker = 1.0 + strength * 0.8 * (rng.random(sub.shape).astype(np.float32) - 0.5)
    sub = sub * flicker
    _, y = sps.istft(sub * np.exp(1j * ph), sr, nperseg=n_fft, noverlap=n_fft - hop)
    y = np.asarray(y, dtype=np.float32)[: len(audio)]
    if len(y) < len(audio):
        y = np.pad(y, (0, len(audio) - len(y)))
    return audio.with_samples(y)


def crosstalk(audio: Audio, other: np.ndarray, level_db: float = -20.0) -> Audio:
    """Another conversation bleeding onto the line."""
    from ..audio import fit_length
    o = fit_length(other, len(audio))
    o = o * db_to_lin(rms_dbfs(audio.samples) + level_db - rms_dbfs(o))
    return audio.with_samples(audio.samples + o)
