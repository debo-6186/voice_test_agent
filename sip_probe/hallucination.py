"""Phase 3: count the turns a vendor agent invents from pure noise.

Energy VAD (vqlab.audio.speech_mask) over the agent's recorded leg, grouped
into contiguous runs and filtered by a minimum duration so an isolated noise
spike doesn't count as a turn. No transcription, no scoring - PLAN.md Phase 3
needs only dial, play, record, and this count.
"""
from __future__ import annotations

import numpy as np

from vqlab.audio import load, speech_mask


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs in a boolean array, as (start, end) sample indices."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist()))


def count_segments(wav_path: str, min_turn_ms: float = 300.0,
                   gap_merge_ms: float = 200.0, rel_db: float = -35.0) -> dict:
    """Returns a self-describing result: every parameter that shaped the
    count is inlined, per project convention."""
    audio = load(wav_path)
    sr = audio.sr
    mask = speech_mask(audio.samples, sr, rel_db=rel_db)

    merge_gap = int(sr * gap_merge_ms / 1000.0)
    merged: list[list[int]] = []
    for start, end in _runs(mask):
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])

    min_len = int(sr * min_turn_ms / 1000.0)
    segments = [
        {"start_s": round(s / sr, 3), "end_s": round(e / sr, 3),
         "duration_s": round((e - s) / sr, 3)}
        for s, e in merged if (e - s) >= min_len
    ]

    return {
        "wav_path": wav_path,
        "n_turns": len(segments),
        "segments": segments,
        "total_speech_s": round(sum(seg["duration_s"] for seg in segments), 3),
        "recording_duration_s": round(audio.duration, 3),
        "min_turn_ms": min_turn_ms,
        "gap_merge_ms": gap_merge_ms,
        "rel_db": rel_db,
    }
