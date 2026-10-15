"""Creates a standardised agent from one CanonicalAgentConfig across vendors
- Phase 7's "real work" per docs/vendors.md: "Only the prompt string ports
cleanly. Voice IDs live in per-vendor namespaces and language semantics
differ fundamentally... The voice-mapping table and language-code
translation are the real work, not the HTTP calls."

One explicit Python function per vendor rather than a templated dispatcher
(vendors/loader.py's resolve_dial() can get away with string substitution
for its static_uri method because a dial URI is just a string; an agent's
create payload is a materially different nested JSON shape per vendor).

Live-verified against real accounts (2026-10-14): Retell, ElevenLabs, Telnyx -
the project's four in-scope vendors (Vapi, Bland and Pipecat were explicitly
descoped the same day - CLAUDE.md). LiveKit has no HTTP creation endpoint at
all (confirmed in docs/vendors.md's "Programmatic agent creation" table:
worker code + dispatch metadata, not an API call) - it raises
VendorCreateError quoting the yaml's create_unsupported_reason rather than
faking a call that was never going to succeed.
"""
from __future__ import annotations

import os

import yaml

from vendors.canonical import CanonicalAgentConfig
from vendors.http import VendorHTTPError, env, get_json, post_json, patch_json, delete_json
from vendors.languages import resolve_language
from vendors.loader import VENDORS_DIR, load_vendor_config

REPO_ROOT = os.path.dirname(VENDORS_DIR)


class VendorCreateError(VendorHTTPError):
    pass


def _env(name: str) -> str:
    return env(name, error_cls=VendorCreateError)


def _utterances_lang_meta(bcp47: str) -> dict:
    """The caller's own per-language voice metadata (config/utterances.yaml)
    - used by Telnyx's BYOK adapter to reuse the exact same ElevenLabs voice
    the caller itself speaks with. A DIFFERENT namespace from
    vendors/voices.yaml - see that file's header comment."""
    path = os.path.join(REPO_ROOT, "config", "utterances.yaml")
    with open(path, encoding="utf-8") as f:
        ucfg = yaml.safe_load(f)
    return ucfg["languages"][bcp47]


def _voices_table() -> dict:
    path = os.path.join(VENDORS_DIR, "voices.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _vendor_voice(vendor: str, voice_key: str) -> dict:
    table = _voices_table()
    try:
        return table["voice_keys"][voice_key][vendor]
    except KeyError as e:
        raise VendorCreateError(
            f"no vendors/voices.yaml entry for voice_key={voice_key!r}, vendor={vendor!r}"
        ) from e


# --------------------------------------------------------------------------
# Retell - POST /create-agent (two-resource flow: a Retell LLM, then the
# agent referencing it via response_engine). voice_field_path/exact create
# payload fields confirmed live via scripts/verify_vendor_adapters.py, not
# guessed - see vendors/retell.yaml's create: block for the agreed field
# paths this function reads from, so a future API change is a YAML edit.
# --------------------------------------------------------------------------

def retell_create_agent(cfg: CanonicalAgentConfig) -> dict:
    vcfg = load_vendor_config("retell")
    create = vcfg["create"]
    api_key = _env(vcfg["auth"]["api_key_env"])
    headers = {"Authorization": f"Bearer {api_key}"}

    lang = resolve_language(vcfg, cfg.bcp47_language)
    voice = _vendor_voice("retell", cfg.voice_key)
    if not voice.get("voice_id"):
        raise VendorCreateError(
            "vendors/voices.yaml has no retell voice_id yet - run "
            "`python scripts/seed_voice_table.py --vendor retell` first"
        )

    llm_resp = post_json(
        create["llm_endpoint"],
        {"general_prompt": cfg.prompt, "model": cfg.llm.get("model", "gpt-4o")},
        headers, error_cls=VendorCreateError,
    )
    llm_id = llm_resp.get("llm_id")
    if not llm_id:
        raise VendorCreateError(f"create-retell-llm returned no llm_id: {llm_resp}")

    try:
        agent_resp = post_json(
            create["endpoint"],
            {
                "response_engine": {"type": "retell-llm", "llm_id": llm_id},
                "voice_id": voice["voice_id"],
                "agent_name": cfg.agent_name,
                "language": lang.vendor_code,
            },
            headers, error_cls=VendorCreateError,
        )
    except VendorCreateError:
        # The llm resource above already exists server-side - clean it up
        # rather than leaving it orphaned just because the second (agent)
        # step failed.
        try:
            delete_json(create["llm_delete_endpoint"].format(llm_id=llm_id),
                       headers, error_cls=VendorCreateError)
        except VendorCreateError:
            pass
        raise

    agent_id = agent_resp.get("agent_id")
    if not agent_id:
        raise VendorCreateError(f"create-agent returned no agent_id: {agent_resp}")
    return {"agent_id": agent_id, "call_time_overrides": {}, "metadata": {"llm_id": llm_id},
           "language_resolution": lang.as_dict()}


def retell_get_agent(agent_id: str) -> dict:
    vcfg = load_vendor_config("retell")
    api_key = _env(vcfg["auth"]["api_key_env"])
    url = vcfg["create"]["get_endpoint"].format(agent_id=agent_id)
    return get_json(url, {"Authorization": f"Bearer {api_key}"}, error_cls=VendorCreateError)


def retell_delete_agent(agent_id: str, *, llm_id: str | None = None) -> None:
    vcfg = load_vendor_config("retell")
    api_key = _env(vcfg["auth"]["api_key_env"])
    headers = {"Authorization": f"Bearer {api_key}"}
    url = vcfg["create"]["delete_endpoint"].format(agent_id=agent_id)
    delete_json(url, headers, error_cls=VendorCreateError)
    if llm_id:
        try:
            delete_json(vcfg["create"]["llm_delete_endpoint"].format(llm_id=llm_id),
                       headers, error_cls=VendorCreateError)
        except VendorCreateError:
            pass  # best-effort - the agent itself is already gone, the llm resource
                  # orphaning is a minor cleanup gap, never block on it


# --------------------------------------------------------------------------
# ElevenLabs - POST /v1/convai/agents/create
# --------------------------------------------------------------------------

def elevenlabs_create_agent(cfg: CanonicalAgentConfig) -> dict:
    vcfg = load_vendor_config("elevenlabs")
    create = vcfg["create"]
    api_key = _env(vcfg["auth"]["api_key_env"])
    headers = {"xi-api-key": api_key}

    lang = resolve_language(vcfg, cfg.bcp47_language)
    # ElevenLabs is the native vendor whose per-language voice already lives
    # in config/utterances.yaml - read it directly rather than duplicating a
    # stale copy into vendors/voices.yaml (that table documents this with
    # source: see_utterances_yaml instead of a real voice_id).
    lang_meta = _utterances_lang_meta(cfg.bcp47_language)

    resp = post_json(
        create["endpoint"],
        {
            "name": cfg.agent_name,
            "conversation_config": {
                "agent": {"prompt": {"prompt": cfg.prompt}, "language": lang.vendor_code},
                "tts": {"voice_id": lang_meta["elevenlabs_voice_id"]},
            },
        },
        headers, error_cls=VendorCreateError,
    )
    agent_id = resp.get("agent_id")
    if not agent_id:
        raise VendorCreateError(f"convai/agents/create returned no agent_id: {resp}")
    return {"agent_id": agent_id, "call_time_overrides": {}, "metadata": {},
           "language_resolution": lang.as_dict()}


def elevenlabs_get_agent(agent_id: str) -> dict:
    vcfg = load_vendor_config("elevenlabs")
    api_key = _env(vcfg["auth"]["api_key_env"])
    url = vcfg["create"]["get_endpoint"].format(agent_id=agent_id)
    return get_json(url, {"xi-api-key": api_key}, error_cls=VendorCreateError)


def elevenlabs_delete_agent(agent_id: str) -> None:
    vcfg = load_vendor_config("elevenlabs")
    api_key = _env(vcfg["auth"]["api_key_env"])
    url = vcfg["create"]["delete_endpoint"].format(agent_id=agent_id)
    delete_json(url, {"xi-api-key": api_key}, error_cls=VendorCreateError)


# --------------------------------------------------------------------------
# Telnyx - POST /v2/ai/assistants. Supports BYOK ElevenLabs voices directly
# (voice format "elevenlabs.<model>.<VoiceId>" + an api_key_ref integration
# secret) - verified live via web search against Telnyx's own docs, 2026-10.
# Reuses config/utterances.yaml's elevenlabs_voice_id for this bcp47
# language, so the vendor agent's voice stays in sync with the caller's own
# TTS voice automatically - see _utterances_lang_meta()'s docstring note on
# why this is a deliberately different lookup from vendors/voices.yaml.
# --------------------------------------------------------------------------

_TELNYX_ELEVENLABS_SECRET_IDENTIFIER = "polycall-elevenlabs-byok"
_TELNYX_ELEVENLABS_MODEL = "v3"   # confirm live - docs example was "v3"/"Adam", exact
                                  # catalog of accepted <model> values unconfirmed


def _ensure_telnyx_elevenlabs_secret(headers: dict) -> str:
    """Returns the secret's UUID id (what api_key_ref actually needs -
    confirmed live: passing the identifier string instead 400s with "Voice
    ... not found ... ensure its integration secret is configured"). GET
    /v2/integration_secrets DOES list existing secrets despite the official
    docs not confirming a list endpoint - confirmed live, not guessed."""
    existing = get_json("https://api.telnyx.com/v2/integration_secrets", headers,
                        error_cls=VendorCreateError)
    for secret in existing.get("data", []):
        if secret.get("identifier") == _TELNYX_ELEVENLABS_SECRET_IDENTIFIER:
            return secret["id"]

    created = post_json(
        "https://api.telnyx.com/v2/integration_secrets",
        {"identifier": _TELNYX_ELEVENLABS_SECRET_IDENTIFIER, "type": "bearer",
         "token": _env("ELEVENLABS_API_KEY")},
        headers, error_cls=VendorCreateError,
    )
    return created["data"]["id"]


def telnyx_create_agent(cfg: CanonicalAgentConfig, *, use_byok_voice: bool = False) -> dict:
    """use_byok_voice=True attempts the ElevenLabs BYOK voice path - written
    and wired correctly per Telnyx's own docs, but NOT live-confirmed: a
    real create call 400'd with "Voice settings API key (integration
    secrets) <id> not found" even with the secret's real UUID id (not its
    identifier string) as api_key_ref. Telnyx's own ElevenLabs integration
    docs note "only a premium ElevenLabs account can be used... freemium is
    not supported" - plausible but unconfirmed root cause (this project's
    ElevenLabs API key lacks the user_read scope needed to check the
    account tier directly). Defaults to Telnyx's own native voice instead
    (introspected into vendors/voices.yaml, same pattern as Retell) so the
    create/get/delete contract itself stays verified rather than blocked on
    this one external dependency."""
    vcfg = load_vendor_config("telnyx")
    create = vcfg["create"]
    api_key = _env(vcfg["auth"]["api_key_env"])
    headers = {"Authorization": f"Bearer {api_key}"}

    lang = resolve_language(vcfg, cfg.bcp47_language)
    if lang.vendor_code is None:
        raise VendorCreateError(
            f"telnyx has no language code or fallback for {cfg.bcp47_language!r}: "
            f"tried {lang.fallback_chain_tried}"
        )

    if use_byok_voice:
        secret_id = _ensure_telnyx_elevenlabs_secret(headers)
        lang_meta = _utterances_lang_meta(cfg.bcp47_language)
        voice_settings = {
            "voice": f"elevenlabs.{_TELNYX_ELEVENLABS_MODEL}.{lang_meta['elevenlabs_voice_id']}",
            "api_key_ref": secret_id,
        }
    else:
        voice_settings = {"voice": _vendor_voice("telnyx", cfg.voice_key)["voice_id"]}

    resp = post_json(
        create["endpoint"],
        {
            "name": cfg.agent_name,
            "model": cfg.llm.get("model", "moonshotai/Kimi-K2.6"),
            "instructions": cfg.prompt,
            "voice_settings": voice_settings,
            "transcription": {"language": lang.vendor_code},
        },
        headers, error_cls=VendorCreateError,
    )
    assistant_id = resp.get("id")
    if not assistant_id:
        raise VendorCreateError(f"ai/assistants create returned no id: {resp}")
    return {"agent_id": assistant_id, "call_time_overrides": {}, "metadata": {},
           "language_resolution": lang.as_dict()}


def telnyx_get_agent(agent_id: str) -> dict:
    vcfg = load_vendor_config("telnyx")
    api_key = _env(vcfg["auth"]["api_key_env"])
    url = vcfg["create"]["get_endpoint"].format(agent_id=agent_id)
    return get_json(url, {"Authorization": f"Bearer {api_key}"}, error_cls=VendorCreateError)


def telnyx_update_agent(agent_id: str, fields: dict) -> dict:
    """Partial update of an EXISTING assistant (e.g. editing instructions on
    the project's own test agent) - distinct from create/get/delete, which
    the three-vendor adapter contract above doesn't otherwise need. PATCH on
    the same resource URL as get_endpoint, confirmed live."""
    vcfg = load_vendor_config("telnyx")
    api_key = _env(vcfg["auth"]["api_key_env"])
    url = vcfg["create"]["get_endpoint"].format(agent_id=agent_id)
    return patch_json(url, fields, {"Authorization": f"Bearer {api_key}"}, error_cls=VendorCreateError)


def telnyx_delete_agent(agent_id: str) -> None:
    vcfg = load_vendor_config("telnyx")
    api_key = _env(vcfg["auth"]["api_key_env"])
    url = vcfg["create"]["delete_endpoint"].format(agent_id=agent_id)
    delete_json(url, {"Authorization": f"Bearer {api_key}"}, error_cls=VendorCreateError)


# --------------------------------------------------------------------------
# LiveKit - no HTTP creation API at all. Raises loudly rather than faking a
# call that was never going to succeed (CLAUDE.md: "never a hard fail" is
# about graceful degradation of OPTIONAL features, not about pretending a
# structurally-absent one exists).
# --------------------------------------------------------------------------

def _unsupported_create(vendor: str, *_args, **_kwargs) -> dict:
    vcfg = load_vendor_config(vendor)
    reason = vcfg.get("create_unsupported_reason", "no creation API documented for this vendor")
    raise VendorCreateError(f"{vendor}: agent creation not supported - {reason}")


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

_ADAPTERS = {
    "retell": (retell_create_agent, retell_get_agent, retell_delete_agent),
    "elevenlabs": (elevenlabs_create_agent, elevenlabs_get_agent, elevenlabs_delete_agent),
    "telnyx": (telnyx_create_agent, telnyx_get_agent, telnyx_delete_agent),
}


def create_agent(vendor: str, cfg: CanonicalAgentConfig) -> dict:
    if vendor not in _ADAPTERS:
        return _unsupported_create(vendor)
    return _ADAPTERS[vendor][0](cfg)


def get_agent(vendor: str, agent_id: str) -> dict:
    if vendor not in _ADAPTERS:
        return _unsupported_create(vendor)
    return _ADAPTERS[vendor][1](agent_id)


def delete_agent(vendor: str, agent_id: str, **kwargs) -> None:
    if vendor not in _ADAPTERS:
        _unsupported_create(vendor)
        return
    _ADAPTERS[vendor][2](agent_id, **kwargs)
