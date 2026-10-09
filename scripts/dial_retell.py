#!/usr/bin/env python
"""Register + dial a real Retell agent over SIP.

Retell requires a two-step dial: POST /v2/register-phone-call with an
agent_id to get a one-time call_id, then dial sip:{call_id}@sip.retellai.com
within 5 minutes (confirmed against Retell's own docs, 2026-09).

This places a REAL call and may incur a real charge. docs/vendors.md's
billing traps apply here: Retell blocks calls the instant balance hits $0,
and a trial account with no card on file leaves the call stuck at
call_status "registered" with silence - looking like a SIP bug when it is
actually billing.

Credentials come from the environment (RETELL_API_KEY, RETELL_AGENT_ID) -
pass them via `docker run --env-file .env`, never baked into the image.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sip_probe.probe import dial_and_probe  # noqa: E402

REGISTER_URL = "https://api.retellai.com/v2/register-phone-call"
SIP_HOST = "sip.retellai.com"


def register_call(api_key: str, agent_id: str) -> dict:
    req = urllib.request.Request(
        REGISTER_URL,
        data=json.dumps({"agent_id": agent_id}).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"register-phone-call failed: HTTP {e.code}: {body}") from e


def main() -> None:
    p = argparse.ArgumentParser(description="Register + dial a Retell agent over SIP.")
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

    api_key = os.environ.get("RETELL_API_KEY")
    agent_id = os.environ.get("RETELL_AGENT_ID")
    if not api_key or not agent_id:
        print("RETELL_API_KEY and RETELL_AGENT_ID must be set in the environment", file=sys.stderr)
        sys.exit(2)

    print(f"Registering phone call with Retell agent {agent_id}...")
    reg = register_call(api_key, agent_id)
    call_id = reg.get("call_id")
    if not call_id:
        print(f"No call_id in response: {reg}", file=sys.stderr)
        sys.exit(2)
    print(f"Registered call_id={call_id}, call_status={reg.get('call_status')}")

    uri = f"sip:{call_id}@{SIP_HOST}"
    print(f"Dialing {uri} (must connect within Retell's 5-minute window) ...")
    result = dial_and_probe(
        uri, args.wav, args.out, timeout_s=args.timeout, record_tail_s=args.tail,
        local_port=args.local_port, stun_server=args.stun,
    )
    result["vendor"] = "retell"
    result["agent_id"] = agent_id
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["confirmed"] else 1)


if __name__ == "__main__":
    main()
