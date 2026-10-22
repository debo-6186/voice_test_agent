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
    extracted_text: str        # the normalized text (full continuous-transcript window searched)
    matched: bool
    method: str
    matched_script: str | None = None   # Hindi name only: "devanagari" | "romanised"
    source_offset_s: float | None = None   # start of the earliest segment the match was found in -
                                            # provenance only, never a turn boundary (see
                                            # find_entity_match_in_segments's docstring)


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def _digit_run_matches(text: str, expected_digits: str) -> bool:
    """True if expected_digits appears in text as its OWN digit run - an
    extra digit immediately adjacent (leading or trailing) means the agent
    actually said a different, longer number, not this one. Confirmed live,
    hi-IN/A_cafe_snr10: the referee transcript has "987-654-32100" (11
    digits, the agent added an extra trailing 0) where the expected number
    is "9876543210" (10 digits) - plain substring containment on a digits-
    only string scores this as a match purely because the correct 10 digits
    happen to be a PREFIX of the wrong 11-digit run. Working on the
    ORIGINAL (not pre-stripped) text preserves real separator/word
    boundaries, so "(?!\\d)" after the match correctly sees that extra "0"
    and rejects it; optional "[\\s-]*" between digits still tolerates
    "987-654-3210"-style formatting."""
    pattern = r"(?<!\d)" + r"[\s\-]*".join(re.escape(d) for d in expected_digits) + r"(?!\d)"
    return bool(re.search(pattern, text))


def match_phone(raw_text: str, normalized_text: str, expected: dict) -> tuple[bool, str]:
    """Returns (matched, method). expected: {value, digits, ...}.

    Tries RAW text first (preserves a leading zero that
    whisper_normalizer's EnglishTextNormalizer can drop when re-rendering a
    digit run as an integer - CLAUDE.md's documented bug). Falls back to
    NORMALIZED text if that fails - confirmed live on a real referee
    transcript under heavy noise (A_cafe_snr00) that Groq can transcribe a
    phone number as spelled-out WORDS ("nine one four two three three zero
    eight seven seven") rather than numerals; raw text then has no digit
    characters to extract at all, while EnglishTextNormalizer correctly
    converts exactly that phrase to "9142330877". Trying raw first and
    normalized second gets the leading-zero protection in the common case
    while still catching the spelled-out case instead of silently missing a
    number the agent did say correctly."""
    if _digit_run_matches(raw_text, expected["value"]):
        return True, "digits_exact"
    if _digit_run_matches(normalized_text, expected["value"]):
        return True, "digits_exact_normalized_fallback"
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
        # See match_phone's own docstring: raw text first (protects a
        # leading zero that normalization can drop), normalized text as a
        # fallback (catches the referee spelling digits out as words
        # instead of numerals - confirmed live under heavy noise).
        matched, method = match_phone(raw_text, normalized_text, expected)
        return matched, method, None
    if field_type == "date":
        return date_entity_present(normalized_text, lang, expected["value"]), "month_day_containment", None
    if field_type == "person_name":
        return match_name(normalized_text, expected)
    raise ValueError(f"unknown entity field_type: {field_type}")


def find_entity_match_in_segments(field: str, field_type: str, expected: dict, lang: str,
                                  segments: list[dict], search_from_offset_s: float) -> EntityMatch:
    """Searches the WHOLE continuous-call transcript (Groq verbose_json
    segments from referee/groq_asr.py's transcribe_wav() on
    `_full_call.wav`) forward from search_from_offset_s through end of
    call - a correct confirm/read-back anywhere later in the call should
    still count as preservation, exactly as the old per-turn version
    intended.

    search_from_offset_s is deliberately a COARSE lower bound (a turn's
    logged t_offset_s - roughly when the caller started supplying this
    field, or the next turn after it) and NOT a precise turn boundary.
    Precise per-turn boundaries are exactly the thing that broke: our own
    per-turn recorder decided where a turn ended using a live
    silence-detection heuristic, and that heuristic was confirmed live to
    cut off real agent speech mid-sentence (a date read-back present in the
    full call recording but absent from its own turn file). Any scheme that
    still tries to attribute an exact span of the continuous transcript to
    an exact turn inherits the same failure mode one level up.

    Filtered by segment END, not segment START - confirmed live on a real
    call this matters: Groq's segment start timestamps are NOT reliable
    when a short utterance follows a long silence (our own reply playing,
    not in this incoming-only recording at all) - a 19-second segment was
    returned starting AT 16.18s for an utterance that could only have
    begun around 24s, because Whisper folded the preceding silence into
    the segment rather than marking it separately. Filtering by start time
    excluded that segment entirely and produced exactly the false-negative
    class this whole redesign exists to eliminate. Segment END is far more
    trustworthy here (segments run several real seconds, so there's no
    sharp edge to mis-place), and erring permissive on the lower bound
    costs nothing - there is no upper bound either; the search already
    always runs to end of call."""
    window = sorted(
        (s for s in segments if s.get("end", s.get("start", 0.0)) >= search_from_offset_s - 1.0),
        key=lambda s: s.get("start", 0.0),
    )
    text = " ".join(s.get("text", "") for s in window)
    normalized = normalize_for_lang(text, lang)
    matched, method, script = match_entity(field_type, text, normalized, lang, expected)
    source_offset_s = window[0]["start"] if (matched and window) else None
    return EntityMatch(
        field=field, expected=expected.get("value"), extracted_text=normalized,
        matched=matched, method=method, matched_script=script, source_offset_s=source_offset_s,
    )
