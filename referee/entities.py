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


def _name_tokens_present(text_words: list[str], name_value: str) -> set[str]:
    """Which of name_value's own tokens (e.g. "rachel", "hoffman") appear as
    a close fuzzy match to some SINGLE word in the text. Word-for-word, not
    a multi-word sliding window compared character-by-character against the
    whole candidate - that approach (the previous version of this function)
    let unrelated NEIGHBORING words inflate the ratio by raw character
    overlap: "rachel and" scored exactly 0.75 against "rachel hoffman" on a
    real referee transcript that never said "Hoffman" at all, a confirmed
    false positive on 13 of 15 real pilot calls, not a coincidence worth
    keeping."""
    present = set()
    for tok in name_value.lower().split():
        if any(difflib.SequenceMatcher(None, tok, w).ratio() >= NAME_MATCH_THRESHOLD
              for w in text_words):
            present.add(tok)
    return present


def match_name(normalized_text: str, expected: dict) -> tuple[bool, str, str | None]:
    """Returns (matched, method, matched_script). Deliberately accepts a
    first-name-only read-back as a match ("first_name_only") - real agents
    commonly echo back only the first name in natural conversation; that's
    not a defect to score against. A full match (every name token found)
    is still recorded as such, so a scores.jsonl reader can tell the two
    apart rather than treating every name match as equally complete."""
    text_words = normalized_text.lower().split()

    def _check(name_value: str) -> str | None:
        tokens = name_value.lower().split()
        if not tokens:
            return None
        present = _name_tokens_present(text_words, name_value)
        if len(present) == len(tokens):
            return "full_name"
        if tokens[0] in present:
            return "first_name_only"
        return None

    label = _check(expected["value"])
    matched_script = "devanagari" if (label and "romanised" in expected) else None
    if label is None and "romanised" in expected:
        label = _check(expected["romanised"])
        if label is not None:
            matched_script = "romanised"

    if label is None:
        return False, "fuzzy_ratio", None
    return True, f"fuzzy_ratio_{label}", matched_script


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
