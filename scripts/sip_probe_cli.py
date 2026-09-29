#!/usr/bin/env python
"""CLI wrapper around sip_probe.probe.dial_and_probe - the Phase 2 SIP probe.

    python scripts/sip_probe_cli.py --uri sip:test@127.0.0.1:5062 \\
        --wav corpus/sample.wav --out out/probe_local
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sip_probe.probe import dial_and_probe  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Dial a SIP URI, play a WAV, record the reply, hang up.")
    p.add_argument("--uri", required=True, help="SIP URI to dial, e.g. sip:test@127.0.0.1:5062")
    p.add_argument("--wav", required=True, help="WAV file to play once the call is answered")
    p.add_argument("--out", required=True, help="Output directory for the recording + manifest")
    p.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait for the call to connect")
    p.add_argument("--tail", type=float, default=3.0,
                   help="Extra seconds to keep recording after the played file ends")
    args = p.parse_args()

    result = dial_and_probe(args.uri, args.wav, args.out, timeout_s=args.timeout, record_tail_s=args.tail)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["confirmed"] else 1)


if __name__ == "__main__":
    main()
