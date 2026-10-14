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
import os
import random
import time
import urllib.error
import urllib.request


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


def request_json(method: str, url: str, headers: dict, body: dict | None = None, *,
                 error_cls: type[VendorHTTPError] = VendorHTTPError,
                 max_retries: int = 5, base_delay_s: float = 1.0,
                 max_delay_s: float = 60.0, _sleep=time.sleep) -> tuple[dict, list[dict]]:
    """Returns (response_json, rate_limit_events). rate_limit_events is the
    429 instrumentation PLAN.md asks for - inspectable per call, not just
    printed, so scripts/run_caller.py can stamp it onto a manifest row.

    On a non-429 HTTPError, raises immediately (no retry) - a 4xx/5xx that
    isn't rate-limiting is a real failure, not a transient one to paper over.
    """
    data = json.dumps(body).encode() if body is not None else None
    req_headers = {**headers, "Content-Type": "application/json"} if body is not None else dict(headers)
    rate_limit_events: list[dict] = []

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return (json.loads(raw) if raw else {}), rate_limit_events
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise error_cls(f"{method} {url} failed: HTTP {e.code}: "
                                f"{e.read().decode(errors='replace')}") from e
            retry_after = _retry_after_seconds(e.headers)
            waited_s = retry_after if retry_after is not None else min(
                max_delay_s, base_delay_s * (2 ** attempt)) * (0.5 + random.random())
            rate_limit_events.append({
                "attempt": attempt, "status": 429, "waited_s": waited_s,
                "retry_after_header": e.headers.get("Retry-After") if e.headers else None,
            })
            if attempt == max_retries:
                raise error_cls(
                    f"{method} {url} failed: HTTP 429 after {max_retries} retries exhausted"
                ) from e
            _sleep(waited_s)

    raise error_cls(f"{method} {url}: unreachable retry loop exit")  # pragma: no cover


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
