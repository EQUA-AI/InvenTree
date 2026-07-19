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
INTERIM_STATUS_DELAY_S = 2.5

#: The complete allow-list; anything not present here is not a status phrase.
ALLOWED_STATUS_PHRASES = frozenset({
    THINKING,
    ANSWER_IN_CHAT,
    TURN_FAILED,
    ANSWER_INCOMPLETE,
})
