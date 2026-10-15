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


def date_entity_present(normalized_text: str, lang: str, expected_value: str) -> bool:
    """expected_value is config/scenarios.yaml's '--MM-DD' ground truth."""
    month_num, day_num = int(expected_value[2:4]), int(expected_value[5:7])
    text = normalized_text.lower()

    month_name = MONTH_NAMES[lang][month_num]
    if month_name.lower() not in text:
        return False

    day_re = re.compile(rf"\b0?{day_num}(?:st|nd|rd|th)?\b")
    return bool(day_re.search(text))
