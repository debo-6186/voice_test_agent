#!/usr/bin/env python
"""Retroactively fetches vendor recordings that weren't ready yet when
scripts/run_caller.py's --save-vendor-recording tried inline. Confirmed live
(2026-10-16): ElevenLabs's own audio asset can still 404
("missing_conversation_audio") 30+ seconds after a call ends - longer than
run_caller.py should ever block a sequential run waiting on it. This script
is the decoupled "try again later, with a bigger budget" half - run it
anytime after the fact (minutes or days later), not from inside a call.

python scripts/fetch_vendor_recordings.py --calls-dir out/calls
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from vendors.recordings import save_vendor_recording  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--calls-dir", default=os.path.join(REPO_ROOT, "out", "calls"))
    p.add_argument("--max-wait-s", type=float, default=120.0,
                   help="budget per call - bigger than run_caller.py's inline "
                        "attempt, since this runs decoupled from any live call")
    p.add_argument("--poll-interval-s", type=float, default=15.0)
    p.add_argument("--force", action="store_true",
                   help="retry even calls already marked saved:true")
    args = p.parse_args()

    n_tried = n_saved = n_skipped_unsupported = n_already_saved = 0
    for call_log_path in sorted(glob.glob(os.path.join(args.calls_dir, "*", "call_log.json"))):
        call_dir = os.path.dirname(call_log_path)
        with open(call_log_path, encoding="utf-8") as f:
            call_log = json.load(f)

        vendor = call_log.get("vendor")
        existing = call_log.get("vendor_recording")
        if existing and existing.get("saved") and not args.force:
            n_already_saved += 1
            continue
        if vendor not in ("retell", "elevenlabs", "telnyx"):
            n_skipped_unsupported += 1
            continue

        n_tried += 1
        # call_log.json doesn't carry the raw call_start timestamp today -
        # the call_id already encodes it (f"call-{int(time.time())}-...")
        # at second resolution, well within _MATCH_TOLERANCE_S.
        call_start_ts = float(call_log["call_id"].split("-")[1])

        print(f"{call_log['call_id']} ({vendor}): fetching...")
        result = save_vendor_recording(vendor, call_dir, call_start_ts,
                                       max_wait_s=args.max_wait_s,
                                       poll_interval_s=args.poll_interval_s)
        print(f"  -> {result}")
        call_log["vendor_recording"] = result
        with open(call_log_path, "w", encoding="utf-8") as f:
            json.dump(call_log, f, ensure_ascii=False, indent=2)
        if result["saved"]:
            n_saved += 1

    print(f"\n{n_tried} tried, {n_saved} saved, {n_already_saved} already saved, "
         f"{n_skipped_unsupported} skipped (unsupported vendor)")


if __name__ == "__main__":
    main()
