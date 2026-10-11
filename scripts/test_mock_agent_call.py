#!/usr/bin/env python
"""Verification-only driver for the Phase 5 mock agent - NOT Phase 6's real
caller state machine. Dials the mock agent and plays a Phase 4 session's
turn_NN.wav files in strict sequence, waiting for the agent's spoken response
between each. Deliberately dumb: it does not detect or react to a repair
prompt, so run the mock agent with all three error rates at 0 for this
happy-path check. The null-test calibration (do measured error rates match
configured ones) reads the mock agent's own per-call JSONL log instead and
doesn't need this script's complexity at all - see scripts/calibrate_mock_agent.py.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pjsua2 as pj  # noqa: E402

from sip_probe.account import make_account, make_endpoint  # noqa: E402
from mock_agent.call import MockAgentCall, listen_for_turn, speak  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session-dir", required=True,
                   help="A Phase 4 sessions/<lang>/<scenario>/<condition> dir")
    p.add_argument("--uri", default="sip:mockagent@127.0.0.1:5062")
    p.add_argument("--local-port", type=int, default=5070)
    p.add_argument("--max-listen-s", type=float, default=15.0)
    args = p.parse_args()

    turn_wavs = sorted(glob.glob(os.path.join(args.session_dir, "turn_*.wav")))
    if not turn_wavs:
        sys.exit(f"no turn_*.wav under {args.session_dir}")

    ep = make_endpoint(port=args.local_port)
    ep.libRegisterThread("test-driver-main")
    acc = make_account(ep)
    call = MockAgentCall(acc, pj.PJSUA_INVALID_ID)
    call.makeCall(args.uri, pj.CallOpParam(True))

    start = time.time()
    while time.time() - start < 10.0 and not call.media_active:
        ep.libHandleEvents(100)
    if not call.media_active:
        sys.exit("call did not reach media-active state")
    print("call connected, media active")

    scratch_dir = os.path.join(REPO_ROOT, "out", "mock_agent_test_driver_scratch")
    os.makedirs(scratch_dir, exist_ok=True)

    try:
        max_repair_retries = 5
        for i, turn_wav in enumerate(turn_wavs):
            is_last = i == len(turn_wavs) - 1
            for attempt in range(max_repair_retries if is_last else 1):
                print(f"[driver] playing {os.path.basename(turn_wav)}"
                     + (f" (retry {attempt})" if attempt else ""))
                speak(ep, call, turn_wav, max_wait_s=args.max_listen_s)
                if call.last_state == "DISCONNECTED":
                    print("[driver] call disconnected while speaking")
                    break
                print("[driver] listening for agent response...")
                _resp_wav, heard = listen_for_turn(
                    ep, call, os.path.join(scratch_dir, f"_resp_{i}_{attempt}.wav"),
                    max_listen_s=args.max_listen_s)
                print(f"[driver] heard_speech={heard}")
                if call.last_state == "DISCONNECTED":
                    print("[driver] call disconnected after agent response")
                    break
                if is_last:
                    # This was the last scripted line, yet the agent is
                    # still on the call - it can only be asking for a
                    # repair (a real closing response ends in a hangup).
                    # Resend the same line rather than silently stalling
                    # the server for the rest of its listen timeout, which
                    # would also skew the timing of whatever call gets
                    # dialled next.
                    print("[driver] call still connected after last turn - assuming repair, resending")
                    continue
                break
            if call.last_state == "DISCONNECTED":
                break

        start = time.time()
        while time.time() - start < 5.0 and call.last_state != "DISCONNECTED":
            ep.libHandleEvents(100)
        print("[driver] final call state:", call.last_state)
    finally:
        # Wrapper objects must be destroyed while the library is still
        # alive, and in this order, regardless of how the loop above
        # exited (sip_probe/probe.py's shutdown-order comment applies
        # identically here).
        del call
        del acc
        ep.libDestroy()


if __name__ == "__main__":
    main()
