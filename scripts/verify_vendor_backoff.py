#!/usr/bin/env python
"""Verifies vendors/http.py's exponential backoff + 429 instrumentation -
PLAN.md's Phase 7 "Exponential backoff and 429 instrumentation from day
one" requirement, confirmed via a full repo grep to not exist anywhere
before this phase. No network, no real vendor involved: monkeypatches
urllib.request.urlopen with a canned response sequence.

python scripts/verify_vendor_backoff.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from vendors import http  # noqa: E402


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _canned_urlopen(responses):
    """responses: list of either an int (429 status) or a dict (200 body).
    Returns a stand-in for urllib.request.urlopen that pops one entry per call."""
    it = iter(responses)

    def _fake(req, timeout=15):
        item = next(it)
        if isinstance(item, int):
            raise urllib.error.HTTPError(
                req.full_url, item, "Too Many Requests",
                {"Retry-After": "0"}, io.BytesIO(b""),
            )
        return _FakeResponse(json.dumps(item).encode())

    return _fake


def test_retries_then_succeeds():
    real_urlopen = http.urllib.request.urlopen
    http.urllib.request.urlopen = _canned_urlopen([429, 429, {"ok": True}])
    try:
        body, events = http.request_json(
            "GET", "https://example.test/x", {}, base_delay_s=0.01,
            max_delay_s=0.02, _sleep=lambda s: None,
        )
    finally:
        http.urllib.request.urlopen = real_urlopen

    assert body == {"ok": True}, f"expected success body, got {body}"
    assert len(events) == 2, f"expected 2 retry events, got {len(events)}: {events}"
    assert all(e["status"] == 429 for e in events)
    print("test_retries_then_succeeds: PASS")


def test_retries_exhausted():
    real_urlopen = http.urllib.request.urlopen
    http.urllib.request.urlopen = _canned_urlopen([429] * 10)
    try:
        try:
            http.request_json("GET", "https://example.test/x", {}, max_retries=3,
                              base_delay_s=0.01, max_delay_s=0.02, _sleep=lambda s: None)
            raise AssertionError("expected VendorHTTPError, got no exception")
        except http.VendorHTTPError as e:
            assert "retries exhausted" in str(e), f"unexpected message: {e}"
    finally:
        http.urllib.request.urlopen = real_urlopen
    print("test_retries_exhausted: PASS")


def test_non_429_fails_immediately():
    def _fake(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"err":1}'))

    real_urlopen = http.urllib.request.urlopen
    http.urllib.request.urlopen = _fake
    try:
        try:
            http.request_json("GET", "https://example.test/x", {}, _sleep=lambda s: None)
            raise AssertionError("expected VendorHTTPError, got no exception")
        except http.VendorHTTPError as e:
            assert "HTTP 400" in str(e), f"unexpected message: {e}"
    finally:
        http.urllib.request.urlopen = real_urlopen
    print("test_non_429_fails_immediately: PASS")


def test_transient_network_error_retries_then_succeeds():
    """The actual bug this was written to catch: a single timeout/connection
    blip on a real referee-scoring run crashed the whole run (only HTTP 429
    was retried before). Confirms a TimeoutError now retries the same way."""
    call_count = {"n": 0}

    def _fake(req, timeout=15):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise TimeoutError("The read operation timed out")
        return _FakeResponse(json.dumps({"ok": True}).encode())

    real_urlopen = http.urllib.request.urlopen
    http.urllib.request.urlopen = _fake
    try:
        body, events = http.request_json(
            "GET", "https://example.test/x", {}, base_delay_s=0.01,
            max_delay_s=0.02, _sleep=lambda s: None,
        )
    finally:
        http.urllib.request.urlopen = real_urlopen

    assert body == {"ok": True}, f"expected success body, got {body}"
    assert len(events) == 2, f"expected 2 retry events, got {len(events)}: {events}"
    assert all(e["status"] is None and "reason" in e for e in events)
    print("test_transient_network_error_retries_then_succeeds: PASS")


def test_post_multipart_retries_then_succeeds():
    """referee/groq_asr.py's audio-upload path uses the same retry core via
    post_multipart() - confirm the multipart encoder + retry loop work
    together with no live network call."""
    real_urlopen = http.urllib.request.urlopen
    http.urllib.request.urlopen = _canned_urlopen([429, {"text": "hello world"}])
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF....WAVEfake")
            wav_path = f.name
        try:
            result = http.post_multipart(
                "https://example.test/transcriptions",
                {"model": "whisper-large-v3", "language": "en"}, "file", wav_path, {},
                base_delay_s=0.01, max_delay_s=0.02, _sleep=lambda s: None,
            )
        finally:
            os.unlink(wav_path)
    finally:
        http.urllib.request.urlopen = real_urlopen

    assert result == {"text": "hello world"}, f"expected transcript body, got {result}"
    print("test_post_multipart_retries_then_succeeds: PASS")


def main() -> None:
    test_retries_then_succeeds()
    test_retries_exhausted()
    test_non_429_fails_immediately()
    test_transient_network_error_retries_then_succeeds()
    test_post_multipart_retries_then_succeeds()
    print("all backoff/429 tests passed")


if __name__ == "__main__":
    main()
