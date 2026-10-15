"""Resolves a vendor's dial URI + per-call dial kwargs from vendors/*.yaml,
per CLAUDE.md's "vendor config is data... never hardcode it" rule.

Replaces the ad-hoc --uri strings typed by hand against Retell/ElevenLabs/
LiveKit/Telnyx during Phase 6 real-call verification (2026-10-12 to
2026-10-14) - each of those required a real debugging round before the
right dial mechanism was found (Retell's one-time register-call, LiveKit's
raw-project-id SIP subdomain vs. its pretty wss:// one, Telnyx's sip_subdomain
only discoverable via the assistant's own API record). This module is where
that knowledge now lives instead of chat history.

resolve_dial() may place free, read-only API calls (Retell's register-call,
Telnyx's lookup) but never an actual phone call itself - the real call still
only happens when the caller dials the returned URI.
"""
from __future__ import annotations

import os

import yaml

from vendors.http import VendorHTTPError, env as _http_env, get_json as _http_get_json, \
    post_json as _http_post_json

VENDORS_DIR = os.path.dirname(os.path.abspath(__file__))


class VendorDialError(VendorHTTPError):
    pass


def known_vendors() -> list[str]:
    """Every vendors/*.yaml that declares a top-level vendor: key - excludes
    non-vendor config like vendors/voices.yaml (the cross-vendor voice table,
    keyed by voice_key then vendor, not itself a vendor)."""
    names = []
    for fn in os.listdir(VENDORS_DIR):
        if not fn.endswith(".yaml"):
            continue
        with open(os.path.join(VENDORS_DIR, fn), encoding="utf-8") as f:
            if yaml.safe_load(f).get("vendor"):
                names.append(fn[:-5])
    return sorted(names)


def load_vendor_config(vendor: str) -> dict:
    path = os.path.join(VENDORS_DIR, f"{vendor}.yaml")
    if not os.path.exists(path):
        raise VendorDialError(f"no vendors/{vendor}.yaml - known vendors: {known_vendors()}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _env(name: str) -> str:
    try:
        return _http_env(name)
    except VendorHTTPError as e:
        raise VendorDialError(str(e)) from e


def _post_json(url: str, body: dict, headers: dict) -> dict:
    return _http_post_json(url, body, headers, error_cls=VendorDialError)


def _get_json(url: str, headers: dict) -> dict:
    return _http_get_json(url, headers, error_cls=VendorDialError)


def resolve_dial(vendor: str) -> dict:
    """Returns {"uri", "sip_auth_user", "sip_auth_pass", "connect_timeout_s"}."""
    cfg = load_vendor_config(vendor)
    dial = cfg.get("dial")
    if dial is None:
        reason = cfg.get("dial_unsupported_reason", "no dial mechanism documented for this vendor")
        raise VendorDialError(f"{vendor}: dialling not supported - {reason}")
    method = dial["method"]
    auth_cfg = cfg.get("auth", {})
    connect_timeout_s = float(cfg.get("connect_timeout_s", 10.0))
    sip_auth_user = sip_auth_pass = None

    if auth_cfg.get("method") == "digest":
        sip_auth_user = _env(auth_cfg["username_env"])
        sip_auth_pass = _env(auth_cfg["password_env"])

    if method == "static_uri":
        uri = dial["uri_template"].format(
            **{k: v for k, v in dial.items() if k not in ("method", "uri_template")}
        )

    elif method == "register_call":
        api_key = _env(auth_cfg["api_key_env"])
        agent_id = _env(auth_cfg["agent_id_env"])
        reg = _post_json(
            dial["register_url"], {"agent_id": agent_id},
            {"Authorization": f"Bearer {api_key}"},
        )
        call_id = reg.get("call_id")
        if not call_id:
            raise VendorDialError(f"register-phone-call returned no call_id: {reg}")
        uri = f"sip:{call_id}@{dial['sip_host']}"

    elif method == "static_uri_via_texml_lookup":
        api_key = _env(auth_cfg["api_key_env"])
        headers = {"Authorization": f"Bearer {api_key}"}
        assistant = _get_json(
            f"https://api.telnyx.com/v2/ai/assistants/{dial['assistant_id']}", headers,
        )
        app_id = assistant["telephony_settings"]["default_texml_app_id"]
        app = _get_json(f"https://api.telnyx.com/v2/texml_applications/{app_id}", headers)
        sip_subdomain = app["data"]["inbound"]["sip_subdomain"]
        uri = dial["uri_template"].format(user=dial.get("user", "probe"),
                                          sip_subdomain=sip_subdomain)

    else:
        raise VendorDialError(f"unknown dial method: {method}")

    return {
        "uri": uri,
        "sip_auth_user": sip_auth_user,
        "sip_auth_pass": sip_auth_pass,
        "connect_timeout_s": connect_timeout_s,
    }
