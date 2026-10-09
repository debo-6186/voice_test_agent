#!/usr/bin/env python
"""The null test: start the mock agent (as a separate process - PJSUA-LIB is
a per-process singleton, so the agent and the dialing driver can never share
one process, same reason Phase 2 always ran the local echo UAS and the probe
as two separate processes), dial it N times on S1_phone_only (one turn, then
closing - the simplest case), and confirm the mock agent's own JSONL log
shows outcome rates close to what was configured.

python scripts/calibrate_mock_agent.py --session-dir out/sessions/sessions/en-US/S1_phone_only/ctl_clean \
    --calls 50 --asr-error-rate 0.08 --repair-threshold 0.0 --silent-error-rate 0.05
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session-dir", required=True)
    p.add_argument("--lang", default="en-US")
    p.add_argument("--calls", type=int, default=50)
    p.add_argument("--asr-error-rate", type=float, default=0.0)
    p.add_argument("--repair-threshold", type=float, default=0.0)
    p.add_argument("--silent-error-rate", type=float, default=0.0)
    p.add_argument("--port", type=int, default=5062)
    p.add_argument("--local-port", type=int, default=5070)
    p.add_argument("--log", default=os.path.join(REPO_ROOT, "out", "mock_agent_calib.jsonl"))
    args = p.parse_args()

    if os.path.exists(args.log):
        os.remove(args.log)

    server = subprocess.Popen([
        sys.executable, os.path.join(REPO_ROOT, "scripts", "run_mock_agent.py"),
        "--scenario", "S1_phone_only", "--lang", args.lang, "--port", str(args.port),
        "--asr-error-rate", str(args.asr_error_rate),
        "--repair-threshold", str(args.repair_threshold),
        "--silent-error-rate", str(args.silent_error_rate),
        "--max-calls", str(args.calls), "--log", args.log,
    ])
    time.sleep(2.0)  # let the endpoint/account come up before dialing

    try:
        for i in range(args.calls):
            print(f"=== calibration call {i + 1}/{args.calls} ===")
            subprocess.run([
                sys.executable, os.path.join(REPO_ROOT, "scripts", "test_mock_agent_call.py"),
                "--session-dir", args.session_dir,
                "--uri", f"sip:mockagent@127.0.0.1:{args.port}",
                "--local-port", str(args.local_port),
            ], check=True)
        server.wait(timeout=30)
    finally:
        if server.poll() is None:
            server.terminate()

    outcomes = {"ok": 0, "repair": 0, "silent_error": 0, "timeout": 0}
    with open(args.log, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            for turn in row["turns"]:
                outcomes[turn["outcome"]] = outcomes.get(turn["outcome"], 0) + 1

    total = sum(outcomes.values())
    print(f"\n{total} turns logged across {args.calls} calls")
    for k, v in outcomes.items():
        rate = v / total if total else 0.0
        print(f"  {k}: {v} ({rate:.3f})")
    print(f"configured: asr_error_rate={args.asr_error_rate} "
         f"repair_threshold={args.repair_threshold} silent_error_rate={args.silent_error_rate}")


if __name__ == "__main__":
    main()
