"""Background ambience generators.

Two sources of noise are supported:

1. **Synthetic** - everything below. No downloads, deterministic per seed,
   and each generator is built from the physical ingredients of the real
   thing (engine harmonics, passing-car doppler swells, cutlery transients,
   babble built from speech-shaped modulated streams) rather than a flat
   hiss, because noise suppressors and VADs respond to structure, not to
   average power.
2. **Real recordings** - point `noise_dir` at MUSAN / DEMAND / FreeSound /
   your own call recordings and reference them by filename. Real noise is
   always the better reference; the synthetic set exists so the suite runs
   anywhere with no assets.
"""
from __future__ import annotations

import os
import glob

import numpy as np
from scipy import signal as sps

from ..audio import (Audio, EPS, bandpass, db_to_lin, fit_length, highpass,
                     load, lowpass, rms_dbfs)

# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------


def _white(n, rng):
    return rng.standard_normal(n).astype(np.float32)


def _pink(n, rng):
    """1/f noise via Voss-McCartney-ish IIR shaping."""
    x = _white(n + 4096, rng)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1.0, -2.494956002, 2.017265875, -0.522189400]
    y = sps.lfilter(b, a, x)[4096:]
    return _norm(y)


def _brown(n, rng):
    """1/f^2 - rumble, traffic, wind, HVAC bodies."""
    x = _white(n + 4096, rng)
    y = sps.lfilter([1.0], [1.0, -0.99], x)[4096:]
    return _norm(y)


def _norm(x):
    x = np.asarray(x, dtype=np.float32)
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))) + EPS)
    return (x / r).astype(np.float32)


def speech_shaped(n, sr, rng):
    """Noise with the long-term average speech spectrum (ITU-T P.50-ish).

    Flat to ~500 Hz then rolling off ~10 dB/decade. This is what makes
    babble sound like people instead of hiss, and it is the noise type that
    hurts ASR most because it lives exactly where speech lives.
    """
    x = _white(n, rng)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    mag = np.ones_like(f)
    lo = f < 100
    mid = (f >= 100) & (f < 500)
    hi = f >= 500
    mag[lo] = np.maximum(f[lo] / 100.0, 1e-3) ** 1.5
    mag[mid] = 1.0
    mag[hi] = (500.0 / np.maximum(f[hi], 1.0)) ** 1.0
    return _norm(np.fft.irfft(X * mag, n=n))


def _env(n, sr, rate_hz, depth, rng, floor=0.05):
    """Slow random amplitude envelope - the 'breathing' of real ambience."""
    m = max(4, int(n / max(sr / max(rate_hz, 0.01), 1)))
    ctrl = rng.random(m + 2).astype(np.float32)
    e = np.interp(np.linspace(0, m + 1, n), np.arange(m + 2), ctrl)
    return (1.0 - depth + depth * e).clip(floor, None).astype(np.float32)


def _transients(n, sr, rng, rate_per_s, gen, jitter=0.6):
    """Sprinkle short events (clicks, clinks, barks) over a silent bed."""
    out = np.zeros(n, dtype=np.float32)
    if rate_per_s <= 0:
        return out
    t = 0.0
    dur = n / sr
    while t < dur:
        t += max(0.02, rng.exponential(1.0 / rate_per_s) * (1 + jitter * (rng.random() - .5)))
        if t >= dur:
            break
        ev = gen(rng)
        i = int(t * sr)
        j = min(n, i + len(ev))
        out[i:j] += ev[: j - i]
    return out


def _click(sr, rng, f=(2000, 7000), dur=0.012, decay=350.0):
    n = max(8, int(sr * dur))
    x = bandpass(_white(n, rng), sr, f[0], min(f[1], sr / 2 * 0.95), order=2)
    return (x * np.exp(-np.arange(n) / sr * decay)).astype(np.float32)


def _tone_burst(sr, rng, freq, dur, decay=6.0, harmonics=3):
    n = max(8, int(sr * dur))
    t = np.arange(n) / sr
    y = np.zeros(n, dtype=np.float32)
    for h in range(1, harmonics + 1):
        if freq * h >= sr / 2:
            break
        y += (np.sin(2 * np.pi * freq * h * t + rng.random() * 6.28) / h).astype(np.float32)
    return (y * np.exp(-t * decay)).astype(np.float32)


# --------------------------------------------------------------------------
# ambiences
# --------------------------------------------------------------------------
def amb_white(n, sr, rng, **kw):
    return _white(n, rng)


def amb_pink(n, sr, rng, **kw):
    return _pink(n, rng)


def amb_brown(n, sr, rng, **kw):
    return _brown(n, rng)


def amb_hiss(n, sr, rng, **kw):
    """Cheap-headset preamp hiss: HF-tilted white."""
    return _norm(highpass(_white(n, rng), sr, 1200, order=2))


def amb_babble(n, sr, rng, talkers=12, **kw):
    """Multi-talker babble - the classic cocktail-party masker."""
    out = np.zeros(n, dtype=np.float32)
    for _ in range(talkers):
        s = speech_shaped(n, sr, rng)
        # syllabic modulation ~3-5 Hz plus pauses between "sentences"
        syl = _env(n, sr, rng.uniform(3.0, 5.5), 0.9, rng, floor=0.0)
        phr = (_env(n, sr, rng.uniform(0.2, 0.5), 1.0, rng, floor=0.0) > 0.35).astype(np.float32)
        phr = lowpass(phr, sr, 8, order=2)
        # a formant-ish resonance per talker so they do not all sound alike
        f0 = rng.uniform(300, 1400)
        s = bandpass(s, sr, f0 * 0.4, min(f0 * 3.2, sr / 2 * 0.95), order=2) * 0.6 + s * 0.4
        out += s * syl * phr
    return _norm(out)


def amb_cafe(n, sr, rng, **kw):
    bab = amb_babble(n, sr, rng, talkers=16)
    cutlery = _transients(n, sr, rng, 1.6, lambda r: _click(sr, r, (3000, 9000), 0.05, 90.0) * r.uniform(.3, 1.))
    machine = np.zeros(n, dtype=np.float32)
    # espresso grinder / steam wand bursts
    t = 0.0
    while t < n / sr:
        t += rng.uniform(6.0, 18.0)
        if t >= n / sr:
            break
        d = rng.uniform(1.2, 3.0)
        m = int(d * sr)
        i = int(t * sr)
        j = min(n, i + m)
        burst = bandpass(_white(j - i, rng), sr, 700, min(6000, sr / 2 * .95), order=2)
        ramp = np.minimum(np.arange(j - i) / (0.15 * sr + 1), 1.0).astype(np.float32)
        machine[i:j] += burst * ramp * 0.5
    music = lowpass(_pink(n, rng), sr, 1800, order=3) * _env(n, sr, 1.2, .6, rng) * 0.25
    return _norm(bab + 0.5 * cutlery + 0.6 * machine + music)


def amb_restaurant(n, sr, rng, **kw):
    return _norm(amb_cafe(n, sr, rng) * 1.0 + amb_babble(n, sr, rng, talkers=24) * 0.8)


def amb_street(n, sr, rng, **kw):
    bed = _brown(n, rng) * 0.9
    passes = np.zeros(n, dtype=np.float32)
    t = 0.0
    while t < n / sr:
        t += rng.exponential(3.2)
        if t >= n / sr:
            break
        d = rng.uniform(1.5, 4.0)
        i, j = int(t * sr), min(n, int((t + d) * sr))
        if j <= i:
            continue
        m = j - i
        tt = np.linspace(-1, 1, m)
        env = np.exp(-(tt ** 2) * 4.0).astype(np.float32)           # approach + recede
        # doppler-ish: centre frequency falls as it passes
        base = rng.uniform(250, 700)
        car = _white(m, rng)
        car = bandpass(car, sr, base * 0.3, min(base * 4, sr / 2 * .95), order=2)
        passes[i:j] += _norm(car) * env * rng.uniform(0.5, 1.4)
    horns = _transients(n, sr, rng, 0.06,
                        lambda r: _tone_burst(sr, r, r.uniform(380, 520), r.uniform(.3, .9), 3.0, 4) * .5)
    steps = _transients(n, sr, rng, 1.1, lambda r: _click(sr, r, (150, 1200), 0.05, 60.0) * .35)
    return _norm(bed + passes * 1.3 + horns + steps)


def amb_traffic(n, sr, rng, **kw):
    return _norm(amb_street(n, sr, rng) + _brown(n, rng) * 1.4 +
                 lowpass(_white(n, rng), sr, 400, order=2) * 0.8)


def amb_car(n, sr, rng, speed=0.7, **kw):
    """Inside a moving car: engine harmonics + tyre roar + wind + indicator."""
    t = np.arange(n) / sr
    rpm = 1500 + 900 * speed + 120 * np.sin(2 * np.pi * 0.07 * t) + 60 * np.sin(2 * np.pi * 0.23 * t)
    f0 = rpm / 60.0 * 2.0                                  # 4-cyl firing order
    phase = 2 * np.pi * np.cumsum(f0) / sr
    eng = np.zeros(n, dtype=np.float32)
    for h, amp in zip((1, 2, 3, 4, 6), (1.0, .55, .3, .18, .08)):
        if float(np.max(f0)) * h < sr / 2:
            eng += (amp * np.sin(phase * h)).astype(np.float32)
    road = lowpass(_brown(n, rng), sr, 900, order=2) * (0.5 + speed)
    wind = bandpass(_white(n, rng), sr, 300, min(3500, sr / 2 * .95), order=2) * speed * 0.5
    tick = _transients(n, sr, rng, 0.0, lambda r: np.zeros(1, np.float32))
    return _norm(eng * 0.8 + road * 1.2 + wind + tick)


def amb_office(n, sr, rng, **kw):
    hvac = lowpass(_brown(n, rng), sr, 500, order=2) * 1.0
    keys = _transients(n, sr, rng, 4.5, lambda r: _click(sr, r, (1500, 8000), 0.01, 500.0) * r.uniform(.2, .7))
    chat = amb_babble(n, sr, rng, talkers=4) * 0.35 * _env(n, sr, 0.15, 1.0, rng, floor=0.0)
    ring = _transients(n, sr, rng, 0.02,
                       lambda r: _tone_burst(sr, r, 1000, 0.35, 8.0, 2) * .4)
    return _norm(hvac + keys * .8 + chat + ring)


def amb_hvac(n, sr, rng, **kw):
    return _norm(lowpass(_brown(n, rng), sr, 350, order=3) +
                 lowpass(_white(n, rng), sr, 1500, order=2) * 0.25)


def amb_wind(n, sr, rng, **kw):
    base = lowpass(_brown(n, rng), sr, 700, order=2)
    gust = _env(n, sr, 0.35, 0.95, rng, floor=0.02) ** 2
    buffet = lowpass(_white(n, rng), sr, 120, order=2) * (gust ** 2) * 2.0
    return _norm(base * gust + buffet)


def amb_rain(n, sr, rng, **kw):
    bed = highpass(_white(n, rng), sr, 700, order=2)
    drops = _transients(n, sr, rng, 25.0, lambda r: _click(sr, r, (2000, 9000), 0.006, 900.0) * r.uniform(.1, .5))
    return _norm(bed * _env(n, sr, 0.2, .4, rng) + drops)


def amb_crowd(n, sr, rng, **kw):
    bab = amb_babble(n, sr, rng, talkers=40)
    cheer = _env(n, sr, 0.12, 1.0, rng, floor=0.1) ** 2
    return _norm(bab * (0.6 + cheer) + _pink(n, rng) * 0.3)


def amb_tv(n, sr, rng, **kw):
    """TV / radio bleed - band-limited speech + music, a classic false trigger."""
    sp = amb_babble(n, sr, rng, talkers=2)
    mus = lowpass(_pink(n, rng), sr, 2500, order=3) * _env(n, sr, 2.0, .7, rng)
    mix = sp * 0.8 + mus * 0.6
    return _norm(bandpass(mix, sr, 200, min(4000, sr / 2 * .95), order=2))


def amb_construction(n, sr, rng, **kw):
    bed = _brown(n, rng) * 0.6
    drill = np.zeros(n, dtype=np.float32)
    t = 0.0
    while t < n / sr:
        t += rng.uniform(2.0, 7.0)
        if t >= n / sr:
            break
        d = rng.uniform(0.5, 2.0)
        i, j = int(t * sr), min(n, int((t + d) * sr))
        if j <= i:
            continue
        m = j - i
        tt = np.arange(m) / sr
        chop = (sps.square(2 * np.pi * rng.uniform(18, 30) * tt) > 0).astype(np.float32)
        drill[i:j] += bandpass(_white(m, rng), sr, 400, min(5000, sr / 2 * .95), order=2) * chop
    hammer = _transients(n, sr, rng, 0.6, lambda r: _click(sr, r, (200, 4000), 0.06, 70.0) * r.uniform(.5, 1.2))
    return _norm(bed + drill * 1.2 + hammer)


def amb_siren(n, sr, rng, **kw):
    t = np.arange(n) / sr
    f = 700 + 400 * sps.sawtooth(2 * np.pi * 0.5 * t, width=0.5)
    y = np.sin(2 * np.pi * np.cumsum(f) / sr).astype(np.float32)
    dist = _env(n, sr, 0.08, 1.0, rng, floor=0.0) ** 2
    return _norm(y * dist + _brown(n, rng) * 0.3)


def amb_baby(n, sr, rng, **kw):
    def cry(r):
        d = r.uniform(0.4, 1.1)
        m = int(d * sr)
        tt = np.arange(m) / sr
        f = r.uniform(380, 620) * (1 + 0.35 * np.sin(2 * np.pi * 3.5 * tt))
        y = np.zeros(m, dtype=np.float32)
        for h, a in zip((1, 2, 3, 4, 5), (1, .7, .5, .3, .2)):
            if float(np.max(f)) * h < sr / 2:
                y += (a * np.sin(2 * np.pi * np.cumsum(f * h) / sr)).astype(np.float32)
        return y * np.minimum(tt / 0.1, 1.0).astype(np.float32) * np.exp(-tt * 0.8)
    return _norm(_transients(n, sr, rng, 0.9, cry) + _pink(n, rng) * 0.15)


def amb_dog(n, sr, rng, **kw):
    def bark(r):
        m = int(r.uniform(0.12, 0.25) * sr)
        tt = np.arange(m) / sr
        f = r.uniform(180, 320) * np.exp(-tt * 3)
        y = np.sin(2 * np.pi * np.cumsum(f) / sr).astype(np.float32)
        y += bandpass(_white(m, r), sr, 500, min(4000, sr / 2 * .95), order=2) * 0.6
        return y * np.exp(-tt * 12)
    return _norm(_transients(n, sr, rng, 0.35, bark) + _pink(n, rng) * 0.1)


def amb_keyboard(n, sr, rng, **kw):
    return _norm(_transients(n, sr, rng, 7.0, lambda r: _click(sr, r, (1200, 9000), 0.012, 400.0)))


def amb_handling(n, sr, rng, **kw):
    """Phone handling / clothing rub against the mic - low-frequency thumps."""
    thumps = _transients(n, sr, rng, 0.8,
                         lambda r: lowpass(_white(int(sr * .15), r), sr, 220, order=2) *
                         np.exp(-np.arange(int(sr * .15)) / sr * 25) * r.uniform(1, 3))
    rub = lowpass(_white(n, rng), sr, 600, order=2) * (_env(n, sr, 0.6, 1.0, rng, floor=0.0) ** 3)
    return _norm(thumps + rub)


AMBIENCES = {
    "white": amb_white, "pink": amb_pink, "brown": amb_brown, "hiss": amb_hiss,
    "babble": amb_babble, "cafe": amb_cafe, "restaurant": amb_restaurant,
    "street": amb_street, "traffic": amb_traffic, "car": amb_car,
    "office": amb_office, "hvac": amb_hvac, "wind": amb_wind, "rain": amb_rain,
    "crowd": amb_crowd, "tv": amb_tv, "construction": amb_construction,
    "siren": amb_siren, "baby": amb_baby, "dog": amb_dog,
    "keyboard": amb_keyboard, "handling": amb_handling,
}


def generate(kind: str, n: int, sr: int, rng: np.random.Generator, **kw) -> np.ndarray:
    if kind not in AMBIENCES:
        raise KeyError(f"unknown ambience '{kind}'. known: {', '.join(sorted(AMBIENCES))}")
    return AMBIENCES[kind](n, sr, rng, **kw)


# --------------------------------------------------------------------------
# real recordings
# --------------------------------------------------------------------------
def from_dir(spec: str, noise_dir: str, n: int, sr: int,
             rng: np.random.Generator) -> np.ndarray | None:
    """Look up a real noise recording by name/glob under noise_dir."""
    if not noise_dir or not os.path.isdir(noise_dir):
        return None
    pats = [spec, spec + ".*", f"*{spec}*"]
    hits: list[str] = []
    for p in pats:
        hits = sorted(glob.glob(os.path.join(noise_dir, "**", p), recursive=True))
        hits = [h for h in hits if os.path.isfile(h)]
        if hits:
            break
    if not hits:
        return None
    path = hits[int(rng.integers(0, len(hits)))]
    a = load(path).resample(sr)
    return fit_length(a.samples, n, rng)


def make_noise(spec: str, n: int, sr: int, rng: np.random.Generator,
               noise_dir: str | None = None, **kw) -> np.ndarray:
    """Resolve a noise spec: a real file under noise_dir wins, else synthetic.

    `spec` may be 'cafe', 'cafe+keyboard' (layered) or a filename/glob.
    """
    if "+" in spec:
        parts = [p.strip() for p in spec.split("+") if p.strip()]
        acc = np.zeros(n, dtype=np.float32)
        for p in parts:
            acc += make_noise(p, n, sr, rng, noise_dir, **kw)
        return _norm(acc)
    real = from_dir(spec, noise_dir or "", n, sr, rng)
    if real is not None:
        return _norm(real)
    return generate(spec, n, sr, rng, **kw)
