"""Spoken response templates for the mock agent.

Deliberately generic (never echoes back the heard value) - the point of this
fixture is protocol/timing/error-injection fidelity, not natural-language
generation sophistication. Templates are picked purely from the scenario's
known turn structure (config/scenarios.yaml) plus the decided outcome; a real
vendor would never get to see the script, but this fixture legitimately may.
"""
from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "repair": {
        "en-US": "Sorry, I didn't catch that. Could you say that again?",
        "en-SG": "Sorry ah, can you repeat that again?",
        "hi-IN": "माफ़ कीजिये, मैं समझ नहीं पाया। क्या आप फिर से बता सकते हैं?",
        "es-ES": "Perdone, no le he entendido bien. ¿Puede repetirlo, por favor?",
        "id-ID": "Maaf, saya kurang jelas dengar. Bisa tolong diulang?",
    },
    "ask_phone_after_name": {
        "en-US": "Thanks, and could I get your phone number please?",
        "en-SG": "Thanks ah, can I get your handphone number?",
        "hi-IN": "धन्यवाद, और क्या मैं आपका फ़ोन नंबर ले सकती हूँ?",
        "es-ES": "Gracias, ¿y me puede dar su número de teléfono?",
        "id-ID": "Terima kasih, boleh saya minta nomor telepon Anda?",
    },
    # Phase 6 verification-only (S3_reordered_test): asks for name LAST
    # instead of first, to prove the caller reacts to intent, not file order.
    "ask_name_after_date": {
        "en-US": "Great, and just so I have it on file, could I get your name please?",
        "en-SG": "Great, and just so I have it on file, can I get your name?",
        "hi-IN": "बहुत बढ़िया, और रिकॉर्ड के लिए, क्या मैं आपका नाम ले सकती हूँ?",
        "es-ES": "Perfecto, y para mis registros, ¿me puede dar su nombre?",
        "id-ID": "Baik, dan untuk catatan saya, boleh saya minta nama Anda?",
    },
    "ask_date_after_phone": {
        "en-US": "Got it, thank you. And what date would you like for the appointment?",
        "en-SG": "Okay got it, and what date you want for the appointment?",
        "hi-IN": "ठीक है, धन्यवाद। और आप अपॉइंटमेंट के लिए कौन सी तारीख चाहेंगे?",
        "es-ES": "Entendido, gracias. ¿Y para qué fecha quiere la cita?",
        "id-ID": "Baik, terima kasih. Dan tanggal berapa yang Anda mau untuk appointment-nya?",
    },
    "closing_after_phone": {
        "en-US": "Thank you, I've noted your number. Have a good day.",
        "en-SG": "Okay thank you, noted your number already. Have a good day ah.",
        "hi-IN": "धन्यवाद, मैंने आपका नंबर नोट कर लिया है। आपका दिन शुभ हो।",
        "es-ES": "Gracias, he anotado su número. Que tenga buen día.",
        "id-ID": "Terima kasih, nomornya sudah saya catat. Semoga harinya menyenangkan.",
    },
    "closing_after_date": {
        "en-US": "Perfect, you're all booked for that date. Thank you for calling.",
        "en-SG": "Okay perfect, you're all booked for that date. Thanks for calling ah.",
        "hi-IN": "बहुत बढ़िया, आपकी अपॉइंटमेंट उस तारीख के लिए बुक हो गई है। कॉल करने के लिए धन्यवाद।",
        "es-ES": "Perfecto, ya está reservada su cita para esa fecha. Gracias por llamar.",
        "id-ID": "Oke sip, jadwal Anda sudah dibooking untuk tanggal itu. Terima kasih sudah menelepon.",
    },
}

# For each scenario, the template to use after a successful (ok/silent_error)
# turn, keyed by turn index (0-based). The repair template is universal and
# not listed here - any turn uses "repair" when the outcome calls for it.
SCENARIO_FLOW: dict[str, list[str]] = {
    "S1_phone_only": ["closing_after_phone"],
    "S2_name_phone": ["ask_phone_after_name", "closing_after_phone"],
    "S3_name_phone_date": ["ask_phone_after_name", "ask_date_after_phone", "closing_after_date"],
    "S3_reordered_test": ["ask_date_after_phone", "ask_name_after_date", "closing_after_date"],
}


def response_text(scenario_id: str, turn_index: int, outcome: str, lang: str) -> str:
    if outcome == "repair":
        template = "repair"
    else:
        template = SCENARIO_FLOW[scenario_id][turn_index]
    return TEMPLATES[template][lang]


def is_closing(scenario_id: str, turn_index: int, outcome: str) -> bool:
    if outcome == "repair":
        return False
    return SCENARIO_FLOW[scenario_id][turn_index].startswith("closing")
