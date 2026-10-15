"""The one canonical agent config every vendor adapter translates from -
docs/vendors.md's own words: "Only the prompt string ports cleanly. Voice
IDs live in per-vendor namespaces and language semantics differ
fundamentally. Define one canonical internal config
({prompt, llm, voice_key, bcp47_language}) and write thin adapters."

agent_name is the one addition beyond that quoted shape - it's the cleanup
marker scripts/verify_vendor_adapters.py relies on to find and delete its
own throwaway test agents (never guess at a vendor's own naming instead).
"""
from __future__ import annotations

from dataclasses import dataclass, field

BCP47_LANGUAGES = ["en-US", "en-SG", "hi-IN", "es-ES", "id-ID"]


@dataclass
class CanonicalAgentConfig:
    prompt: str
    llm: dict   # confirmed live (2026-10-14): llm["model"] is NOT portable
                # across vendors - Telnyx 422s on "gpt-4o" ("not available
                # for inference"), since each vendor has its own fixed model
                # catalog. Leave unset to let each adapter use its own
                # documented default (vendors/create.py's *_create_agent()
                # functions), same portability caveat docs/vendors.md
                # already states for voice IDs.
    voice_key: str
    bcp47_language: str
    agent_name: str
    metadata: dict = field(default_factory=dict)


def validate_canonical_config(cfg: dict) -> CanonicalAgentConfig:
    """Checks only the required shape - everything else is permissive, per
    CLAUDE.md's "never a hard fail"."""
    missing = [k for k in ("prompt", "llm", "voice_key", "bcp47_language", "agent_name")
              if k not in cfg]
    if missing:
        raise ValueError(f"canonical config missing required field(s): {missing}")
    if cfg["bcp47_language"] not in BCP47_LANGUAGES:
        raise ValueError(
            f"bcp47_language must be one of {BCP47_LANGUAGES}, got {cfg['bcp47_language']!r}"
        )
    return CanonicalAgentConfig(
        prompt=cfg["prompt"], llm=cfg["llm"], voice_key=cfg["voice_key"],
        bcp47_language=cfg["bcp47_language"], agent_name=cfg["agent_name"],
        metadata=cfg.get("metadata", {}),
    )
