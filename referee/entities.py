"""Entity-preservation matching: did the field we already know the ground
truth for survive in the referee-transcribed agent text? We scripted the
caller, so the answer key (config/scenarios.yaml's entities blocks) is
already known - this is a containment/similarity check against that known
value, not blind extraction or NER.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from referee.dates import date_entity_present
from referee.normalize import normalize_for_lang

NAME_MATCH_THRESHOLD = 0.75   # unvalidated - calibrate against real mock-agent
                              # runs (Phase 8 verification step 5/6) before
                              # trusting it on live vendor calls


@dataclass
class EntityMatch:
    field: str
    expected: str
    extracted_text: str        # the turn's normalized text the match was found in
    matched: bool
    method: str
    matched_script: str | None = None   # Hindi name only: "devanagari" | "romanised"
    source_turn_i: int | None = None


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def match_phone(normalized_text: str, expected: dict) -> tuple[bool, str]:
    """Returns (matched, method). expected: {value, digits, ...}."""
    digits = _digits_only(normalized_text)
    if expected["value"] in digits:
        return True, "digits_exact"
    return False, "digits_exact"


def match_name(normalized_text: str, expected: dict) -> tuple[bool, str, str | None]:
    """Returns (matched, method, matched_script)."""
    text_lower = normalized_text.lower()

    def _ratio(candidate: str) -> float:
        return difflib.SequenceMatcher(None, candidate.lower(), text_lower).ratio()

    # SequenceMatcher.ratio() compares the WHOLE text, which punishes a short
    # name against a long surrounding sentence - find the best-matching
    # contiguous window of the name's own length instead.
    def _best_window_ratio(candidate: str) -> float:
        words = text_lower.split()
        n = max(1, len(candidate.split()))
        if len(words) <= n:
            return _ratio(candidate)
        best = 0.0
        for i in range(len(words) - n + 1):
            window = " ".join(words[i:i + n])
            best = max(best, difflib.SequenceMatcher(None, candidate.lower(), window).ratio())
        return best

    best_ratio = _best_window_ratio(expected["value"])
    best_script = "devanagari" if "romanised" in expected else None
    if "romanised" in expected:
        romanised_ratio = _best_window_ratio(expected["romanised"])
        if romanised_ratio > best_ratio:
            best_ratio, best_script = romanised_ratio, "romanised"

    return best_ratio >= NAME_MATCH_THRESHOLD, "fuzzy_ratio", best_script


def match_entity(field_type: str, raw_text: str, normalized_text: str, lang: str,
                 expected: dict) -> tuple[bool, str, str | None]:
    if field_type == "phone":
        # Digits only, from the RAW (un-normalized) text - whisper_normalizer's
        # number-word normalization re-renders a digit run as an integer and
        # silently drops a meaningful leading zero ("914-233-0877" ->
        # "914 233 877"), confirmed live on a real referee transcript. Same
        # class of bug as the Hindi combining-mark issue CLAUDE.md documents:
        # correct for WER scoring, wrong for literal digit preservation.
        # Digit extraction itself needs no case/mark normalization at all.
        matched, method = match_phone(raw_text, expected)
        return matched, method, None
    if field_type == "date":
        return date_entity_present(normalized_text, lang, expected["value"]), "month_day_containment", None
    if field_type == "person_name":
        return match_name(normalized_text, expected)
    raise ValueError(f"unknown entity field_type: {field_type}")


def find_entity_match(field: str, field_type: str, expected: dict, lang: str,
                      turn_log: list[dict], search_from_i: int) -> EntityMatch:
    """Searches turn_log forward from search_from_i (the turn AFTER the
    caller supplied this field) through end of call - a correct confirm/
    read-back anywhere later in the call should still count as preservation,
    not just the immediately-next turn."""
    best: EntityMatch | None = None
    for row in turn_log:
        i = row.get("i")
        if i is None or i < search_from_i:
            continue
        agent_text = row.get("agent_text")
        if not agent_text:
            continue
        normalized = normalize_for_lang(agent_text, lang)
        matched, method, script = match_entity(field_type, agent_text, normalized, lang, expected)
        candidate = EntityMatch(
            field=field, expected=expected.get("value"), extracted_text=normalized,
            matched=matched, method=method, matched_script=script, source_turn_i=i,
        )
        if matched:
            return candidate   # first preservation anywhere later wins
        if best is None:
            best = candidate   # keep the earliest attempt for provenance if never matched

    return best or EntityMatch(field=field, expected=expected.get("value"), extracted_text="",
                               matched=False, method="no_turns_after_field", source_turn_i=None)
