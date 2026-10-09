#!/usr/bin/env python
"""Stitch a caller-side session (Phase 4 turn_NN.wav files) and the mock
agent's captured responses (from test_mock_agent_call.py's scratch dir) into
one playable, chronologically-ordered conversation WAV.

Not part of the production harness (which will eventually record a real
stereo dual-leg tape per docs/sip-harness.md) - just a quick way to actually
listen to what a call sounded like.

python scripts/stitch_demo_conversation.py \
    --session-dir out/sessions/sessions/en-US/S3_name_phone_date/ctl_clean \
    --driver-scratch out/mock_agent_test_driver_scratch \
    --out out/demo_conversation.wav
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import numpy as np

from vqlab.audio import Audio, load, speech_mask, write_wav


def _trim_silence(a: Audio, pad_s: float = 0.2) -> Audio:
    """Crop to [first speech - pad, last speech + pad]. Response clips can
    carry several seconds of dead air before speech starts (real synthesis/
    network latency, faithfully recorded by listen_for_turn) - stitched
    untrimmed, that silence reads as "the agent never answered" rather than
    as the latency it actually is."""
    mask = speech_mask(a.samples, a.sr)
    if not np.any(mask):
        return a
    idx = np.flatnonzero(mask)
    pad = int(pad_s * a.sr)
    start = max(0, idx[0] - pad)
    end = min(len(a.samples), idx[-1] + pad)
    return Audio(a.samples[start:end], a.sr)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session-dir", required=True)
    p.add_argument("--driver-scratch", default=os.path.join(REPO_ROOT, "out", "mock_agent_test_driver_scratch"))
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "out", "demo_conversation.wav"))
    p.add_argument("--sr", type=int, default=16000)
    p.add_argument("--gap-s", type=float, default=0.4)
    args = p.parse_args()

    caller_turns = sorted(glob.glob(os.path.join(args.session_dir, "turn_*.wav")))
    if not caller_turns:
        sys.exit(f"no turn_*.wav under {args.session_dir}")

    def resp_for(i: int) -> str | None:
        # test_mock_agent_call.py names these _resp_{turn_index}_{attempt}.wav -
        # take the LAST attempt (the one that actually got a non-repair result).
        matches = sorted(
            glob.glob(os.path.join(args.driver_scratch, f"_resp_{i}_*.wav")),
            key=lambda f: int(re.search(r"_resp_\d+_(\d+)\.wav$", f).group(1)),
        )
        return matches[-1] if matches else None

    pieces: list[Audio] = []
    labels: list[str] = []
    gap = Audio(np.zeros(int(args.gap_s * args.sr), dtype=np.float32), args.sr)

    for i, caller_wav in enumerate(caller_turns):
        caller_audio = _trim_silence(load(caller_wav).resample(args.sr))
        pieces.append(caller_audio)
        labels.append(f"CALLER: {os.path.basename(caller_wav)} ({round(caller_audio.duration,1)}s)")
        pieces.append(gap)
        resp_wav = resp_for(i)
        if resp_wav:
            agent_audio = _trim_silence(load(resp_wav).resample(args.sr))
            pieces.append(agent_audio)
            labels.append(f"AGENT:  {os.path.basename(resp_wav)} ({round(agent_audio.duration,1)}s)")
            pieces.append(gap)
        else:
            labels.append(f"AGENT:  (no response captured for turn {i})")

    combined = np.concatenate([a.samples for a in pieces])
    write_wav(args.out, Audio(combined, args.sr))

    total_s = len(combined) / args.sr
    print(f"Wrote {args.out} ({round(total_s, 1)}s)")
    for label in labels:
        print(" ", label)


if __name__ == "__main__":
    main()
