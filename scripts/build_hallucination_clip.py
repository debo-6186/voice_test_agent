#!/usr/bin/env python
"""Phase 3: build the ctl_noise_only clip - 30 s of cafe ambience, zero
speech at all (PLAN.md Phase 3).

Goes through the real corpus builder rather than a parallel noise-mixing
implementation, so the clip's provenance (seed, manifest row, SHA-256,
noise-level convention) is identical to what the full matrix would produce
for this condition - CLAUDE.md: "build on vqlab, do not rewrite it."
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from vqlab.corpus import build  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Build the ctl_noise_only hallucination-reel clip.")
    p.add_argument("--lang", default="en-US")
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "out", "hallucination_corpus"))
    args = p.parse_args()

    cond_path = os.path.join(REPO_ROOT, "config", "conditions.yaml")
    ccfg = yaml.safe_load(open(cond_path, encoding="utf-8"))
    ccfg["blocks"] = {"ctl": ccfg["blocks"]["ctl"]}  # only the controls block

    fd, trimmed_cond_path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(ccfg, f)

        utt_path = os.path.join(REPO_ROOT, "config", "utterances.yaml")
        dataset = build(utt_path, trimmed_cond_path, args.out,
                        tts="espeak", limit_langs=[args.lang])
    finally:
        os.remove(trimmed_cond_path)

    clip_path = os.path.join(args.out, "degraded", args.lang, "ctl_noise_only",
                             f"_{args.lang}.wav")
    print(f"Built {clip_path}")
    print(f"dataset: {dataset}")


if __name__ == "__main__":
    main()
