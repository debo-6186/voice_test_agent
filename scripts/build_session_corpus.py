#!/usr/bin/env python
"""Phase 4: build the multi-turn session corpus (PLAN.md Phase 4).

python scripts/build_session_corpus.py --languages en-US --scenarios S3_name_phone_date \
    --conditions ctl_clean,A_cafe_snr10 --tts elevenlabs --out out/sessions_test
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from vqlab.sessions import build_sessions  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Build the Phase 4 multi-turn session corpus.")
    p.add_argument("--languages", help="Comma-separated lang codes (default: all)")
    p.add_argument("--scenarios", help="Comma-separated scenario IDs (default: all)")
    p.add_argument("--conditions", help="Comma-separated condition IDs (default: all)")
    p.add_argument("--tts", default="espeak", choices=["espeak", "elevenlabs"])
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "out", "sessions"))
    p.add_argument("--scenarios-config", default=os.path.join(REPO_ROOT, "config", "scenarios.yaml"))
    p.add_argument("--utterances-config", default=os.path.join(REPO_ROOT, "config", "utterances.yaml"))
    p.add_argument("--conditions-config", default=os.path.join(REPO_ROOT, "config", "conditions.yaml"))
    args = p.parse_args()

    limit_langs = args.languages.split(",") if args.languages else None
    limit_scenarios = args.scenarios.split(",") if args.scenarios else None

    cond_path = args.conditions_config
    tmp_cond_path = None
    if args.conditions:
        wanted = set(args.conditions.split(","))
        ccfg = yaml.safe_load(open(args.conditions_config, encoding="utf-8"))
        for block in ccfg["blocks"].values():
            block["conditions"] = [c for c in block["conditions"] if c["id"] in wanted]
        ccfg["blocks"] = {k: b for k, b in ccfg["blocks"].items() if b["conditions"]}
        fd, tmp_cond_path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(ccfg, f)
        cond_path = tmp_cond_path

    try:
        dataset = build_sessions(
            args.scenarios_config, args.utterances_config, cond_path, args.out,
            tts=args.tts, limit_langs=limit_langs, limit_scenarios=limit_scenarios,
        )
    finally:
        if tmp_cond_path:
            os.remove(tmp_cond_path)

    print(f"Built {dataset['n_sessions']} sessions under {args.out}")
    print(dataset)


if __name__ == "__main__":
    main()
