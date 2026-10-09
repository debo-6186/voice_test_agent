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

INTENTS = ["ask_name", "ask_phone", "ask_date", "confirm", "repair", "closing"]

# Checked in this order - more specific patterns (ask_date's "what date")
# before vaguer ones (confirm's generic phrasing) so a compound sentence
# like "Got it, thank you. And what date would you like?" resolves to the
# question being asked, not the acknowledgement preceding it.
_TIER1_PATTERNS: dict[str, list[str]] = {
    "repair": [r"didn.?t catch", r"say that again", r"\brepeat\b", r"\bclarify\b"],
    "ask_date": [r"\bwhat date\b", r"\bdate would you like\b", r"\bwhen.*appointment\b"],
    "ask_phone": [r"\bphone number\b", r"\bhandphone\b", r"\bcontact number\b", r"\bcallback number\b"],
    "ask_name": [r"\byour name\b", r"\bget your name\b", r"\bmay i have your name\b"],
    "closing": [r"\bhave a good day\b", r"\ball booked\b", r"\bthank you for calling\b", r"\bnoted your number\b"],
    "confirm": [r"\blet me confirm\b", r"\bis that correct\b", r"\breading.*back\b"],
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


@dataclass
class Classification:
    intent: str
    confidence: float
    tier: int              # 1, 2, or 3 - the divergence metric (docs/stack.md)
    needs_adaptive: bool    # True -> caller/adaptive.py must generate a reply


def classify(text: str, tier3_threshold: float = 0.75) -> Classification:
    t1 = tier1_classify(text)
    if t1 is not None:
        return Classification(t1, 1.0, tier=1, needs_adaptive=False)
    intent, confidence = tier2_classify(text)
    if confidence < tier3_threshold:
        return Classification(intent, confidence, tier=3, needs_adaptive=True)
    return Classification(intent, confidence, tier=2, needs_adaptive=False)
