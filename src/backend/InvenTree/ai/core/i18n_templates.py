"""S33: per-locale tables for the bounded deterministic template set.

Only templates the SERVER authors verbatim live here — advisory declines,
the grounding downgrade, and the safety boundary line. Model-generated text
is out of scope (it gets a locale hint in instructions instead), and voice
status phrases keep their own allow-listed table in
``ai.core.voice.status_phrases`` because they join a spoken allow-list.

Lookup is fail-safe: an unknown locale or key returns English, because a
wrong answer in the right language is worse than the right answer in
English.
"""

from __future__ import annotations

SAFETY_BOUNDARY = "safety_boundary"
ADVISORY_VOICE_ACTION = "advisory_voice_action"
ADVISORY_VOICE_READONLY = "advisory_voice_readonly"
ADVISORY_TEXT = "advisory_text"
ADVISORY_NEXT_QUESTION = "advisory_next_question"
QUESTION_DECLINED_ACK = "question_declined_ack"
GROUNDING_DOWNGRADE = "grounding_downgrade"

_TEMPLATES: dict[str, dict[str, str]] = {
    SAFETY_BOUNDARY: {
        "en": "This response does not change or confirm any safety status.",
        "es": "Esta respuesta no cambia ni confirma ningún estado de seguridad.",
    },
    ADVISORY_VOICE_ACTION: {
        "en": (
            "I could not prepare that action from the details provided. Say it "
            "again with the required record, quantity, and destination. {safety}"
        ),
        "es": (
            "No pude preparar esa acción con los datos proporcionados. Repítelo "
            "con el registro, la cantidad y el destino requeridos. {safety}"
        ),
    },
    ADVISORY_VOICE_READONLY: {
        "en": (
            "I can help look up the details, but I do not create or change "
            "records by voice. Use the normal authenticated screen. {safety}"
        ),
        "es": (
            "Puedo ayudarte a consultar los detalles, pero no creo ni modifico "
            "registros por voz. Usa la pantalla autenticada habitual. {safety}"
        ),
    },
    ADVISORY_TEXT: {
        "en": (
            "I can discuss that requested change, but this turn cannot create a "
            "proposal or perform an effect. Use the normal authenticated action "
            "surface for an allow-listed operation."
        ),
        "es": (
            "Puedo comentar el cambio solicitado, pero este turno no puede crear "
            "una propuesta ni ejecutar un efecto. Usa la superficie de acción "
            "autenticada habitual para una operación permitida."
        ),
    },
    ADVISORY_NEXT_QUESTION: {
        "en": "What details would you like me to look up first?",
        "es": "¿Qué detalles quieres que consulte primero?",
    },
    QUESTION_DECLINED_ACK: {
        "en": "Okay — tell me a bit more about what you're looking for.",
        "es": "De acuerdo — cuéntame un poco más sobre lo que buscas.",
    },
    GROUNDING_DOWNGRADE: {
        "en": (
            "I found relevant sections in {titles} — verify the procedure in "
            "the manual before acting."
        ),
        "es": (
            "Encontré secciones relevantes en {titles} — verifica el "
            "procedimiento en el manual antes de actuar."
        ),
    },
}


def deterministic_template(key: str, locale: str | None) -> str:
    """Return the template for a locale, falling back to English."""
    table = _TEMPLATES[key]
    if locale:
        base = str(locale).lower().split("-")[0]
        if base in table:
            return table[base]
    return table["en"]


__all__ = [
    "ADVISORY_NEXT_QUESTION",
    "ADVISORY_TEXT",
    "ADVISORY_VOICE_ACTION",
    "ADVISORY_VOICE_READONLY",
    "GROUNDING_DOWNGRADE",
    "QUESTION_DECLINED_ACK",
    "SAFETY_BOUNDARY",
    "deterministic_template",
]
