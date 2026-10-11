#!/usr/bin/env python
"""Phase 5: start the mock agent (polycall demo's backend, docs/cli.md).

python scripts/run_mock_agent.py --scenario S3_name_phone_date --lang en-US \
    --port 5062 --tts espeak --asr-error-rate 0.08 --repair-threshold 0.4 \
    --silent-error-rate 0.03 --log out/mock_agent.jsonl
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
    p.add_argument("--tts", default="espeak", choices=["espeak", "elevenlabs"])
    p.add_argument("--asr-error-rate", type=float, default=0.0)
    p.add_argument("--repair-threshold", type=float, default=0.0)
    p.add_argument("--silent-error-rate", type=float, default=0.0)
    p.add_argument("--max-turn-listen-s", type=float, default=15.0)
    p.add_argument("--asr-model", default="base")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-calls", type=int, default=None)
    p.add_argument("--log", default=None, help="Append per-call turn logs as JSONL here")
    args = p.parse_args()

    run_mock_agent(
        port=args.port, scenario_id=args.scenario, lang=args.lang, tts=args.tts,
        asr_error_rate=args.asr_error_rate, repair_threshold=args.repair_threshold,
        silent_error_rate=args.silent_error_rate, max_turn_listen_s=args.max_turn_listen_s,
        asr_model=args.asr_model, seed=args.seed, log_path=args.log, max_calls=args.max_calls,
    )


if __name__ == "__main__":
    main()
