"""Confirm what codec was actually negotiated on a call, rather than
assuming the offer (PCMU-only, see account.py) was what got used.
docs/sip-harness.md: "verify what was actually negotiated rather than
assuming."
"""
from __future__ import annotations

import pjsua2 as pj


def negotiated_codec(call: pj.Call, media_index: int) -> dict:
    try:
        si = call.getStreamInfo(media_index)
        return {
            "codec_name": si.codecName,
            "clock_rate": si.codecClockRate,
        }
    except Exception as e:  # noqa: BLE001 - report whatever pjsua2/SWIG actually raises
        return {"error": f"{type(e).__name__}: {e}"}
