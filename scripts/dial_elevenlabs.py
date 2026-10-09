#!/usr/bin/env python
"""Dial a real ElevenLabs Conversational AI agent over SIP.

Unlike Retell, there is no register-call API step: ElevenLabs' SIP trunk
dials straight to an imported E.164 number that has been assigned to the
agent in their dashboard (confirmed against ElevenLabs' own docs, 2026-09 -
dialling by agent_id alone is explicitly not supported). SDP must offer at
least one of G.722 or G.711 (PCMU/PCMA); this probe offers PCMU only, which
their docs list as supported.

This places a REAL call and may incur a real charge.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sip_probe.probe import dial_and_probe  # noqa: E402

SIP_HOST = "sip.rtc.elevenlabs.io"
SIP_PORT = 5060


def main() -> None:
    p = argparse.ArgumentParser(description="Dial an ElevenLabs agent's SIP number.")
    p.add_argument("--number", required=True,
                    help="E.164 number assigned to the agent, e.g. +15094456456")
    p.add_argument("--wav", required=True, help="WAV file to play once answered")
    p.add_argument("--out", required=True, help="Output directory for the recording + manifest")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--tail", type=float, default=5.0)
    p.add_argument("--local-port", type=int, default=5070,
                   help="Fixed local SIP port - must match the Docker -p publish")
    p.add_argument("--stun", default="stun.l.google.com:19302",
                   help="STUN server so the SDP advertises a reachable public address, "
                        "not a container-internal IP (see account.make_endpoint)")
    args = p.parse_args()

    number = args.number.strip()
    if not re.fullmatch(r"\+\d{6,15}", number):
        print(f"--number must be E.164 (e.g. +15094456456), got: {number}", file=sys.stderr)
        sys.exit(2)

    uri = f"sip:{number}@{SIP_HOST}:{SIP_PORT}"
    print(f"Dialing {uri} ...")
    result = dial_and_probe(
        uri, args.wav, args.out, timeout_s=args.timeout, record_tail_s=args.tail,
        local_port=args.local_port, stun_server=args.stun,
    )
    result["vendor"] = "elevenlabs"
    result["number"] = number
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["confirmed"] else 1)


if __name__ == "__main__":
    main()
