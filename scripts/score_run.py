#!/usr/bin/env python
"""Phase 8 slice: referee-ASR + entity-preservation scoring. The scoped
implementation of docs/cli.md's aspirational "polycall score --run <dir>"
line - no polycall console command exists yet, every capability today is a
standalone scripts/*.py (matching scripts/run_caller.py's own precedent).

python scripts/score_run.py --log out/caller_run.jsonl --out out/scores
python scripts/score_run.py --calls-dir out/calls --out out/scores
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import yaml  # noqa: E402

from referee.score import score_call, transcribe_call  # noqa: E402


def _load_calls(log_path: str | None, calls_dir: str | None) -> dict[str, dict]:
    """Returns {call_id: call_log_dict}, deduped."""
    calls: dict[str, dict] = {}
    if log_path and os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if "call_id" in row:
                    calls[row["call_id"]] = row
    if calls_dir and os.path.isdir(calls_dir):
        for call_log_path in glob.glob(os.path.join(calls_dir, "*", "call_log.json")):
            with open(call_log_path, encoding="utf-8") as f:
                row = json.load(f)
            row.setdefault("_call_dir", os.path.dirname(call_log_path))
            calls[row["call_id"]] = row
    return calls


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--log", default=None)
    p.add_argument("--calls-dir", default=os.path.join(REPO_ROOT, "out", "calls"))
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "out", "scores"))
    args = p.parse_args()

    if not args.log and not args.calls_dir:
        p.error("either --log or --calls-dir is required")

    scfg = yaml.safe_load(open(os.path.join(REPO_ROOT, "config", "scenarios.yaml"), encoding="utf-8"))

    calls = _load_calls(args.log, args.calls_dir)
    if not calls:
        sys.exit(f"no calls found (--log={args.log}, --calls-dir={args.calls_dir})")

    os.makedirs(args.out, exist_ok=True)
    scores_jsonl_path = os.path.join(args.out, "scores.jsonl")
    results = []

    with open(scores_jsonl_path, "w", encoding="utf-8") as scores_f:
        for call_id, call_log in calls.items():
            call_dir = call_log.get("_call_dir") or os.path.join(args.calls_dir, call_id)
            if not os.path.isdir(call_dir):
                print(f"skipping {call_id}: no audio directory at {call_dir}", file=sys.stderr)
                continue

            scenario_turns = scfg["scenarios"][call_log["scenario"]]["turns"]
            call_log = dict(call_log)
            call_log["turns"] = transcribe_call(call_dir, call_log["turns"], call_log["lang"])

            result = score_call(call_log, scenario_turns)
            results.append(result)

            with open(os.path.join(call_dir, "score.json"), "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            scores_f.write(json.dumps(result, ensure_ascii=False) + "\n")

            print(f"{call_id}: {result['n_slots_correct']}/{result['n_slots_total']} slots, "
                 f"{result['n_turns']} turns, {result['duration_s']}s, "
                 f"{result['n_repairs']} repairs, {result['n_silent_errors']} silent errors, "
                 f"task_success={result['task_success']}")

    # Plain per-language aggregate - counts/means only, no CI/bootstrap/pass^k
    # (explicitly out of scope for this pass - see the plan's Phase 8 slice).
    by_lang = defaultdict(list)
    for r in results:
        by_lang[r["lang"]].append(r)

    summary = {}
    for lang, rows in by_lang.items():
        n = len(rows)
        summary[lang] = {
            "n_calls": n,
            "task_success_rate": sum(r["task_success"] for r in rows) / n,
            "mean_slots_correct_fraction": sum(
                r["n_slots_correct"] / r["n_slots_total"] for r in rows if r["n_slots_total"]
            ) / n,
            "mean_repairs": sum(r["n_repairs"] for r in rows) / n,
            "mean_silent_errors": sum(r["n_silent_errors"] for r in rows) / n,
        }

    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== summary by language ===")
    for lang, s in summary.items():
        print(f"{lang}: {s['n_calls']} calls, task_success={s['task_success_rate']:.2f}, "
             f"mean_slots={s['mean_slots_correct_fraction']:.2f}, "
             f"mean_repairs={s['mean_repairs']:.1f}, mean_silent_errors={s['mean_silent_errors']:.1f}")


if __name__ == "__main__":
    main()
