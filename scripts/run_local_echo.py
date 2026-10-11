#!/usr/bin/env python
"""Start the zero-spend local SIP echo UAS used to test the probe without
any vendor account. Blocks until interrupted.

    python scripts/run_local_echo.py --port 5062 --wav corpus/reference.wav
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sip_probe.local_echo import run_echo_server  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Auto-answering local SIP UAS for zero-spend probe testing.")
    p.add_argument("--port", type=int, default=5062)
    p.add_argument("--wav", required=True, help="Reference WAV to play to any caller")
    args = p.parse_args()
    run_echo_server(args.port, args.wav)


if __name__ == "__main__":
    main()
