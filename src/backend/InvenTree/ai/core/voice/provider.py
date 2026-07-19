"""Voice Live control-plane policy (WS4-T4).

Pure module: no Django, FastAPI, or Azure SDK imports, so both test runtimes
and the future gateway share one authority for session configuration and the
inbound event allow-list. The binding rules (contract §§0.2, 5.3, 6.5):

- ``create_response`` is always ``false``; Voice Live never answers.
- No tools are ever registered on the realtime session.
- Autonomous provider responses and any tool-call event are policy
  violations, surfaced as ``forbidden`` so the session can fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal[
    "session_ack",
    "transcript_partial",
    "transcript_final",
    "sdp_created",
    "sdp_error",
    "speech_lifecycle",
    "error",
    "ignorable",
    "forbidden",
]

#: Inbound provider event types the gateway acts on.
_EVENT_KINDS: dict[str, EventKind] = {
    "session.created": "session_ack",
    "session.updated": "session_ack",
    "conversation.item.input_audio_transcription.delta": "transcript_partial",
    "conversation.item.input_audio_transcription.completed": "transcript_final",
    "rtc.call.sdp.created": "sdp_created",
    "rtc.call.error": "sdp_error",
    "input_audio_buffer.speech_started": "speech_lifecycle",
    "input_audio_buffer.speech_stopped": "speech_lifecycle",
    "response.created": "speech_lifecycle",
    "response.done": "speech_lifecycle",
    "response.audio.delta": "speech_lifecycle",
    "response.audio.done": "speech_lifecycle",
    "response.audio_transcript.delta": "speech_lifecycle",
    "response.audio_transcript.done": "speech_lifecycle",
    "response.content_part.added": "speech_lifecycle",
    "response.content_part.done": "speech_lifecycle",
    "response.output_item.added": "speech_lifecycle",
    "response.output_item.done": "speech_lifecycle",
    "response.text.delta": "speech_lifecycle",
    "response.text.done": "speech_lifecycle",
    "response.cancelled": "speech_lifecycle",
    "error": "error",
}

_FORBIDDEN_FRAGMENTS = (
    "function_call",
    "tool_call",
    "tool_calls",
)

#: response.* events are only legitimate when they belong to a response the
#: application itself requested through exact TTS.
_RESPONSE_PREFIX = "response."


@dataclass(frozen=True)
class SessionPolicy:
    """The exact realtime session configuration AIMMS will accept."""

    voice_name: str = "en-US-AvaNeural"
    language: str = "en-US"
    transcription_model: str = "azure-speech"
    phrase_hints: tuple[str, ...] = ()
    input_audio_sampling_rate: int = 24000

    def session_update_payload(self) -> dict[str, Any]:
        """Return the exact ``session.update`` body sent on connect."""
        transcription: dict[str, Any] = {
            "model": self.transcription_model,
            "language": self.language,
        }
        if self.phrase_hints:
            transcription["phrase_list"] = list(self.phrase_hints)
        return {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "input_audio_sampling_rate": self.input_audio_sampling_rate,
                "voice": {
                    "type": "azure-standard",
                    "name": self.voice_name,
                    "rate": "1.0",
                },
                "input_audio_transcription": transcription,
                "turn_detection": {
                    "type": "azure_semantic_vad",
                    "prefix_padding_ms": 420,
                    "speech_duration_ms": 80,
                    "silence_duration_ms": 550,
                    "remove_filler_words": False,
                    "languages": [self.language],
                    "create_response": False,
                    "interrupt_response": True,
                    "auto_truncate": True,
                },
                "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
                "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
                "tools": [],
                "tool_choice": "none",
            },
        }


@dataclass
class EventGate:
    """Classify inbound provider events against application expectations."""

    expected_response_ids: set[str] = field(default_factory=set)
    violations: list[str] = field(default_factory=list)
    pending_app_responses: int = 0
    #: Client event ids of in-flight ``response.create`` requests, so a
    #: provider error can be attributed to the request that caused it.
    pending_create_event_ids: set[str] = field(default_factory=set)
    #: Adopted app responses that have not yet reached a terminal event;
    #: used to decide whether a ``response.cancel`` has anything to cancel.
    active_app_response_ids: set[str] = field(default_factory=set)

    def expect_response(self, response_id: str) -> None:
        """Register a response id the application itself requested."""
        if response_id:
            self.expected_response_ids.add(response_id)

    def expect_app_response(self, event_id: str = "") -> None:
        """Register one application ``response.create`` before its id is known.

        Provider-generated response ids are only learned from the
        acknowledging ``response.created`` event, so exact-TTS dispatch
        registers intent here and :meth:`classify` adopts the next id.
        Adoption is first-come: a provider drifting in exactly the
        request-to-ack window could be misattributed, so this gate remains
        telemetry and fail-closed teardown, never the speech authority —
        ``create_response:false`` is what structurally prevents drift.
        """
        self.pending_app_responses += 1
        if event_id:
            self.pending_create_event_ids.add(event_id)

    def abandon_app_response(self, event_id: str = "") -> None:
        """Drop one pending allowance for a failed or rejected request.

        An allowance without a forthcoming acknowledgement would otherwise
        legitimize the next autonomous provider response indefinitely.
        """
        if self.pending_app_responses > 0:
            self.pending_app_responses -= 1
        if event_id:
            self.pending_create_event_ids.discard(event_id)

    def reset_pending(self) -> None:
        """Clear in-flight bookkeeping when its connection goes away."""
        self.pending_app_responses = 0
        self.pending_create_event_ids.clear()
        self.active_app_response_ids.clear()

    def has_active_app_response(self) -> bool:
        """Whether an application-requested response may still be playing."""
        return bool(self.active_app_response_ids)

    def classify(self, event: dict[str, Any]) -> EventKind:
        """Return the event kind, flagging policy violations as forbidden."""
        event_type = str(event.get("type", ""))
        lowered = event_type.lower()
        if any(fragment in lowered for fragment in _FORBIDDEN_FRAGMENTS):
            self.violations.append(event_type)
            return "forbidden"
        if event_type.startswith(_RESPONSE_PREFIX) and event_type not in _EVENT_KINDS:
            self.violations.append(event_type)
            return "forbidden"
        kind = _EVENT_KINDS.get(event_type)
        if kind is None:
            return "ignorable"
        if kind == "error":
            # A rejected request never acknowledges, so its allowance must be
            # dropped — but only when the error is attributable to one of our
            # ``response.create`` requests (the provider echoes the causing
            # client event id). A stale ``response.cancel`` error must never
            # consume the allowance of the answer sent right behind it.
            error_event_id = str(event.get("error", {}).get("event_id", "") or "")
            if error_event_id and error_event_id in self.pending_create_event_ids:
                self.abandon_app_response(error_event_id)
            return kind
        if event_type.startswith(_RESPONSE_PREFIX):
            response_id = str(
                event.get("response", {}).get("id", "") or event.get("response_id", "")
            )
            if response_id and response_id in self.expected_response_ids:
                if event_type in ("response.done", "response.cancelled"):
                    self.active_app_response_ids.discard(response_id)
                return kind
            if event_type == "response.created" and self.pending_app_responses > 0:
                # The acknowledgement of a response the application itself
                # requested through exact TTS: adopt its provider-generated id.
                self.pending_app_responses -= 1
                if response_id:
                    self.expected_response_ids.add(response_id)
                    self.active_app_response_ids.add(response_id)
                return kind
            # An answer nobody asked for: the two-agent drift case.
            self.violations.append(event_type)
            return "forbidden"
        return kind
