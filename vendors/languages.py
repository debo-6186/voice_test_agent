"""Resolves a canonical bcp47 language to a vendor's own language code,
recording any fallback used - docs/vendors.md, verbatim: "Seven of eight
vendors do not document en-SG at all... Record which fallback each vendor
used, per call, in the manifest - without it the en-SG results are not
comparable, and 'no vendor supports Singapore English' is itself a
publishable finding."

Pure data lookup against each vendor yaml's create.languages block - a
vendor's language support changing is a YAML edit, never a Python change
(CLAUDE.md: "vendor config is data... never hardcode it"). Works for a
Mode A dial-only call too (no agent creation involved) - this is about what
language we're asking the vendor to operate in, not about having created
the agent ourselves.
"""
from __future__ import annotations

from dataclasses import dataclass


class LanguageResolutionError(RuntimeError):
    pass


@dataclass
class LanguageResolution:
    requested_bcp47: str
    vendor_code: str | None
    fallback_used: bool
    fallback_chain_tried: list[str]

    def as_dict(self) -> dict:
        return {
            "requested_bcp47": self.requested_bcp47,
            "vendor_code": self.vendor_code,
            "fallback_used": self.fallback_used,
            "fallback_chain_tried": self.fallback_chain_tried,
        }


def resolve_language(vendor_cfg: dict, bcp47: str) -> LanguageResolution:
    languages = (vendor_cfg.get("create") or {}).get("languages", {})
    entry = languages.get(bcp47)
    if entry is None:
        raise LanguageResolutionError(
            f"no language entry for {bcp47!r} in this vendor's create.languages block"
        )

    if entry.get("code"):
        return LanguageResolution(requested_bcp47=bcp47, vendor_code=entry["code"],
                                  fallback_used=False, fallback_chain_tried=[])

    tried: list[str] = []
    for fallback_bcp47 in entry.get("fallback", []):
        fallback_entry = languages.get(fallback_bcp47)
        tried.append(fallback_bcp47)
        if fallback_entry and fallback_entry.get("code"):
            return LanguageResolution(requested_bcp47=bcp47, vendor_code=fallback_entry["code"],
                                      fallback_used=True, fallback_chain_tried=tried)

    return LanguageResolution(requested_bcp47=bcp47, vendor_code=None,
                              fallback_used=True, fallback_chain_tried=tried)
