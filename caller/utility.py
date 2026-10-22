"""Generic, non-scenario caller phrases - not per-field content, so they
live separately from config/scenarios.yaml's turn definitions."""
from __future__ import annotations

UTILITY_PHRASES: dict[str, dict[str, str]] = {
    "confirm_ack": {
        "en-US": "Yes, that's right, thank you.",
        "en-SG": "Yes correct, thank you.",
        "hi-IN": "हाँ, यह सही है, धन्यवाद।",
        "es-ES": "Sí, eso es correcto, gracias.",
        "id-ID": "Iya, itu benar, terima kasih.",
    },
    "check_in_ack": {
        "en-US": "Sorry, yes, I'm still here.",
        "en-SG": "Sorry ah, yes, still here.",
        "hi-IN": "माफ़ कीजिये, हां, मैं यहीं हूँ।",
        "es-ES": "Perdone, sí, sigo aquí.",
        "id-ID": "Maaf, iya, saya masih di sini.",
    },
    # Used after repeating the same field twice and the agent still asks to
    # repeat a third time - state_machine.py's repair handling gives up at
    # that point rather than looping forever, and this acknowledges that
    # without inventing new content (same reasoning as every other
    # "known-safe, don't call the LLM" utility phrase here).
    "repair_exhausted_ack": {
        "en-US": "I'm sorry, it seems the line isn't clear right now - let's move on.",
        "en-SG": "Sorry ah, line not very clear now - let's continue lah.",
        "hi-IN": "माफ़ कीजिये, लगता है लाइन अभी साफ़ नहीं है, हम आगे बढ़ते हैं।",
        "es-ES": "Lo siento, parece que la línea no se oye bien ahora - sigamos adelante.",
        "id-ID": "Maaf, sepertinya saluran tidak jelas sekarang - kita lanjut saja ya.",
    },
}
