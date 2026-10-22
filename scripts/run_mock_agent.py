#!/usr/bin/env python
"""Phase 5: start the mock agent (polycall demo's backend, docs/cli.md).

python scripts/run_mock_agent.py --scenario S3_name_phone_date --lang en-US \
    --port 5062 --asr-error-rate 0.08 --repair-threshold 0.4 \
    --silent-error-rate 0.03 --log out/mock_agent.jsonl

--tts/--asr-backend DEFAULT TO elevenlabs/groq, NOT espeak/local - user's
explicit, repeated choice (pay-per-use, "no problem") after confirming live on
hi-IN that local espeak+faster-whisper-base produces garbled, low-confidence
transcripts (and on longer utterances, un-transcribable ones even via Groq) -
see mock_agent/asr.py's _transcribe_groq docstring for the measured numbers.
A DELIBERATE, STATED DEVIATION from CLAUDE.md's "`polycall demo` runs the
whole loop with zero accounts and zero cost" - same documentation pattern
already used for referee/groq_asr.py's own stated departure from "per-language
referees are mandatory". Pass --tts espeak --asr-backend local to get the
original zero-cost/zero-account path back.
"""
from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from mock_agent.server import run_mock_agent  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Run the Phase 5 mock agent.")
    p.add_argument("--scenario", default="S3_name_phone_date",
                   choices=["S1_phone_only", "S2_name_phone", "S3_name_phone_date", "S3_reordered_test"])
    p.add_argument("--lang", default="en-US")
    p.add_argument("--port", type=int, default=5062)
    p.add_argument("--tts", default="elevenlabs", choices=["espeak", "elevenlabs"],
                   help="elevenlabs (default): higher-quality voice, pay-per-use "
                        "(ELEVENLABS_API_KEY) - needed on longer utterances for Groq to "
                        "transcribe cleanly, confirmed live on hi-IN. espeak: free, "
                        "zero-account fallback, but its Hindi voice degrades badly on "
                        "longer sentences.")
    p.add_argument("--asr-error-rate", type=float, default=0.0)
    p.add_argument("--repair-threshold", type=float, default=0.0)
    p.add_argument("--silent-error-rate", type=float, default=0.0)
    p.add_argument("--max-turn-listen-s", type=float, default=15.0)
    p.add_argument("--asr-model", default="base")
    p.add_argument("--asr-backend", default="groq", choices=["local", "groq"],
                   help="groq (default): pay-per-use Groq-hosted whisper-large-v3 "
                        "(GROQ_API_KEY) - user's explicit, repeated choice; much "
                        "faster/more accurate than the local CPU model. local: free "
                        "faster-whisper on CPU, zero accounts - the original dev "
                        "fallback, still available via --asr-backend local.")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-calls", type=int, default=None)
    p.add_argument("--log", default=None, help="Append per-call turn logs as JSONL here")
    args = p.parse_args()

    run_mock_agent(
        port=args.port, scenario_id=args.scenario, lang=args.lang, tts=args.tts,
        asr_error_rate=args.asr_error_rate, repair_threshold=args.repair_threshold,
        silent_error_rate=args.silent_error_rate, max_turn_listen_s=args.max_turn_listen_s,
        asr_model=args.asr_model, asr_backend=args.asr_backend, seed=args.seed,
        log_path=args.log, max_calls=args.max_calls,
    )


if __name__ == "__main__":
    main()
