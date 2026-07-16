"""WS4-T4/T7/T8 pure-contract tests: session policy, event gate, exact TTS."""

from __future__ import annotations

import pytest
from ai.core.voice.provider import EventGate, SessionPolicy
from ai.core.voice.speech import (
    ExactSpeechViolation,
    build_exact_tts_payload,
    speakable_response_state,
    spoken_summary_hash,
)
from ai.core.voice.transcription import (
    FINAL_EVENT_TYPE,
    PARTIAL_EVENT_TYPE,
    TranscriptEventError,
    is_partial_transcript,
    normalize_final_transcript,
)


class TestSessionPolicy:
    def test_session_update_disables_autonomous_responses(self):
        payload = SessionPolicy().session_update_payload()
        session = payload["session"]
        assert payload["type"] == "session.update"
        assert session["turn_detection"]["create_response"] is False
        assert session["tools"] == []
        assert session["tool_choice"] == "none"

    def test_exact_vad_and_audio_policy(self):
        session = SessionPolicy().session_update_payload()["session"]
        vad = session["turn_detection"]
        assert vad["type"] == "azure_semantic_vad"
        assert vad["prefix_padding_ms"] == 420
        assert vad["speech_duration_ms"] == 80
        assert vad["silence_duration_ms"] == 550
        assert vad["interrupt_response"] is True
        assert vad["auto_truncate"] is True
        assert session["input_audio_noise_reduction"] == {"type": "azure_deep_noise_suppression"}
        assert session["input_audio_echo_cancellation"] == {"type": "server_echo_cancellation"}
        assert session["input_audio_sampling_rate"] == 24000

    def test_phrase_hints_appear_only_when_configured(self):
        bare = SessionPolicy().session_update_payload()["session"]
        assert "phrase_list" not in bare["input_audio_transcription"]
        hinted = SessionPolicy(phrase_hints=("AIMMS", "LOTO")).session_update_payload()["session"]
        assert hinted["input_audio_transcription"]["phrase_list"] == [
            "AIMMS",
            "LOTO",
        ]


class TestEventGate:
    def test_final_and_partial_transcripts_classify(self):
        gate = EventGate()
        assert gate.classify({"type": FINAL_EVENT_TYPE}) == "transcript_final"
        assert gate.classify({"type": PARTIAL_EVENT_TYPE}) == "transcript_partial"

    def test_sdp_events_classify(self):
        gate = EventGate()
        assert gate.classify({"type": "rtc.call.sdp.created"}) == "sdp_created"
        assert gate.classify({"type": "rtc.call.error"}) == "sdp_error"

    def test_tool_call_events_are_forbidden(self):
        gate = EventGate()
        assert gate.classify({"type": "response.function_call_arguments.done"}) == "forbidden"
        assert gate.violations

    def test_autonomous_response_is_forbidden(self):
        gate = EventGate()
        kind = gate.classify({"type": "response.created", "response": {"id": "resp-unrequested"}})
        assert kind == "forbidden"
        assert gate.violations == ["response.created"]

    @pytest.mark.parametrize(
        "event",
        [
            {"type": "response.created"},
            {"type": "response.done", "response": {}},
            {"type": "response.audio.delta", "response_id": ""},
        ],
    )
    def test_response_without_id_is_forbidden(self, event):
        gate = EventGate()
        assert gate.classify(event) == "forbidden"
        assert gate.violations == [event["type"]]

    def test_requested_response_is_allowed(self):
        gate = EventGate()
        gate.expect_response("resp-1")
        kind = gate.classify({"type": "response.done", "response": {"id": "resp-1"}})
        assert kind == "speech_lifecycle"
        assert not gate.violations

    def test_unknown_events_are_ignorable_not_actionable(self):
        gate = EventGate()
        assert gate.classify({"type": "rate_limits.updated"}) == "ignorable"

    def test_unknown_response_event_is_forbidden(self):
        gate = EventGate()
        event = {"type": "response.unrecognized", "response": {"id": "resp-1"}}
        assert gate.classify(event) == "forbidden"
        assert gate.violations == [event["type"]]


class TestExactSpeech:
    TEXT = "I found two likely causes. Confirm the vibration reading first."

    def test_payload_binds_exact_persisted_text(self):
        payload = build_exact_tts_payload(
            persisted_text=self.TEXT,
            persisted_hash=spoken_summary_hash(self.TEXT),
        )
        message = payload["response"]["pre_generated_assistant_message"]
        assert payload["type"] == "response.create"
        assert message["role"] == "assistant"
        assert message["content"] == [{"type": "text", "text": self.TEXT}]

    def test_hash_mismatch_refuses_to_speak(self):
        with pytest.raises(ExactSpeechViolation):
            build_exact_tts_payload(
                persisted_text=self.TEXT + " (paraphrased)",
                persisted_hash=spoken_summary_hash(self.TEXT),
            )

    def test_empty_text_refuses_to_speak(self):
        with pytest.raises(ExactSpeechViolation):
            build_exact_tts_payload(persisted_text="  ", persisted_hash=spoken_summary_hash("  "))

    def test_only_complete_responses_are_speakable(self):
        assert speakable_response_state("complete") is True
        for state in ("incomplete", "canceled", "failed", ""):
            assert speakable_response_state(state) is False


class TestTranscriptNormalization:
    def _event(self, **overrides):
        event = {
            "type": FINAL_EVENT_TYPE,
            "transcript": "The pump is vibrating.",
            "item_id": "item-1",
            "confidence": 0.91,
            "language": "en-US",
        }
        event.update(overrides)
        return event

    def test_final_transcript_normalizes(self):
        final = normalize_final_transcript(self._event())
        assert final.text == "The pump is vibrating."
        assert final.idempotency_key("vs-1") == "voice:vs-1:item-1"
        metadata = final.modality_metadata()
        assert metadata["transcription_confidence"] == 0.91  # noqa: RUF069
        assert "audio" not in str(metadata).lower()

    def test_partial_events_never_normalize(self):
        assert is_partial_transcript({"type": PARTIAL_EVENT_TYPE})
        with pytest.raises(TranscriptEventError):
            normalize_final_transcript({"type": PARTIAL_EVENT_TYPE})

    @pytest.mark.parametrize(
        "overrides",
        [
            {"transcript": ""},
            {"item_id": ""},
            {"confidence": "high"},
            {"confidence": 1.5},
            {"transcript": "x" * 8001},
        ],
    )
    def test_malformed_events_are_rejected(self, overrides):
        with pytest.raises(TranscriptEventError):
            normalize_final_transcript(self._event(**overrides))

    def test_same_item_id_replays_same_turn_key(self):
        first = normalize_final_transcript(self._event())
        second = normalize_final_transcript(self._event())
        assert first.idempotency_key("vs-1") == second.idempotency_key("vs-1")
