"""Text normalisation before entity matching - NFC first, then a
language-routed normaliser, exact order docs/referees.md specifies.

Verified live (not assumed) that the bug CLAUDE.md warns about is real on
this project's own data: BasicTextNormalizer() alone turns the ground-truth
Hindi name "प्रिया शर्मा" into "प र य शर म " (combining marks stripped,
unreadable) - BasicTextNormalizer(preserve_marks=True) preserves it
correctly. That confirmed API is what's used here, not the package's
separate HindiNormalizer/DevanagariNormalizer classes (a different,
nukta/nasal-aware approach with no preserve_marks concept - a plausible
future upgrade, not what CLAUDE.md's literal instruction maps to).
"""
from __future__ import annotations

import unicodedata

from whisper_normalizer.basic import BasicTextNormalizer
from whisper_normalizer.english import EnglishTextNormalizer

_NORMALIZER_FACTORIES = {
    "en-US": EnglishTextNormalizer,
    "en-SG": EnglishTextNormalizer,
    "hi-IN": lambda: BasicTextNormalizer(preserve_marks=True),
    "es-ES": BasicTextNormalizer,
    "id-ID": BasicTextNormalizer,
}

_normalizer_cache: dict[str, object] = {}


def _normalizer_for(lang: str):
    if lang not in _normalizer_cache:
        _normalizer_cache[lang] = _NORMALIZER_FACTORIES[lang]()
    return _normalizer_cache[lang]


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def normalize_for_lang(text: str, lang: str) -> str:
    return _normalizer_for(lang)(nfc(text))
