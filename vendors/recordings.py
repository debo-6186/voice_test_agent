"""Fetches and saves the VENDOR's OWN call recording - a separate thing
from referee/groq_asr.py's transcription target, which is always OUR OWN
recorded leg (CLAUDE.md: never trust the vendor's own transcript/recording
for scoring). This is purely an archival convenience, opt-in via
scripts/run_caller.py's --save-vendor-recording flag (docs/responsible-use.md:
"Recordings of your own agent's responses... treat run artifacts accordingly
and do not publish them without checking" - the same caution applies here).

None of the four vendors hand back a recording URL at dial time (not even
Retell's register-phone-call, which only returns the call_id used to build
the SIP URI) - every lookup below is "find the call that started around
call_start_ts" against a per-vendor history/detail-records endpoint, then
fetch its recording. Matched by closest start-time within _MATCH_TOLERANCE_S,
not by vendor call_id - uniform across vendors, confirmed live against real
calls placed earlier in this project (2026-10-15) rather than guessed from
docs alone.

LiveKit has no equivalent lookup at all - recordings there are Egress/S3-based
infrastructure this project hasn't set up, not a simple history API call.
"""
from __future__ import annotations

import calendar
import os
import time
import urllib.parse

from vendors.http import VendorHTTPError, env, get_json, post_json, request_bytes
from vendors.loader import load_vendor_config

_MATCH_TOLERANCE_S = 10.0   # how close a vendor's own recorded start-time must
                            # be to our call_start_ts to count as the same call


class VendorRecordingError(VendorHTTPError):
    pass


def _save_bytes(raw: bytes, call_dir: str, filename: str) -> str:
    path = os.path.join(call_dir, filename)
    with open(path, "wb") as f:
        f.write(raw)
    return path


def _fetch_retell(call_dir: str, call_start_ts: float) -> dict:
    vcfg = load_vendor_config("retell")
    api_key = env(vcfg["auth"]["api_key_env"], error_cls=VendorRecordingError)
    agent_id = env(vcfg["auth"]["agent_id_env"], error_cls=VendorRecordingError)
    headers = {"Authorization": f"Bearer {api_key}"}

    calls = post_json("https://api.retellai.com/v2/list-calls",
                      {"filter_criteria": {"agent_id": [agent_id]}, "limit": 20},
                      headers, error_cls=VendorRecordingError)
    best = min(calls, key=lambda c: abs(c.get("start_timestamp", 0) / 1000.0 - call_start_ts),
              default=None)
    if best is None or abs(best["start_timestamp"] / 1000.0 - call_start_ts) > _MATCH_TOLERANCE_S:
        return {"saved": False, "path": None,
               "reason": "no matching call found in list-calls within tolerance"}

    url = best.get("recording_url")
    if not url:
        return {"saved": False, "path": None,
               "reason": f"call {best['call_id']} has no recording_url yet"}
    raw, _ = request_bytes("GET", url, {}, error_cls=VendorRecordingError)
    path = _save_bytes(raw, call_dir, "vendor_recording.wav")
    return {"saved": True, "path": path, "vendor_call_id": best["call_id"]}


def _fetch_telnyx(call_dir: str, call_start_ts: float) -> dict:
    vcfg = load_vendor_config("telnyx")
    api_key = env(vcfg["auth"]["api_key_env"], error_cls=VendorRecordingError)
    headers = {"Authorization": f"Bearer {api_key}"}

    params = {"filter[record_type]": "ai-voice-assistant", "page[size]": "20"}
    url = "https://api.telnyx.com/v2/detail_records?" + urllib.parse.urlencode(params)
    records = get_json(url, headers, error_cls=VendorRecordingError).get("data", [])

    def _started_at(rec: dict) -> float | None:
        created = rec.get("created_at")
        if created is None:
            return None
        # created_at IS the call's start time, confirmed live against three
        # real calls (within 3s each) - NOT call-end as first assumed (an
        # earlier version subtracted duration_sec to "back out" a start
        # time, which was simply wrong and missed every real match by
        # 80-190s). calendar.timegm(), not time.mktime(): created_at is UTC
        # ("...Z" suffix) and mktime() would assume local time instead.
        return calendar.timegm(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))

    best, best_delta = None, None
    for rec in records:
        started = _started_at(rec)
        if started is None:
            continue
        delta = abs(started - call_start_ts)
        if best_delta is None or delta < best_delta:
            best, best_delta = rec, delta

    if best is None or best_delta > _MATCH_TOLERANCE_S:
        return {"saved": False, "path": None,
               "reason": "no matching ai-voice-assistant detail record within tolerance"}

    session_id = best.get("telnyx_session_id")
    rec_params = {"filter[call_session_id]": session_id}
    rec_url = "https://api.telnyx.com/v2/recordings?" + urllib.parse.urlencode(rec_params)
    rec_data = get_json(rec_url, headers, error_cls=VendorRecordingError).get("data", [])
    if not rec_data:
        return {"saved": False, "path": None,
               "reason": f"no recording yet for call_session_id={session_id}"}

    mp3_url = rec_data[0].get("download_urls", {}).get("mp3")
    if not mp3_url:
        return {"saved": False, "path": None, "reason": "recording found but no mp3 download_url"}
    raw, _ = request_bytes("GET", mp3_url, {}, error_cls=VendorRecordingError)
    path = _save_bytes(raw, call_dir, "vendor_recording.mp3")
    return {"saved": True, "path": path, "vendor_call_id": session_id}


def _fetch_elevenlabs(call_dir: str, call_start_ts: float) -> dict:
    vcfg = load_vendor_config("elevenlabs")
    api_key = env(vcfg["auth"]["api_key_env"], error_cls=VendorRecordingError)
    agent_id = env(vcfg["auth"]["agent_id_env"], error_cls=VendorRecordingError)
    headers = {"xi-api-key": api_key}

    params = {"agent_id": agent_id, "page_size": "20"}
    url = "https://api.elevenlabs.io/v1/convai/conversations?" + urllib.parse.urlencode(params)
    conversations = get_json(url, headers, error_cls=VendorRecordingError).get("conversations", [])

    best = min(conversations, key=lambda c: abs(c.get("start_time_unix_secs", 0) - call_start_ts),
              default=None)
    if best is None or abs(best["start_time_unix_secs"] - call_start_ts) > _MATCH_TOLERANCE_S:
        return {"saved": False, "path": None,
               "reason": "no matching conversation within tolerance"}

    conv_id = best["conversation_id"]
    audio_url = f"https://api.elevenlabs.io/v1/convai/conversations/{conv_id}/audio"
    raw, _ = request_bytes("GET", audio_url, headers, error_cls=VendorRecordingError)
    path = _save_bytes(raw, call_dir, "vendor_recording.mp3")
    return {"saved": True, "path": path, "vendor_call_id": conv_id}


_FETCHERS = {
    "retell": _fetch_retell,
    "telnyx": _fetch_telnyx,
    "elevenlabs": _fetch_elevenlabs,
}


def save_vendor_recording(vendor: str, call_dir: str, call_start_ts: float, *,
                          max_wait_s: float = 30.0, poll_interval_s: float = 5.0) -> dict:
    """Polls briefly (recordings can lag a few seconds past call-end before
    the vendor's API surfaces them) rather than failing on the first miss.
    Returns {"saved": bool, "path": str|None, "reason": str|None,
    "vendor_call_id": str|None} - always a dict, never raises for a plain
    not-found-yet outcome (only for a real HTTP/auth failure)."""
    if vendor not in _FETCHERS:
        return {"saved": False, "path": None,
               "reason": f"{vendor}: vendor recording retrieval not supported "
                        f"(no simple history/lookup API - e.g. LiveKit's recordings "
                        f"are Egress/S3-based, not implemented here)"}

    fetch = _FETCHERS[vendor]
    deadline = time.time() + max_wait_s
    last_result = None
    while True:
        try:
            last_result = fetch(call_dir, call_start_ts)
        except VendorHTTPError as e:
            # Confirmed live: ElevenLabs's own audio asset can 404
            # ("missing_conversation_audio") for a real conversation that
            # already shows up in the conversations list - the metadata
            # lands before the audio finishes processing. Treat as the same
            # not-ready-yet case as a plain miss, not a hard failure, up to
            # the same budget - never propagate past the deadline, this is a
            # best-effort archival feature that must not crash the call.
            last_result = {"saved": False, "path": None, "reason": f"transient error: {e}"}
        if last_result["saved"] or time.time() >= deadline:
            return last_result
        time.sleep(poll_interval_s)
