#!/usr/bin/env python
"""Phase 6: dial a target with the real caller state machine - replaces
scripts/test_mock_agent_call.py's fixed-order driver. Works against the mock
agent (Phase 5) or, with --uri pointed at a real vendor SIP URI, a real call
(same dial mechanics as scripts/dial_retell.py/dial_elevenlabs.py).

python scripts/run_caller.py \
    --session-dir out/sessions/sessions/en-US/S3_name_phone_date/ctl_clean \
    --scenario S3_name_phone_date --lang en-US \
    --uri sip:mockagent@127.0.0.1:5062 --local-port 5070 \
    --log out/caller_demo.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import yaml  # noqa: E402
import pjsua2 as pj  # noqa: E402

from sip_probe.account import make_account, make_endpoint  # noqa: E402
from caller.call import CallerCall, run_call  # noqa: E402


def _load_condition(session_dir: str) -> dict:
    """The manifest row Phase 4 already wrote for this exact session -
    reused rather than re-derived, so adaptive-turn degradation matches the
    session's real parameters."""
    parts = os.path.normpath(session_dir).split(os.sep)
    lang, scenario, condition_id = parts[-3], parts[-2], parts[-1]
    # session_dir = <out_dir>/sessions/<lang>/<scenario>/<condition>
    out_dir = session_dir
    for _ in range(4):
        out_dir = os.path.dirname(out_dir)
    manifest_path = os.path.join(out_dir, "manifest.jsonl")
    session_id = f"{lang}__{scenario}__{condition_id}"
    with open(manifest_path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["session_id"] == session_id:
                return row["condition"]
    sys.exit(f"no manifest row for session_id={session_id} in {manifest_path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session-dir", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--lang", default="en-US")
    p.add_argument("--uri", required=True)
    p.add_argument("--local-port", type=int, default=5070)
    p.add_argument("--stun", default=None,
                   help="host:port - needed for anything off-localhost, see "
                        "sip_probe.account.make_endpoint's docstring")
    p.add_argument("--tts", default="espeak", choices=["espeak", "elevenlabs"])
    p.add_argument("--tier3-threshold", type=float, default=0.75)
    p.add_argument("--max-listen-s", type=float, default=15.0)
    p.add_argument("--silence-s", type=float, default=0.7,
                   help="end-of-turn silence threshold - lower cuts dead air "
                        "but risks truncating the agent mid-sentence")
    p.add_argument("--asr-model", default="base")
    p.add_argument("--log", default=None)
    args = p.parse_args()

    scfg = yaml.safe_load(open(os.path.join(REPO_ROOT, "config", "scenarios.yaml"), encoding="utf-8"))
    turns_def = [dict(t) for t in scfg["scenarios"][args.scenario]["turns"]]
    turn_wavs = sorted(glob.glob(os.path.join(args.session_dir, "turn_*.wav")))
    if len(turn_wavs) != len(turns_def):
        sys.exit(f"turn count mismatch: {len(turn_wavs)} wavs vs {len(turns_def)} scenario turns")
    for t, wav in zip(turns_def, turn_wavs):
        t["_wav_path"] = wav

    condition = _load_condition(args.session_dir)

    ucfg = yaml.safe_load(open(os.path.join(REPO_ROOT, "config", "utterances.yaml"), encoding="utf-8"))
    lang_meta = ucfg["languages"][args.lang]

    ep = make_endpoint(port=args.local_port, stun_server=args.stun)
    ep.libRegisterThread("caller-main")
    acc = make_account(ep)
    call = CallerCall(acc, pj.PJSUA_INVALID_ID)
    call.makeCall(args.uri, pj.CallOpParam(True))

    try:
        start = time.time()
        while time.time() - start < 10.0 and not call.media_active:
            ep.libHandleEvents(100)
        if not call.media_active:
            sys.exit("call did not reach media-active state")
        print("call connected, media active")

        scratch_dir = os.path.join(REPO_ROOT, "out", "caller_scratch")
        turn_log = run_call(
            ep, call, turns_def, args.lang, lang_meta, args.tts, condition,
            scratch_dir, max_turn_listen_s=args.max_listen_s, asr_model=args.asr_model,
            tier3_threshold=args.tier3_threshold, silence_s=args.silence_s,
        )

        start = time.time()
        while time.time() - start < 5.0 and call.last_state != "DISCONNECTED":
            ep.libHandleEvents(100)

        print(json.dumps(turn_log, indent=2, ensure_ascii=False))
        if args.log:
            with open(args.log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"turns": turn_log}, ensure_ascii=False) + "\n")
    finally:
        del call
        del acc
        ep.libDestroy()


if __name__ == "__main__":
    main()
