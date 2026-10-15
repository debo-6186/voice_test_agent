#!/usr/bin/env python
"""Phase 6: dial a target with the real caller state machine - replaces
scripts/test_mock_agent_call.py's fixed-order driver. Works against the mock
agent (Phase 5) with --uri, or against a real vendor with --vendor (dial URI,
auth and connect timeout resolved from vendors/*.yaml - Phase 7's vendor
config, see vendors/loader.py).

Mock agent / arbitrary SIP target:
python scripts/run_caller.py \
    --session-dir out/sessions/sessions/en-US/S3_name_phone_date/ctl_clean \
    --scenario S3_name_phone_date --lang en-US \
    --uri sip:mockagent@127.0.0.1:5062 --local-port 5070 \
    --log out/caller_demo.jsonl

Real vendor:
python scripts/run_caller.py \
    --session-dir out/sessions/sessions/en-US/S3_name_phone_date/ctl_clean \
    --scenario S3_name_phone_date --lang en-US \
    --vendor telnyx --local-port 5070 --stun stun.l.google.com:19302 \
    --tts elevenlabs --log out/caller_telnyx.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import uuid

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import yaml  # noqa: E402
import pjsua2 as pj  # noqa: E402

from sip_probe.account import make_account, make_endpoint  # noqa: E402
from caller.call import CallerCall, run_call  # noqa: E402
from caller import intent  # noqa: E402
from mock_agent import asr  # noqa: E402
from vendors.loader import known_vendors, load_vendor_config, resolve_dial, VendorDialError  # noqa: E402
from vendors.languages import resolve_language, LanguageResolutionError  # noqa: E402
from vendors.recordings import save_vendor_recording  # noqa: E402
from vendors.create import set_agent_language, default_agent_id, VendorCreateError  # noqa: E402

_LANGUAGE_SWITCHABLE_VENDORS = {"retell", "elevenlabs", "telnyx"}


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
    p.add_argument("--uri", default=None,
                   help="raw SIP URI to dial - required unless --vendor resolves one")
    p.add_argument("--vendor", default=None, choices=known_vendors() or None,
                   help="resolves --uri, --sip-auth-user/-pass and --connect-timeout-s "
                        "from vendors/<vendor>.yaml instead of passing them by hand - "
                        "any of those flags passed explicitly still overrides its value")
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
    p.add_argument("--sip-auth-user", default=None,
                   help="digest auth username for trunks gated on username/password "
                        "(e.g. LiveKit when allowed_addresses isn't enabled for the project)")
    p.add_argument("--sip-auth-pass", default=None)
    p.add_argument("--connect-timeout-s", type=float, default=None,
                   help="seconds to wait for media-active before giving up - "
                        "defaults to the vendor's own recommended value with "
                        "--vendor (e.g. LiveKit's agent-dispatch cold start "
                        "needs well over the plain default of 10.0s), else 10.0")
    p.add_argument("--log", default=None)
    p.add_argument("--save-vendor-recording", action="store_true",
                   help="also fetch and save the VENDOR's own call recording "
                        "(separate from the agent audio we always record on our "
                        "own leg for scoring) into the call's out/calls/<call_id>/ "
                        "directory, as vendor_recording.{wav,mp3}. Opt-in: an extra "
                        "vendor API round trip per call, only implemented for "
                        "retell/elevenlabs/telnyx (vendors/recordings.py) - a "
                        "best-effort archival copy, not required for scoring.")
    args = p.parse_args()

    if not args.uri and not args.vendor:
        p.error("either --uri or --vendor is required")

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

    # Forces Whisper's and Laya's model loads now (~4.4s + ~15s measured live)
    # instead of on the first real transcribe()/classify() call, which
    # otherwise lands mid-call with a live vendor agent waiting on us.
    print("warming up ASR + intent classifier...")
    warmup_start = time.time()
    asr.warmup(args.asr_model)
    intent.warmup()
    print(f"warmup done in {time.time() - warmup_start:.1f}s")

    ep = make_endpoint(port=args.local_port, stun_server=args.stun)
    ep.libRegisterThread("caller-main")

    # Resolved as late as possible, right before dialing - Retell's call_id
    # (one of the four dial methods vendors/*.yaml supports) is single-use
    # with a 5-minute TTL from registration.
    vendor_resolved = None
    if args.vendor:
        print(f"resolving dial target from vendors/{args.vendor}.yaml...")
        try:
            vendor_resolved = resolve_dial(args.vendor)
        except VendorDialError as e:
            sys.exit(f"vendor dial resolution failed: {e}")

    # Independent of whether an agent was created via vendors/create.py -
    # this is about what language we're asking an existing vendor agent to
    # operate in, and whether it had to fall back (docs/vendors.md: "record
    # which fallback each vendor used, per call, in the manifest"). This
    # USED to be record-only - confirmed live (2026-10-16) that none of the
    # three test agents' own language had ever actually been switched, so
    # every non-English --lang call before this was silently dialing an
    # English-configured agent. Now actually pushed live, every call, not
    # just recorded - idempotent (same PATCH whether or not it's a no-op).
    vendor_language = None
    if args.vendor:
        try:
            vendor_language = resolve_language(load_vendor_config(args.vendor), args.lang).as_dict()
        except LanguageResolutionError as e:
            print(f"vendor_language not recorded: {e}")
        if args.vendor in _LANGUAGE_SWITCHABLE_VENDORS:
            try:
                agent_id = default_agent_id(args.vendor)
                print(f"setting {args.vendor} agent language for {args.lang}...")
                set_agent_language(args.vendor, agent_id, args.lang)
            except VendorCreateError as e:
                sys.exit(f"failed to set {args.vendor} agent language for {args.lang}: {e}")
        else:
            print(f"note: {args.vendor} agent language not switchable via API - "
                 f"whatever language it's configured with on the vendor's own "
                 f"side is what this call will actually use")

    uri = args.uri or vendor_resolved["uri"]
    sip_auth_user = args.sip_auth_user or (vendor_resolved and vendor_resolved["sip_auth_user"])
    sip_auth_pass = args.sip_auth_pass or (vendor_resolved and vendor_resolved["sip_auth_pass"])
    connect_timeout_s = args.connect_timeout_s
    if connect_timeout_s is None:
        connect_timeout_s = vendor_resolved["connect_timeout_s"] if vendor_resolved else 10.0

    acc = make_account(ep, auth_username=sip_auth_user, auth_password=sip_auth_pass)
    call = CallerCall(acc, pj.PJSUA_INVALID_ID)
    print(f"dialing {uri} ...")
    call.makeCall(uri, pj.CallOpParam(True))

    # Unique per call - out/caller_scratch was a FIXED path, silently
    # overwritten by every new call (no raw audio ever survived past the
    # most recent one). Phase 8's referee scoring needs that audio to still
    # exist after the call, so every call now gets its own directory.
    call_id = f"call-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    try:
        call_start = time.time()
        start = call_start
        while time.time() - start < connect_timeout_s and not call.media_active:
            ep.libHandleEvents(100)
        if not call.media_active:
            sys.exit("call did not reach media-active state")
        print("call connected, media active")

        scratch_dir = os.path.join(REPO_ROOT, "out", "calls", call_id)
        turn_log = run_call(
            ep, call, turns_def, args.lang, lang_meta, args.tts, condition,
            scratch_dir, max_turn_listen_s=args.max_listen_s, asr_model=args.asr_model,
            tier3_threshold=args.tier3_threshold, silence_s=args.silence_s,
        )

        start = time.time()
        while time.time() - start < 5.0 and call.last_state != "DISCONNECTED":
            ep.libHandleEvents(100)

        print(json.dumps(turn_log, indent=2, ensure_ascii=False))
        log_row = {
            "call_id": call_id, "scenario": args.scenario, "lang": args.lang,
            "vendor": args.vendor or "direct_uri", "tts": args.tts, "condition": condition,
            "duration_s": round(time.time() - call_start, 2), "turns": turn_log,
        }
        if vendor_language:
            log_row["vendor_language"] = vendor_language

        if args.save_vendor_recording:
            if args.vendor:
                print(f"fetching {args.vendor}'s own call recording...")
                # Best-effort only - this is an opt-in archival convenience,
                # never allowed to stop call_log.json (the primary artifact,
                # written right below) from being written. Confirmed live
                # this matters: an unhandled VendorHTTPError here previously
                # crashed the whole script before the log write, losing the
                # entire call's record over a feature that was never
                # required in the first place.
                try:
                    result = save_vendor_recording(args.vendor, scratch_dir, call_start)
                except Exception as e:  # noqa: BLE001 - see comment above
                    result = {"saved": False, "path": None, "reason": f"unexpected error: {e!r}"}
                print(f"vendor recording: {result}")
                log_row["vendor_recording"] = result
            else:
                print("--save-vendor-recording ignored: no --vendor given, "
                     "nothing to look up a recording from")
        # Written both ways: a self-contained copy next to this call's own
        # audio (survives even if --log is omitted or paths diverge later),
        # and appended to the shared --log for cheap cross-call aggregation
        # without globbing every out/calls/* directory.
        with open(os.path.join(scratch_dir, "call_log.json"), "w", encoding="utf-8") as f:
            json.dump(log_row, f, ensure_ascii=False, indent=2)
        if args.log:
            with open(args.log, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_row, ensure_ascii=False) + "\n")
    finally:
        del call
        del acc
        ep.libDestroy()


if __name__ == "__main__":
    main()
