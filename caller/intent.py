"""3-tier intent classifier for the real caller (Phase 6; PLAN.md §6,
docs/stack.md). Decides WHETHER to escalate to the adaptive fallback; the
actual tier-3 generation lives in caller/adaptive.py.

Tier 1: regex, free, catches the common case.
Tier 2: Laya `choice` - verified live against real mock-agent response text
before building this (3/4 correct, one genuine miss at 0.698 confidence on
an ambiguous closing/confirm line - the concrete reason the threshold below
sits above docs/stack.md's quoted 0.75-0.83 uncalibrated mean, not inside it).
Tier 3: escalation only - see caller/adaptive.py for the LLM call itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

INTENTS = ["ask_name", "ask_phone", "ask_date", "confirm", "repair", "closing", "check_in"]

# Checked in this order - more specific patterns (ask_date's "what date")
# before vaguer ones (confirm's generic phrasing) so a compound sentence
# like "Got it, thank you. And what date would you like?" resolves to the
# question being asked, not the acknowledgement preceding it.
#
# check_in exists only because of what tier 3's own latency causes: while
# we're generating an adaptive reply (ASR -> Laya -> Gemini -> TTS ->
# degrade, several real seconds), we aren't listening at all, so a vendor
# agent that re-prompts "are you still there?" during that window gets
# answered as if it had asked the ORIGINAL question - verified live on a
# real ElevenLabs call, which re-prompted twice before our reply arrived.
# Catching this at tier 1 means it costs nothing and never touches Gemini.
_TIER1_PATTERNS: dict[str, list[str]] = {
    # Hindi additions (hi-IN) alongside each intent's existing English
    # patterns - NOT translations of the English regex syntax, separate
    # literal Hindi phrases. Before this, tier 1 was English-only: lower()
    # on Devanagari text can never match a Latin-alphabet pattern, so every
    # single Hindi utterance was structurally forced past tier 1 into tier
    # 2/3 regardless of how unambiguous the phrasing was - confirmed as the
    # main driver of hi-IN's abnormally high tier-3 rate (CLAUDE.md's
    # uncalibrated-Laya warning compounding a gap that had nothing to do
    # with Laya at all). Phrases chosen from this project's own Hindi
    # response templates (mock_agent/responses.py) plus natural variants -
    # checked against every existing hi-IN template below to confirm no
    # cross-intent false positives (e.g. "धन्यवाद" alone is deliberately
    # NOT a closing pattern - it also appears in the ask_phone template).
    "check_in": [r"are you (still )?there", r"\bstill on the line\b",
                r"\bcan you hear me\b", r"^\s*hello\??\s*$",
                r"अभी भी (लाइन पर|साथ)", r"सुन (पा|रह) रह", r"^\s*हैलो\??\s*$",
                # Telnyx's own silence-timeout warning, confirmed live hi-IN:
                # "It seems like the line is quiet. I'll go ahead and
                # disconnect now. Take care." - semantically the SAME
                # "checking because it thinks the line is dead" category as
                # "are you still there", just a different vendor's phrasing,
                # not covered by the patterns above. Repeated THREE TIMES in
                # one real call (ctl_noise_only) before the real conversation
                # continued - nothing in tier 1 caught it, so each occurrence
                # fell through to Laya/tier-3, slow enough that the vendor's
                # own timeout fired again before our reply arrived. check_in's
                # existing reply ("Sorry, yes, I'm still here") already
                # answers this phrasing correctly without any new text -
                # catching it at tier 1 just makes the answer instant instead
                # of several LLM-latency seconds late.
                r"line is quiet", r"go ahead and disconnect", r"\bdisconnect now\b"],
    "repair": [r"didn.?t catch", r"say that again", r"\brepeat\b", r"\bclarify\b",
              r"समझ (नहीं|नही) (पाया|पायी|आया)", r"फिर से बता", r"दोहरा"],
    "ask_date": [r"\bwhat date\b", r"\bdate would you like\b", r"\bwhen.*appointment\b",
                r"कौन सी तारीख", r"किस तारीख", r"तारीख चाहेंगे", r"कब.{0,15}अपॉइंटमेंट"],
    "ask_phone": [r"\bphone number\b", r"\bhandphone\b", r"\bcontact number\b", r"\bcallback number\b",
                 r"फ़?ोन नंबर", r"मोबाइल नंबर", r"संपर्क नंबर", r"नंबर (दे|ले) सक"],
    "ask_name": [r"\byour name\b", r"\bget your name\b", r"\bmay i have your name\b",
                r"आपका नाम", r"नाम (बता|ले) सक"],
    "closing": [r"\bhave a good day\b", r"\ball booked\b", r"\bthank you for calling\b", r"\bnoted your number\b",
               r"दिन शुभ हो", r"बुक हो (गई|गयी) है", r"कॉल करने के लिए धन्यवाद", r"अलविदा"],
    "confirm": [r"\blet me confirm\b", r"\bis that correct\b", r"\breading.*back\b",
               r"क्या यह सही है", r"कन्फर्म कर"],
}

LAYA_QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": "What is the agent asking the caller for, or doing?",
        "criteria": {
            "ask_name": "requesting the caller's name",
            "ask_phone": "requesting a phone or contact number",
            "ask_date": "requesting a date or appointment time",
            "confirm": "reading a value back for confirmation",
            "repair": "asking the caller to repeat or clarify",
            "closing": "ending the call",
        },
    }
}

_laya_agent = None


def _get_laya():
    """Loaded once, lazily, at module scope - same pattern as
    mock_agent/asr.py's whisper model (never per-call)."""
    global _laya_agent
    if _laya_agent is None:
        import laya
        _laya_agent = laya.load("convaiinnovations/laya", subfolder="multilingual")
    return _laya_agent


def warmup() -> None:
    """Forces Laya's model load now instead of on the first real tier-2
    classification - verified live that cold load costs ~15s, which landed
    mid-call (a live vendor agent waiting for our reply) before this existed.
    Call once before dialing, not per-call."""
    _get_laya()


def tier1_classify(text: str) -> str | None:
    t = text.lower()
    for intent, patterns in _TIER1_PATTERNS.items():
        for p in patterns:
            if re.search(p, t):
                return intent
    return None


def tier2_classify(text: str) -> tuple[str, float]:
    agent = _get_laya()
    r = agent.predict(text, LAYA_QUESTIONS)
    ans = r["answers"]["intent"]
    return ans["choice"], float(ans["confidence"])


# Real affirmation/agreement words - not translations of each other, the
# actual particles a caller-facing confirm would use in each supported
# language. Deliberately separate from _TIER1_PATTERNS' intent-detection
# patterns above: this is used downstream by state_machine.py to decide
# whether a "confirm"-classified utterance is trustworthy enough to mark
# every remaining field collected, not to classify intent itself.
_AFFIRMATION_PATTERNS = [
    r"\byes\b", r"\bcorrect\b", r"\bthat.?s right\b", r"\byep\b", r"\byeah\b",
    # Deliberately NOT "ठीक है" alone ("okay"/"alright") - confirmed live
    # it's too generic, common in plain filler that confirms nothing (e.g.
    # "ठीक है प्रिया जी, मैं आपकी मदद करूँगी" - "okay Priya, I'll help you" -
    # no actual confirmation at all). "जी हां"/"हां"/"हाँ" (yes) and
    # "सही है" (that's correct) are specific enough to keep - EXCEPT "हां"/
    # "हाँ" are also literal substrings of three extremely common, totally
    # unrelated words: यहां/वहां/कहां ("here"/"there"/"where", anusvara
    # spelling) and यहाँ/वहाँ/कहाँ (same three, chandrabindu spelling).
    # Confirmed live, hi-IN/C_jitter30: a plain greeting - "मैं आपकी मदद के
    # लिए यहां हूँ" ("I'm here to help you") - matched "हां" as if it were
    # the word "yes", via plain substring search, and got treated as a real
    # confirmation with nothing to confirm. "\b" word-boundary anchoring
    # does NOT fix this for Devanagari in Python's `re` - combining marks
    # (matras/anusvara) aren't classified as \w, so \b lands in the wrong
    # place even for a standalone "हां" (confirmed live: \bहां\b failed to
    # match "हां, बिल्कुल" too). A negative lookbehind for the three known
    # colliding prefixes is the reliable fix here, not \b.
    r"(?<![यवक])हां", r"(?<![यवक])हाँ", r"सही है", r"जी हां",
    r"\bsí\b", r"\bcorrecto\b", r"\bexacto\b", r"\beso es\b",
    r"\bbetul\b", r"\bbenar\b", r"\biya\b",
]


def looks_like_confirmation(text: str) -> bool:
    """Cheap, language-agnostic content check: does this utterance plausibly
    confirm something, as opposed to being classified "confirm" by Laya with
    nothing actually behind it? Confirmed live, hi-IN/C_jitter30: a jitter-
    clipped question fragment ("को confirm करवाना चाहते हैं?" - "...do you
    want to confirm?", missing its subject entirely) classified as confirm
    at 0.992 confidence - high enough to bypass the tier-3 gate completely -
    with no digit, no date term, and no affirmation word in it at all.
    state_machine.py's confirm branch then marked every remaining field
    collected anyway, with nothing ever actually given.

    Deliberately loose, NOT full entity matching against a specific
    expected value: state_machine.py's confirm branch exists specifically
    to trust a BARE affirmation like "yes, that's right" that never repeats
    any value at all - exact entity matching would reject that case too,
    which is the one thing this branch is FOR. True if the text contains
    EITHER a real affirmation word/particle OR at least one digit or a
    month name in any supported language - i.e. it is actually saying
    something, not just a content-free fragment that happened to get
    labelled "confirm"."""
    from referee.dates import MONTH_NAMES   # deferred: avoids a module-load-
                                             # order dependency on referee
                                             # for every caller.intent import
    t = text.lower()
    if re.search(r"\d", t):
        return True
    for lang_months in MONTH_NAMES.values():
        if any(name.lower() in t for name in lang_months.values()):
            return True
    return any(re.search(p, t) for p in _AFFIRMATION_PATTERNS)


@dataclass
class Classification:
    intent: str
    confidence: float
    tier: int              # 1, 2, or 3 - the divergence metric (docs/stack.md)
    needs_adaptive: bool    # True -> caller/adaptive.py must generate a reply


_TOO_SHORT_TO_CLASSIFY_CHARS = 8


def classify(text: str, tier3_threshold: float = 0.75) -> Classification:
    t1 = tier1_classify(text)
    if t1 is not None:
        return Classification(t1, 1.0, tier=1, needs_adaptive=False)
    # Too short/fragmentary to carry real classification signal - confirmed
    # live, hi-IN/C_jitter30: under severe jitter, whole turns of agent
    # speech came through as 1-3 character fragments ("876", "T-CAP",
    # "877", "6 5 4"). Laya still produces a confident-SOUNDING guess from
    # these (e.g. "closing" at 0.55), which then drives a BRAND NEW
    # adaptive LLM call each time, restating the same remaining fields in
    # slightly different wording turn after turn - 19 turns and 220s to do
    # what should have taken 5, with no new information ever actually
    # exchanged. Treating a fragment this short as "repair" instead - free,
    # deterministic, skips both Laya and the LLM - is both cheaper and more
    # honest: there genuinely isn't enough signal here to classify, so say
    # so (ask to repeat) instead of guessing confidently from scraps.
    # Threshold picked from the real fragments observed, well below any
    # genuine short utterance tier 1 already catches above (e.g. "hello?").
    if len(text.strip()) < _TOO_SHORT_TO_CLASSIFY_CHARS:
        return Classification("repair", 1.0, tier=1, needs_adaptive=False)
    intent, confidence = tier2_classify(text)
    if confidence < tier3_threshold:
        return Classification(intent, confidence, tier=3, needs_adaptive=True)
    return Classification(intent, confidence, tier=2, needs_adaptive=False)
