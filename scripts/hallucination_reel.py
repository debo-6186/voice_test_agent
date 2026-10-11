#!/usr/bin/env python
"""Phase 3: hallucination reel.

Play ctl_noise_only (30 s of cafe ambience, zero speech) to a vendor agent
and count the turns it invents. Nearly free once Phase 2 works - dial, play,
record, and a VAD pass. No transcription, no scoring, no scenarios
(PLAN.md Phase 3).

Reuses Phase 2's dial_and_probe and the same per-vendor dial mechanics as
scripts/dial_retell.py / dial_elevenlabs.py rather than a parallel
implementation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from dial_retell import register_call  # noqa: E402
from sip_probe.hallucination import count_segments  # noqa: E402
from sip_probe.probe import dial_and_probe  # noqa: E402

RETELL_SIP_HOST = "sip.retellai.com"
ELEVENLABS_SIP_HOST = "sip.rtc.elevenlabs.io"
ELEVENLABS_SIP_PORT = 5060


def resolve_uri(vendor: str, args: argparse.Namespace) -> tuple[str, dict]:
    """Returns (sip_uri, extra manifest fields)."""
    if vendor == "retell":
        api_key = os.environ.get("RETELL_API_KEY")
        agent_id = os.environ.get("RETELL_AGENT_ID")
        if not api_key or not agent_id:
            sys.exit("RETELL_API_KEY and RETELL_AGENT_ID must be set in the environment")
        reg = register_call(api_key, agent_id)
        call_id = reg.get("call_id")
        if not call_id:
            sys.exit(f"No call_id in Retell response: {reg}")
        print(f"Registered call_id={call_id}, call_status={reg.get('call_status')}")
        return f"sip:{call_id}@{RETELL_SIP_HOST}", {"agent_id": agent_id}

    if vendor == "elevenlabs":
        if not args.number:
            sys.exit("--number is required for vendor=elevenlabs")
        number = args.number.strip()
        if not re.fullmatch(r"\+\d{6,15}", number):
            sys.exit(f"--number must be E.164 (e.g. +15094456456), got: {number}")
        return f"sip:{number}@{ELEVENLABS_SIP_HOST}:{ELEVENLABS_SIP_PORT}", {"number": number}

    if vendor == "local":
        if not args.uri:
            sys.exit("--uri is required for vendor=local")
        return args.uri, {}

    sys.exit(f"Unknown vendor: {vendor}")


def main() -> None:
    p = argparse.ArgumentParser(description="Hallucination reel: play noise-only, count invented turns.")
    p.add_argument("--vendor", required=True, choices=["retell", "elevenlabs", "local"])
    p.add_argument("--clip", required=True, help="Path to the ctl_noise_only WAV")
    p.add_argument("--out", required=True, help="Output directory for recording + report")
    p.add_argument("--number", help="ElevenLabs E.164 number")
    p.add_argument("--uri", help="Raw SIP URI (vendor=local)")
    p.add_argument("--timeout", type=float, default=40.0)
    p.add_argument("--tail", type=float, default=5.0,
                   help="Seconds of extra recording after the clip finishes playing")
    p.add_argument("--local-port", type=int, default=5070,
                   help="Fixed local SIP port - must match the Docker -p publish")
    p.add_argument("--stun", default="stun.l.google.com:19302")
    args = p.parse_args()

    if not os.path.exists(args.clip):
        sys.exit(f"Clip not found: {args.clip} (run scripts/build_hallucination_clip.py first)")

    uri, extra = resolve_uri(args.vendor, args)
    print(f"Dialing {uri} with {args.clip} ...")
    result = dial_and_probe(
        uri, args.clip, args.out, timeout_s=args.timeout, record_tail_s=args.tail,
        local_port=args.local_port, stun_server=args.stun,
    )
    result["vendor"] = args.vendor
    result.update(extra)

    report: dict = {"probe": result}
    if result.get("wav_out"):
        wav_path = os.path.join(args.out, result["wav_out"])
        hallu = count_segments(wav_path)
        report["hallucination"] = hallu
        print(f"\n{args.vendor}: {hallu['n_turns']} turn(s) invented from "
              f"{result['wav_in_duration_s']}s of pure noise, zero speech input.")
        for i, seg in enumerate(hallu["segments"], 1):
            print(f"  turn {i}: {seg['start_s']}s - {seg['end_s']}s "
                  f"({seg['duration_s']}s)")
    else:
        report["hallucination"] = None
        print("No recording captured - call did not confirm or produced an empty file.")

    report_path = os.path.join(args.out, "hallucination_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nWrote {report_path}")


if __name__ == "__main__":
    main()
