"""Date entity-preservation check: did the expected day + month survive
in the referee-transcribed agent text? Containment, not parsing - we
already know the ground truth (config/scenarios.yaml), so the question is
just "do its defining tokens appear," not "what date does this text mean."
"""
from __future__ import annotations

import re

MONTH_NAMES: dict[str, dict[int, str]] = {
    "en-US": {1: "january", 2: "february", 3: "march", 4: "april", 5: "may", 6: "june",
             7: "july", 8: "august", 9: "september", 10: "october", 11: "november", 12: "december"},
    "en-SG": {1: "january", 2: "february", 3: "march", 4: "april", 5: "may", 6: "june",
             7: "july", 8: "august", 9: "september", 10: "october", 11: "november", 12: "december"},
    "es-ES": {1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
             7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"},
    "id-ID": {1: "januari", 2: "februari", 3: "maret", 4: "april", 5: "mei", 6: "juni",
             7: "juli", 8: "agustus", 9: "september", 10: "oktober", 11: "november", 12: "desember"},
    "hi-IN": {1: "जनवरी", 2: "फरवरी", 3: "मार्च", 4: "अप्रैल", 5: "मई", 6: "जून",
             7: "जुलाई", 8: "अगस्त", 9: "सितंबर", 10: "अक्टूबर", 11: "नवंबर", 12: "दिसंबर"},
}

# Alternate hi-IN spellings for the same month, confirmed live on real Telnyx
# transcripts - THREE distinct spellings for October alone have turned up so
# far ("अक्टूबर", "अक्टोबर", "ऑक्टोबर" - the long "o" vowel transliterated
# three different ways, the third using "ऑ", the candra-O sign typically
# reserved for English loanwords). Not exhaustive for every month, just
# every spelling actually observed in this project's own corpus/transcripts
# so far. Add to this as new variants turn up live, rather than guessing all
# twelve months' alternates up front - the recurring pattern (three
# spellings for ONE month already) suggests more are likely still out there
# for months not yet seen enough times to have surfaced a variant.
HI_IN_MONTH_ALT_SPELLINGS: dict[int, list[str]] = {
    # "उक्टूबर" - a FOURTH spelling, confirmed live on a real hi-IN/
    # ElevenLabs batch (9 of 15 calls used this exact spelling, consistent
    # with the TTS/LLM pipeline having its own fixed pronunciation habit,
    # not a per-call fluke) - this module's own comment above already
    # predicted more would turn up ("the recurring pattern... suggests more
    # are likely still out there for months not yet seen enough times").
    # "अक्तूबर" - a FIFTH spelling (त vs ट - dental vs retroflex), same
    # ElevenLabs batch, same reasoning as उक्टूबर above.
    # "अक्तुबर" - a SIXTH spelling, confirmed live on an ElevenLabs retry:
    # short उ vs अक्तूबर's long ू - the exact same vowel-length variation
    # already seen on "शुन्य" vs "शून्य" (referee/entities.py) and तारीक
    # vs तरीक (caller/intent.py). At six variants for one word, this is
    # clearly this project's single most unstable Hindi spelling - treat
    # any future October-field miss as "check for a new vowel-length or
    # consonant variant first" before assuming anything else is wrong.
    10: ["अक्टोबर", "ऑक्टोबर", "उक्टूबर", "अक्तूबर", "अक्तुबर"],
}


def date_entity_present(normalized_text: str, lang: str, expected_value: str) -> bool:
    """expected_value is config/scenarios.yaml's '--MM-DD' ground truth.

    Checks the month name in BOTH the call's own language and English, not
    just the call's language - confirmed live on real hi-IN/Telnyx calls
    (15/15 in one batch) that this project's own referee was scoring every
    single "date" field as unmatched even when the vendor clearly confirmed
    the date, because the vendor said the month in plain English ("Got it!
    You'd like October 15th.") inside an otherwise-Hindi call, and this
    function previously required the Hindi month name ONLY - a real,
    expected code-switching pattern (this project's own corpus has a whole
    utterance class for it), not a transcription error, but the date check
    treated it as entirely absent. English-only languages have nothing
    extra to add here (en-US's own fallback IS the primary check), so the
    set naturally collapses to one name for those."""
    month_num, day_num = int(expected_value[2:4]), int(expected_value[5:7])
    text = normalized_text.lower()

    month_names = {MONTH_NAMES[lang][month_num].lower(), MONTH_NAMES["en-US"][month_num].lower()}
    if lang == "hi-IN":
        month_names.update(s.lower() for s in HI_IN_MONTH_ALT_SPELLINGS.get(month_num, []))
    if not any(name in text for name in month_names):
        return False

    day_re = re.compile(rf"\b0?{day_num}(?:st|nd|rd|th)?\b")
    return bool(day_re.search(text))
