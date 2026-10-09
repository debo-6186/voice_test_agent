"""Phase 2: dial one SIP URI, play one WAV, record what comes back, hang up.

No state machine, no intent classification, no scenarios - the smallest
thing that makes a real call, so PJSIP/codec/credentials are debugged before
any conversational logic exists to confuse the picture (PLAN.md Phase 2).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import wave

import pjsua2 as pj

from .account import make_account, make_endpoint
from .call import ProbeCall
from .codec_check import negotiated_codec


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _wav_duration_s(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def dial_and_probe(
    uri: str,
    wav_in: str,
    out_dir: str,
    timeout_s: float = 30.0,
    record_tail_s: float = 3.0,
    local_port: int = 0,
    stun_server: str | None = None,
) -> dict:
    """Returns a self-describing result dict, and also writes it as
    manifest.json alongside the recording - per project convention, a
    result row should never depend on config the reader may not have.

    `local_port`/`stun_server`: needed for anything outside the local host -
    see account.make_endpoint()'s docstring for why a NAT'd/containerized
    probe records silence without them.
    """
    os.makedirs(out_dir, exist_ok=True)
    wav_out = os.path.join(out_dir, "received.wav")
    manifest_path = os.path.join(out_dir, "manifest.json")
    play_duration_s = _wav_duration_s(wav_in)

    ep = make_endpoint(port=local_port, stun_server=stun_server)
    ep.libRegisterThread("probe-main")
    acc = make_account(ep)

    result: dict = {
        "uri": uri,
        "wav_in": os.path.abspath(wav_in),
        "wav_in_duration_s": round(play_duration_s, 3),
        "wav_out": None,
        "codec": None,
        "disconnect_reason": None,
        "call_states": [],
        "confirmed": False,
    }
    media_active_at = {"t": None}

    def on_media_active(call: ProbeCall, media_index: int):
        if media_active_at["t"] is None:
            media_active_at["t"] = time.time()
            result["codec"] = negotiated_codec(call, media_index)

    def on_disconnect(call: ProbeCall):
        result["disconnect_reason"] = call.disconnect_reason

    call = ProbeCall(acc, wav_in, wav_out, on_media_active, on_disconnect)
    call.makeCall(uri, pj.CallOpParam(True))

    start = time.time()
    while time.time() - start < timeout_s:
        ep.libHandleEvents(50)
        if call.last_state and (
            not result["call_states"] or result["call_states"][-1] != call.last_state
        ):
            result["call_states"].append(call.last_state)
        if call.last_state == "DISCONNECTED":
            break
        if media_active_at["t"] is not None:
            elapsed_since_media = time.time() - media_active_at["t"]
            if elapsed_since_media >= play_duration_s + record_tail_s:
                break

    # Hang up if the far end has not already.
    try:
        ci = call.getInfo()
        if ci.state != pj.PJSIP_INV_STATE_DISCONNECTED:
            call.hangup(pj.CallOpParam(True))
            hangup_start = time.time()
            while time.time() - hangup_start < 5.0:
                ep.libHandleEvents(50)
                if call.last_state == "DISCONNECTED":
                    break
    except pj.Error:
        pass

    result["confirmed"] = "CONFIRMED" in result["call_states"]
    if os.path.exists(wav_out) and os.path.getsize(wav_out) > 44:  # > bare WAV header
        result["wav_out"] = os.path.relpath(wav_out, out_dir)
        result["wav_out_sha256"] = sha256(wav_out)
        result["wav_out_bytes"] = os.path.getsize(wav_out)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    # Both wrapper objects must be destroyed while the library is still
    # alive - acc only went out of scope with the function itself, i.e.
    # *after* libDestroy(), and its destructor firing against an already-
    # destroyed library trips a pjsua_call_set_user_data assertion.
    del call
    del acc
    ep.libDestroy()
    return result
