"""Packet-network impairments applied at the RTP frame level.

A voice call is a stream of 20 ms frames. Loss, jitter and reordering all
act on whole frames, so that is the grain everything here works at - which
also means the artefacts (a clipped syllable, a repeated 20 ms of vowel,
the metallic stutter of frame repetition) come out the same shape a real
jitter buffer produces them.
"""
from __future__ import annotations

import numpy as np

from ..audio import Audio, EPS, db_to_lin


def _frames(x: np.ndarray, n: int) -> np.ndarray:
    pad = (-len(x)) % n
    return np.concatenate([x, np.zeros(pad, dtype=np.float32)]).reshape(-1, n)


def gilbert_elliott(n_frames: int, loss_pct: float, burst_len: float,
                    rng: np.random.Generator) -> np.ndarray:
    """Two-state burst loss model (ITU-T G.1050 style).

    `loss_pct` is the mean loss rate; `burst_len` the mean number of
    consecutive lost frames. Real networks lose packets in clumps, and a
    clump of three hurts far more than three isolated losses spread out -
    which is exactly why uniform random loss under-states the damage.
    """
    p_loss = max(0.0, min(loss_pct / 100.0, 0.95))
    if p_loss <= 0:
        return np.zeros(n_frames, dtype=bool)
    burst_len = max(1.0, float(burst_len))
    # bad -> good
    r = 1.0 / burst_len
    # good -> bad, solved so stationary bad-state probability == p_loss
    q = r * p_loss / max(1.0 - p_loss, EPS)
    q = min(q, 1.0)
    lost = np.zeros(n_frames, dtype=bool)
    bad = False
    for i in range(n_frames):
        if bad:
            lost[i] = True
            if rng.random() < r:
                bad = False
        else:
            if rng.random() < q:
                bad = True
                lost[i] = True
    return lost


def packet_loss(audio: Audio, loss_pct: float = 5.0, burst_len: float = 3.0,
                frame_ms: float = 20.0, plc: str = "hold", seed: int = 0
                ) -> tuple[Audio, dict]:
    """Drop frames and conceal them the way a real decoder would.

    plc:
      none  - silence, the honest worst case
      hold  - repeat the last good frame with a fade (G.711 Appendix I style)
      noise - comfort noise at the local floor
    """
    sr = audio.sr
    n = max(1, int(sr * frame_ms / 1000.0))
    f = _frames(audio.samples, n).copy()
    rng = np.random.default_rng(seed)
    lost = gilbert_elliott(len(f), loss_pct, burst_len, rng)

    last_good = np.zeros(n, dtype=np.float32)
    run = 0
    for i in range(len(f)):
        if not lost[i]:
            last_good = f[i].copy()
            run = 0
            continue
        run += 1
        if plc == "none":
            f[i] = 0.0
        elif plc == "noise":
            f[i] = rng.standard_normal(n).astype(np.float32) * 0.002
        else:  # hold, attenuating as the gap grows
            g = db_to_lin(-6.0 * run)
            f[i] = last_good * g
            if run > 1:  # decorrelate so it does not ring as a tone
                f[i] = f[i] * (1.0 - 0.25 * rng.random(n).astype(np.float32))

    y = f.reshape(-1)[: len(audio)]
    stats = {
        "frames": int(len(f)),
        "frames_lost": int(lost.sum()),
        "loss_pct_actual": round(100.0 * float(lost.mean()), 3),
        "max_burst": int(_max_run(lost)),
    }
    return audio.with_samples(y), stats


def _max_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def jitter(audio: Audio, jitter_ms: float = 40.0, buffer_ms: float = 60.0,
           frame_ms: float = 20.0, seed: int = 0) -> tuple[Audio, dict]:
    """Simulate a fixed-size playout buffer.

    Frames arrive with random delay; anything later than the buffer depth is
    discarded (it becomes loss), and the survivors are played in arrival
    order. This is why jitter sounds like loss plus scrambling rather than
    like an echo.
    """
    sr = audio.sr
    n = max(1, int(sr * frame_ms / 1000.0))
    f = _frames(audio.samples, n)
    rng = np.random.default_rng(seed)
    k = len(f)
    delay = np.abs(rng.normal(0.0, jitter_ms, size=k))
    late = delay > buffer_ms

    out = np.zeros_like(f)
    last_good = np.zeros(n, dtype=np.float32)
    # arrival order within the buffer window
    order = np.argsort(np.arange(k) * frame_ms + delay, kind="stable")
    played = 0
    for idx in order:
        if late[idx]:
            continue
        if played >= k:
            break
        out[played] = f[idx]
        last_good = f[idx]
        played += 1
    for i in range(played, k):          # buffer starved at the tail
        out[i] = last_good * db_to_lin(-12.0)

    y = out.reshape(-1)[: len(audio)]
    stats = {
        "frames": int(k),
        "frames_late_discarded": int(late.sum()),
        "late_pct": round(100.0 * float(late.mean()), 3),
    }
    return audio.with_samples(y), stats


def frame_repeat(audio: Audio, prob: float = 0.05, frame_ms: float = 20.0,
                 seed: int = 0) -> Audio:
    """The metallic 'robot voice' of a decoder repeating frames."""
    sr = audio.sr
    n = max(1, int(sr * frame_ms / 1000.0))
    f = _frames(audio.samples, n).copy()
    rng = np.random.default_rng(seed)
    for i in range(1, len(f)):
        if rng.random() < prob:
            f[i] = f[i - 1]
    return audio.with_samples(f.reshape(-1)[: len(audio)])


def clock_skew(audio: Audio, ppm: float = 300.0) -> Audio:
    """Sender and receiver clocks disagree; the stream slowly stretches."""
    from ..audio import resample_to
    sr = audio.sr
    eff = sr * (1.0 + ppm / 1e6)
    y = resample_to(audio.samples, int(round(eff)), sr)
    return audio.with_samples(y)
