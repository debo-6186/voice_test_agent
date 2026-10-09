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
}
