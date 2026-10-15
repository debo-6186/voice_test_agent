"""Shared HTTP core for every vendor API call - dial resolution (vendors/
loader.py) and agent creation (vendors/create.py) alike.

Confirmed via a full repo grep before writing this: no backoff/retry/429
handling exists anywhere yet (CLAUDE.md, PLAN.md, docs/cli.md and
docs/responsible-use.md all describe it as required; nothing implements it).
Four vendors publish no rate limits at all (docs/vendors.md), so this is the
one place every vendor HTTP call must go through, not a per-vendor concern.
"""
from __future__ import annotations

import json
import mimetypes
import os
import random
import time
import urllib.error
import urllib.request
import uuid


class VendorHTTPError(RuntimeError):
    """Base class for vendor HTTP/config errors - vendors/loader.py's
    VendorDialError and vendors/create.py's VendorCreateError both subclass
    this, so a caller can catch either specifically or this broadly."""


def env(name: str, *, error_cls: type[VendorHTTPError] = VendorHTTPError) -> str:
    val = os.environ.get(name)
    if not val:
        raise error_cls(f"{name} must be set in the environment")
    return val


def _retry_after_seconds(headers) -> float | None:
    val = headers.get("Retry-After") if headers else None
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        return None  # HTTP-date form - not worth parsing for this project's scale


def request_bytes(method: str, url: str, headers: dict, data: bytes | None = None, *,
                  error_cls: type[VendorHTTPError] = VendorHTTPError,
                  max_retries: int = 5, base_delay_s: float = 1.0,
                  max_delay_s: float = 60.0, timeout_s: float = 15.0,
                  _sleep=time.sleep) -> tuple[bytes, list[dict]]:
    """Body-agnostic core: the same retry/429-instrumentation loop
    request_json() used to own directly, now shared with post_multipart()
    (referee/groq_asr.py's audio upload) so there is exactly one retry
    implementation for every vendor/referee HTTP call, not two.

    Returns (raw_response_bytes, rate_limit_events). rate_limit_events is
    the 429 instrumentation PLAN.md asks for - inspectable per call, not
    just printed, so a caller can stamp it onto a manifest row.

    On a non-429 HTTPError, raises immediately (no retry) - a 4xx/5xx that
    isn't rate-limiting is a real failure, not a transient one to paper over.
    A transient network error (timeout, connection reset, DNS blip) DOES
    retry with the same backoff as 429 - verified live that a single such
    blip on a referee-scoring run otherwise crashes the whole run and loses
    every call's progress after it. Same class of bug already fixed once in
    caller/adaptive.py's Gemini calls (a bare TimeoutError wasn't caught) -
    the general version of that lesson belongs in the shared HTTP core, not
    re-discovered per call site.
    """
    rate_limit_events: list[dict] = []
    # urllib's default User-Agent ("Python-urllib/3.x") gets flagged by
    # Cloudflare bot detection - confirmed live against Groq's API (HTTP 403
    # "error code: 1010" from Python, identical request succeeds via curl
    # with no other change). Harmless to send everywhere, not just Groq.
    headers = {"User-Agent": "polycall-bench/1.0", **headers}

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read(), rate_limit_events
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise error_cls(f"{method} {url} failed: HTTP {e.code}: "
                                f"{e.read().decode(errors='replace')}") from e
            retry_after = _retry_after_seconds(e.headers)
            waited_s = retry_after if retry_after is not None else min(
                max_delay_s, base_delay_s * (2 ** attempt)) * (0.5 + random.random())
            event = {"attempt": attempt, "status": 429, "waited_s": waited_s,
                    "retry_after_header": e.headers.get("Retry-After") if e.headers else None}
            failure_desc = "HTTP 429"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            waited_s = min(max_delay_s, base_delay_s * (2 ** attempt)) * (0.5 + random.random())
            event = {"attempt": attempt, "status": None, "waited_s": waited_s, "reason": repr(e)}
            failure_desc = f"transient network error ({e!r})"

        rate_limit_events.append(event)
        if attempt == max_retries:
            raise error_cls(f"{method} {url} failed: {failure_desc} after "
                            f"{max_retries} retries exhausted")
        _sleep(waited_s)

    raise error_cls(f"{method} {url}: unreachable retry loop exit")  # pragma: no cover


def request_json(method: str, url: str, headers: dict, body: dict | None = None, *,
                 error_cls: type[VendorHTTPError] = VendorHTTPError,
                 **retry_kwargs) -> tuple[dict, list[dict]]:
    """Returns (response_json, rate_limit_events) - see request_bytes()."""
    data = json.dumps(body).encode() if body is not None else None
    req_headers = {**headers, "Content-Type": "application/json"} if body is not None else dict(headers)
    raw, events = request_bytes(method, url, req_headers, data, error_cls=error_cls, **retry_kwargs)
    return (json.loads(raw) if raw else {}), events


def _encode_multipart(fields: dict[str, str], file_field: str, file_path: str,
                      file_content_type: str | None = None) -> tuple[bytes, str]:
    """Hand-rolled multipart/form-data body (RFC 2388) - no new dependency,
    matching this module's existing urllib-only convention. Returns
    (body_bytes, content_type_header_value)."""
    boundary = uuid.uuid4().hex
    content_type = file_content_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
        + file_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post_multipart(url: str, fields: dict, file_field: str, file_path: str, headers: dict, *,
                   file_content_type: str | None = None,
                   error_cls: type[VendorHTTPError] = VendorHTTPError, **retry_kwargs) -> dict:
    """POST a file + form fields as multipart/form-data - referee/groq_asr.py's
    audio-upload transcription call, same retry/429 core as every JSON call."""
    data, content_type = _encode_multipart(fields, file_field, file_path, file_content_type)
    raw, _ = request_bytes("POST", url, {**headers, "Content-Type": content_type}, data,
                           error_cls=error_cls, **retry_kwargs)
    return json.loads(raw) if raw else {}


def post_json(url: str, body: dict, headers: dict, *,
             error_cls: type[VendorHTTPError] = VendorHTTPError, **retry_kwargs) -> dict:
    result, _ = request_json("POST", url, headers, body, error_cls=error_cls, **retry_kwargs)
    return result


def get_json(url: str, headers: dict, *,
            error_cls: type[VendorHTTPError] = VendorHTTPError, **retry_kwargs) -> dict:
    result, _ = request_json("GET", url, headers, None, error_cls=error_cls, **retry_kwargs)
    return result


def delete_json(url: str, headers: dict, *,
                error_cls: type[VendorHTTPError] = VendorHTTPError, **retry_kwargs) -> dict:
    result, _ = request_json("DELETE", url, headers, None, error_cls=error_cls, **retry_kwargs)
    return result


def patch_json(url: str, body: dict, headers: dict, *,
               error_cls: type[VendorHTTPError] = VendorHTTPError, **retry_kwargs) -> dict:
    result, _ = request_json("PATCH", url, headers, body, error_cls=error_cls, **retry_kwargs)
    return result
