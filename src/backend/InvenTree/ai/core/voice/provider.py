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

    def expect_response(self, response_id: str) -> None:
        """Register a response id the application itself requested."""
        if response_id:
            self.expected_response_ids.add(response_id)

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
        if event_type.startswith(_RESPONSE_PREFIX):
            response_id = str(
                event.get("response", {}).get("id", "") or event.get("response_id", "")
            )
            if not response_id or response_id not in self.expected_response_ids:
                # An answer nobody asked for: the two-agent drift case.
                self.violations.append(event_type)
                return "forbidden"
        return kind
