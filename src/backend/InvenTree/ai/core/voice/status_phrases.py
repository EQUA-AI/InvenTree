"""Versioned static status phrases for eyes-free voice sessions (§7.6).

Pure module. Field technicians may be unable to look at a screen, so every
turn outcome must be audible. Only these exact application-selected phrases
may be spoken as status; they carry no diagnosis, recommendation, or safety
content, and each is persisted as a ``VoiceUtterance`` before the exact-TTS
request, like any other speech.
"""

from __future__ import annotations

#: Recorded with each status utterance so a phrase change is auditable.
STATUS_PHRASE_POLICY_VERSION = "status-v1"

#: Spoken after a processing delay so the technician knows the turn landed.
THINKING = "Let me check that."

#: Spoken when a completed answer cannot be read aloud (too long or has no
#: schema-valid spoken form); the visible chat remains the full record.
ANSWER_IN_CHAT = "The answer is ready in the chat."

#: Spoken when the turn failed outright.
TURN_FAILED = "Sorry, I could not process that. Please try again."

#: Spoken when the turn ended without a complete answer (bounded review
#: exhausted, terminal replay); silence here would strand an eyes-free user.
ANSWER_INCOMPLETE = "I could not finish that answer. Please try again."

#: Seconds of processing before the thinking phrase is spoken. Short turns
#: finish first and are never preceded by filler.
#: Raised from 2.5 s after measurement: 30 of 32 turns in the 2026-07-26 test
#: crossed that threshold, so "Let me check that" was the default experience
#: rather than a signal that something was taking unusually long. Social turns
#: and the bounded planner (see wf8 _is_social_turn and
#: voice_write_plan_timeout_s) now finish below this.
INTERIM_STATUS_DELAY_S = 4.0

#: S33: per-locale variants of the SAME four phrases. Every entry joins the
#: allow-list below, so the persist-before-speak hash discipline is identical
#: in every language — a localized phrase is allow-listed or it is not spoken.
#: Keys are the English phrases (the canonical identity of each status).
LOCALIZED_STATUS_PHRASES: dict[str, dict[str, str]] = {
    "es": {
        THINKING: "Déjame comprobarlo.",
        ANSWER_IN_CHAT: "La respuesta está lista en el chat.",
        TURN_FAILED: "Lo siento, no pude procesarlo. Inténtalo de nuevo.",
        ANSWER_INCOMPLETE: ("No pude terminar esa respuesta. Inténtalo de nuevo."),
    },
}


def localized_status_phrase(phrase: str, locale: str | None) -> str:
    """Return the locale's variant of a canonical status phrase.

    Unknown locales and unknown phrases fall back to the English input —
    a wrong-language phrase is worse than an English one, and the caller's
    allow-list check runs on the RETURNED value either way.
    """
    if not locale:
        return phrase
    table = LOCALIZED_STATUS_PHRASES.get(str(locale).lower().split("-")[0])
    if not table:
        return phrase
    return table.get(phrase, phrase)


#: The complete allow-list; anything not present here is not a status phrase.
ALLOWED_STATUS_PHRASES = frozenset(
    {THINKING, ANSWER_IN_CHAT, TURN_FAILED, ANSWER_INCOMPLETE}
    | {localized for table in LOCALIZED_STATUS_PHRASES.values() for localized in table.values()}
)
