"""Voice wire-contract models (S43).

The session and turn payloads used to be hand-built dicts in ``routes.py``
with a hand-mirrored TypeScript copy in ``src/frontend/lib/types/Voice.tsx``
— drift between them was undetectable. These pydantic models are now the
single source: ``routes.py`` builds them (behavior-identical
``model_dump()`` output), and ``manage.py generate_wire_contract`` walks
``model_fields`` to emit the TypeScript interfaces.

By design there is no field for an Azure credential, provider URL, token,
or session-configuration authority — the server never sends one and the
client must not model one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class VoiceTransportsAllowed(BaseModel):
    """Which transports this deployment permits."""

    model_config = ConfigDict(extra="forbid")

    webrtc: bool
    relay: bool


class VoiceSessionPayload(BaseModel):
    """One authenticated voice session, as sent to the client."""

    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    thread_id: str
    transport: Literal["webrtc", "relay"] | None
    transports_allowed: VoiceTransportsAllowed
    webrtc_preview: bool
    turn_count: int
    policy_version: str
    terminal_reason: str | None


class VoiceSpokenPayload(BaseModel):
    """The validated spoken form of a completed turn."""

    model_config = ConfigDict(extra="forbid")

    utterance_id: str
    spoken_summary: str
    spoken_summary_hash: str
    playback_state: str


class VoicePendingQuestionOption(BaseModel):
    """One selectable option on a structured question (S22)."""

    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    kind: str | None = None
    description: str | None = None
    recommended: bool | None = None


class VoicePendingQuestion(BaseModel):
    """Wire shape of a pending structured question on the voice rail."""

    model_config = ConfigDict(extra="allow")

    kind: str
    interrupt_id: str
    question_text: str
    options: list[VoicePendingQuestionOption]
    expires_at: str | None = None
    source: str | None = None


class VoiceTurnResponse(BaseModel):
    """One completed (or replayed) voice turn."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    thread_id: str
    turn_id: str
    message: str
    workflow_used: str | None
    response_state: str
    replayed: bool
    spoken: VoiceSpokenPayload | None
    pending_question: VoicePendingQuestion | None = None


#: Every error code the SERVER can send a voice client (exception ``code``
#: attributes in ``voice.services.realtime`` and ``ai.core.voice.signaling``
#: plus the HTTP ``detail`` literals in ``routes.py``). Client-minted codes
#: (MICROPHONE_DENIED, BROWSER_UNSUPPORTED) are the frontend's own and stay
#: hand-listed there; the generated union covers this tuple. A source test
#: (test_voice_wire.py) asserts this list never falls behind the code.
SERVER_VOICE_ERROR_CODES: tuple[str, ...] = (
    "VOICE_SESSION_UNAVAILABLE",
    "VOICE_SESSION_FORBIDDEN",
    "VOICE_SESSION_LIMIT",
    "VOICE_SESSION_EXPIRED",
    "IDEMPOTENCY_CONFLICT",
    "VOICE_SIGNALING_FAILED",
    "VOICE_TRANSPORT_UNAVAILABLE",
    "VOICE_TRANSCRIPT_INCOMPLETE",
    "VOICE_RESPONSE_INCOMPLETE",
)


__all__ = [
    "SERVER_VOICE_ERROR_CODES",
    "VoicePendingQuestion",
    "VoicePendingQuestionOption",
    "VoiceSessionPayload",
    "VoiceSpokenPayload",
    "VoiceTransportsAllowed",
    "VoiceTurnResponse",
]
