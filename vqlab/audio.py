"""Core audio I/O and signal utilities.

Everything in vqlab works on float32 mono numpy arrays in the range [-1, 1]
plus an integer sample rate. WAV I/O uses the stdlib so the only hard
dependency is numpy/scipy.
"""
from __future__ import annotations

import io
import os
import wave
import struct
import subprocess
import tempfile
from dataclasses import dataclass, replace

import numpy as np
from scipy import signal as sps

EPS = 1e-12


# --------------------------------------------------------------------------
# container
# --------------------------------------------------------------------------
@dataclass
class Audio:
    """Mono float32 signal + sample rate."""

    samples: np.ndarray
    sr: int

    def __post_init__(self):
        x = np.asarray(self.samples, dtype=np.float32)
        if x.ndim > 1:                       # downmix if someone hands us stereo
            x = x.mean(axis=1 if x.shape[0] > x.shape[1] else 0)
        self.samples = np.ascontiguousarray(x, dtype=np.float32)

    # -- convenience ------------------------------------------------------
    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.sr)

    def __len__(self) -> int:
        return len(self.samples)

    def copy(self) -> "Audio":
        return Audio(self.samples.copy(), self.sr)

    def with_samples(self, x: np.ndarray) -> "Audio":
        return Audio(x, self.sr)

    def resample(self, sr: int) -> "Audio":
        if sr == self.sr:
            return self.copy()
        return Audio(resample_to(self.samples, self.sr, sr), sr)


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------
def read_wav(path: str) -> Audio:
    """Read a PCM WAV file. Anything else is routed through ffmpeg."""
    try:
        with wave.open(path, "rb") as w:
            n_ch = w.getnchannels()
            width = w.getsampwidth()
            sr = w.getframerate()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return read_any(path)

    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:                         # 8-bit wav is unsigned
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    else:
        return read_any(path)

    if n_ch > 1:
        x = x.reshape(-1, n_ch).mean(axis=1)
    return Audio(x, sr)


def read_any(path: str, sr: int | None = None) -> Audio:
    """Decode any ffmpeg-readable file (mp3, m4a, ogg, ulaw, ...) to mono float."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", "a:0", "-ac", "1", "-f", "f32le"]
    if sr:
        cmd += ["-ar", str(sr)]
    cmd += ["-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(out, dtype="<f4").copy()
    if sr is None:
        sr = probe_sample_rate(path)
    return Audio(x, sr)


def load(path: str) -> Audio:
    return read_wav(path) if path.lower().endswith(".wav") else read_any(path)


def probe_sample_rate(path: str) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return int(out.splitlines()[0])


def write_wav(path: str, audio: Audio, bits: int = 16, dither: bool = False) -> str:
    """Write 16- or 24-bit PCM WAV, hard-limiting anything above full scale."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    x = np.asarray(audio.samples, dtype=np.float64)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 1.0:                           # never let a chain clip on export
        x = x / peak * 0.999
    if bits == 16:
        if dither:
            x = x + (np.random.random(x.shape) - np.random.random(x.shape)) / 65536.0
        data = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2").tobytes()
        width = 2
    elif bits == 24:
        v = np.clip(np.round(x * 8388607.0), -8388608, 8388607).astype(np.int32)
        b = np.stack([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF], axis=1).astype(np.uint8)
        data = b.tobytes()
        width = 3
    else:
        raise ValueError("bits must be 16 or 24")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(audio.sr)
        w.writeframes(data)
    return path


# --------------------------------------------------------------------------
# resampling / filtering
# --------------------------------------------------------------------------
def resample_to(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return np.asarray(x, dtype=np.float32)
    g = np.gcd(int(sr_in), int(sr_out))
    up, down = int(sr_out // g), int(sr_in // g)
    if max(up, down) > 2000:                 # awkward ratio -> polyphase is slow
        n_out = int(round(len(x) * sr_out / sr_in))
        return sps.resample(x, n_out).astype(np.float32)
    return sps.resample_poly(x, up, down).astype(np.float32)


def butter_filter(x: np.ndarray, sr: int, cutoff, btype: str, order: int = 4) -> np.ndarray:
    nyq = sr / 2.0
    if isinstance(cutoff, (list, tuple)):
        lo, hi = cutoff
        wn = [max(lo, 1.0) / nyq, min(hi, nyq * 0.999) / nyq]
        if wn[1] <= wn[0]:
            return x.astype(np.float32)
    else:
        wn = min(max(float(cutoff), 1.0), nyq * 0.999) / nyq
    sos = sps.butter(order, wn, btype=btype, output="sos")
    return sps.sosfilt(sos, x).astype(np.float32)


def highpass(x, sr, f, order=4):
    return butter_filter(x, sr, f, "highpass", order)


def lowpass(x, sr, f, order=4):
    return butter_filter(x, sr, f, "lowpass", order)


def bandpass(x, sr, lo, hi, order=4):
    return butter_filter(x, sr, (lo, hi), "bandpass", order)


# --------------------------------------------------------------------------
# levels
# --------------------------------------------------------------------------
def db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def lin_to_db(v: float) -> float:
    return float(20.0 * np.log10(max(abs(v), EPS)))


def rms_dbfs(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    return float(10.0 * np.log10(float(np.mean(np.square(x, dtype=np.float64))) + EPS))


def peak_dbfs(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    return lin_to_db(float(np.max(np.abs(x))))


def active_speech_level(x: np.ndarray, sr: int, margin_db: float = 15.9) -> tuple[float, float]:
    """ITU-T P.56 style active speech level.

    Returns (active level dBov, activity factor 0-1). Unlike plain RMS this
    ignores the silence between words, which is what telephony level specs
    (and every AGC you will ever fight) actually care about.
    """
    if len(x) == 0:
        return -120.0, 0.0
    # envelope with 30 ms exponential smoothing, as in P.56 Annex A
    g = np.exp(-1.0 / (sr * 0.030))
    env = np.abs(x).astype(np.float64)
    q = sps.lfilter([1.0 - g], [1.0, -g], env)
    long_term = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + EPS)
    thresholds = long_term * (2.0 ** np.arange(-20, 1))
    best = (-120.0, 0.0)
    for t in thresholds:
        act = q > t
        n = int(np.count_nonzero(act))
        if n < max(8, 0.005 * len(x)):
            continue
        a_lvl = 10.0 * np.log10(float(np.sum(np.square(x[act], dtype=np.float64))) / n + EPS)
        af = n / float(len(x))
        best = (a_lvl, af)
        if a_lvl - 20.0 * np.log10(t + EPS) > margin_db:
            break
    return best


def set_active_level(audio: Audio, target_dbov: float) -> Audio:
    """Scale so the *speech* sits at target_dbov (e.g. -26 dBov, the ITU norm)."""
    lvl, _ = active_speech_level(audio.samples, audio.sr)
    gain = db_to_lin(target_dbov - lvl)
    return audio.with_samples(audio.samples * gain)


def set_rms(audio: Audio, target_dbfs: float) -> Audio:
    gain = db_to_lin(target_dbfs - rms_dbfs(audio.samples))
    return audio.with_samples(audio.samples * gain)


# --------------------------------------------------------------------------
# VAD (used for SNR placement and for metrics)
# --------------------------------------------------------------------------
def speech_mask(x: np.ndarray, sr: int, frame_ms: float = 20.0,
                rel_db: float = -35.0) -> np.ndarray:
    """Cheap energy VAD -> per-sample boolean mask of speech-active regions."""
    n = max(1, int(sr * frame_ms / 1000.0))
    pad = (-len(x)) % n
    f = np.concatenate([x, np.zeros(pad, dtype=x.dtype)]).reshape(-1, n)
    e = 10.0 * np.log10(np.mean(np.square(f.astype(np.float64)), axis=1) + EPS)
    thr = np.max(e) + rel_db
    act = e > thr
    # 1-frame dilation so we keep onsets/offsets
    act = act | np.roll(act, 1) | np.roll(act, -1)
    mask = np.repeat(act, n)[: len(x)]
    return mask


def speech_rms(x: np.ndarray, sr: int) -> float:
    m = speech_mask(x, sr)
    seg = x[m] if np.any(m) else x
    return rms_dbfs(seg)


# --------------------------------------------------------------------------
# misc helpers
# --------------------------------------------------------------------------
def fit_length(x: np.ndarray, n: int, rng: np.random.Generator | None = None) -> np.ndarray:
    """Loop/crop a signal to exactly n samples with a crossfade at the seam."""
    if len(x) == 0:
        return np.zeros(n, dtype=np.float32)
    if len(x) >= n:
        start = 0 if rng is None else int(rng.integers(0, len(x) - n + 1))
        return np.asarray(x[start:start + n], dtype=np.float32)
    xf = min(len(x) // 4, 2048)
    out = np.zeros(n, dtype=np.float32)
    pos = 0
    body = np.asarray(x, dtype=np.float32)
    while pos < n:
        take = min(len(body), n - pos)
        chunk = body[:take].copy()
        if pos > 0 and xf > 0 and take > xf:
            ramp = np.linspace(0, 1, xf, dtype=np.float32)
            out[pos:pos + xf] = out[pos:pos + xf] * (1 - ramp) + chunk[:xf] * ramp
            out[pos + xf:pos + take] = chunk[xf:take]
        else:
            out[pos:pos + take] = chunk
        pos += take - (xf if take == len(body) else 0)
        if take < len(body):
            break
    return out[:n]


def mix_at_snr(speech: np.ndarray, noise: np.ndarray, sr: int, snr_db: float) -> np.ndarray:
    """Add noise scaled so the *active speech* to noise ratio equals snr_db."""
    if len(noise) == 0:
        return speech
    noise = fit_length(noise, len(speech))
    s_db = speech_rms(speech, sr)
    n_db = rms_dbfs(noise)
    gain = db_to_lin(s_db - snr_db - n_db)
    return (speech + noise * gain).astype(np.float32)


def pad(audio: Audio, head_s: float = 0.0, tail_s: float = 0.0) -> Audio:
    h = np.zeros(int(head_s * audio.sr), dtype=np.float32)
    t = np.zeros(int(tail_s * audio.sr), dtype=np.float32)
    return audio.with_samples(np.concatenate([h, audio.samples, t]))


def fade(audio: Audio, ms: float = 10.0) -> Audio:
    n = min(int(audio.sr * ms / 1000.0), len(audio) // 2)
    if n <= 0:
        return audio
    x = audio.samples.copy()
    r = np.linspace(0, 1, n, dtype=np.float32)
    x[:n] *= r
    x[-n:] *= r[::-1]
    return audio.with_samples(x)
