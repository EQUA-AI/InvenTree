"""S49: classic-dialect → AG-UI spec translation."""

# ruff: noqa: E402

from __future__ import annotations

import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.agui.translate import (
    _SKIPPED,
    CUSTOM_CHANNELS,
    SpecTranslator,
    encode_sse,
)
from ai.core.streaming import EventType

TS = "2026-08-13T12:00:00+00:00"
TS_MS = 1786622400000


def _t() -> SpecTranslator:
    return SpecTranslator(thread_id="thread_req", run_id="run_req")


def _record(event_type: EventType, **payload) -> dict:
    return {
        "type": event_type.value,
        "timestamp": TS,
        "threadId": "thread_stored",
        "runId": "run_stored",
        **payload,
    }


class TestTotality:
    """Every EventType member has an EXPLICIT disposition."""

    EXPLICIT = _SKIPPED | {
        EventType.RUN_STARTED,
        EventType.RUN_FINISHED,
        EventType.RUN_ERROR,
        EventType.RUN_CANCELLED,
        EventType.ERROR,
        EventType.TEXT_MESSAGE_START,
        EventType.TEXT_MESSAGE_CONTENT,
        EventType.TEXT_MESSAGE_END,
        EventType.TEXT_MESSAGE_CHUNK,
        EventType.TOOL_CALL_START,
        EventType.TOOL_CALL_ARGS,
        EventType.TOOL_CALL_END,
        EventType.STEP_STARTED,
        EventType.STEP_FINISHED,
        EventType.QUESTION,
        EventType.HITL_REQUIRED,
        EventType.HITL_APPROVED,
        EventType.HITL_REJECTED,
        EventType.HITL_TIMEOUT,
        EventType.STATE_DELTA,
        EventType.MESSAGES_SNAPSHOT,
        EventType.CUSTOM,
    }

    def test_every_member_is_dispositioned(self) -> None:
        missing = set(EventType) - self.EXPLICIT
        assert not missing, f"EventType members without an /agui disposition: {missing}"

    def test_translate_never_raises_for_any_member(self) -> None:
        for member in EventType:
            out = _t().translate(_record(member))
            assert isinstance(out, list)

    def test_unknown_stored_type_is_dropped_not_leaked(self) -> None:
        out = _t().translate({"type": "SOME_FUTURE_EVENT", "content": "LEAK"})
        assert out == []


class TestEnvelope:
    def test_timestamps_become_epoch_ms(self) -> None:
        out = _t().translate(_record(EventType.TEXT_MESSAGE_CONTENT, messageId="m", delta="x"))
        assert out[0]["timestamp"] == TS_MS

    def test_classic_envelope_extras_never_leave(self) -> None:
        record = _record(
            EventType.TEXT_MESSAGE_CONTENT,
            messageId="m",
            delta="x",
            agentName="root",
            eventId="e1",
        )
        blob = json.dumps(_t().translate(record))
        assert "agentName" not in blob
        assert "eventId" not in blob
        assert "thread_stored" not in blob

    def test_lifecycle_echoes_request_ids_not_stored_ids(self) -> None:
        translator = _t()
        assert translator.translate(_record(EventType.RUN_FINISHED)) == []
        finished = translator.flush_finish()[-1]
        assert finished["threadId"] == "thread_req"
        assert finished["runId"] == "run_req"

    def test_bare_data_framing(self) -> None:
        frame = encode_sse({"type": "RUN_STARTED"})
        assert frame.startswith("data: ")
        assert frame.endswith("\n\n")
        assert "event:" not in frame
        assert "[DONE]" not in frame


class TestDispositions:
    def test_run_started_deduped_after_synthetic_frame(self) -> None:
        translator = _t()
        first = translator.run_started_frame()
        assert first == {"type": "RUN_STARTED", "threadId": "thread_req", "runId": "run_req"}
        assert translator.translate(_record(EventType.RUN_STARTED, workflow_id="wf8")) == []

    def test_empty_text_delta_skipped(self) -> None:
        assert (
            _t().translate(_record(EventType.TEXT_MESSAGE_CONTENT, messageId="m", delta="")) == []
        )

    def test_tool_call_end_splits_spec_plus_custom(self) -> None:
        out = _t().translate(
            _record(
                EventType.TOOL_CALL_END,
                toolCallId="c1",
                toolCallName="get_part",
                status="ok",
                durationMs=12.3,
            )
        )
        assert [e["type"] for e in out] == ["TOOL_CALL_END", "CUSTOM"]
        assert set(out[0]) == {"type", "timestamp", "toolCallId"}
        assert out[1]["name"] == "aimms.toolStatus"
        assert out[1]["value"]["status"] == "ok"
        assert abs(out[1]["value"]["durationMs"] - 12.3) < 1e-9

    def test_run_error_custom_precedes_spec_error(self) -> None:
        out = _t().translate(
            _record(
                EventType.RUN_ERROR,
                message="AI turn failed",
                code="turn_failed",
                failure_class="provider_outage",
                localized_message="Localized copy",
            )
        )
        assert [e["type"] for e in out] == ["CUSTOM", "RUN_ERROR"]
        assert out[0]["name"] == "aimms.error"
        assert out[0]["value"]["failureClass"] == "provider_outage"
        assert out[0]["value"]["localizedMessage"] == "Localized copy"
        assert out[1] == {
            "type": "RUN_ERROR",
            "timestamp": TS_MS,
            "message": "AI turn failed",
            "code": "turn_failed",
        }

    def test_run_cancelled_maps_to_coded_run_error(self) -> None:
        out = _t().translate(_record(EventType.RUN_CANCELLED, message="Run cancelled"))
        assert out == [
            {
                "type": "RUN_ERROR",
                "timestamp": TS_MS,
                "message": "Run cancelled",
                "code": "run_cancelled",
            }
        ]

    def test_state_delta_kinds_route_to_channels(self) -> None:
        entities = _t().translate(
            _record(EventType.STATE_DELTA, kind="entity_manifest", entities=[{"model": "part"}])
        )
        assert entities[0]["name"] == "aimms.entities"
        media = _t().translate(
            _record(
                EventType.STATE_DELTA,
                kind="media_evidence",
                media_evidence=[{"attachment_id": 9, "segment_index": 4}],
            )
        )
        assert media[0]["name"] == "aimms.mediaEvidence"
        assert media[0]["value"]["media_evidence"] == [{"attachment_id": 9, "segment_index": 4}]
        provenance = _t().translate(
            _record(
                EventType.STATE_DELTA,
                kind="diagnosis_provenance",
                confidence="high",
                evidence=[{"kind": "manual"}],
            )
        )
        assert provenance[0]["name"] == "aimms.provenance"
        other = _t().translate(_record(EventType.STATE_DELTA, kind="future_kind", x=1))
        assert other[0]["name"] == "aimms.stateDelta"

    def test_question_rides_custom_channel(self) -> None:
        out = _t().translate(
            _record(EventType.QUESTION, kind="clarification_question", interrupt_id="i1")
        )
        assert out[0]["name"] == "aimms.question"
        assert out[0]["value"]["interrupt_id"] == "i1"

    def test_messages_snapshot_synthesizes_deterministic_ids(self) -> None:
        record = _record(
            EventType.MESSAGES_SNAPSHOT,
            messages=[{"role": "assistant", "content": "Final."}],
        )
        first = _t().translate(record)
        second = _t().translate(record)
        assert first[0]["messages"][0]["id"] == "msg_run_req_0"
        assert first == second

    def test_run_finished_is_buffered_and_flushes_last(self) -> None:
        """The classic dialect emits tail events AFTER its RUN_FINISHED
        (question arming, entity manifest, provenance, reconcile snapshot);
        @ag-ui/client rejects any post-finish frame, so the spec finish is
        held until stream close."""
        quiet = _t()
        assert quiet.translate(_record(EventType.RUN_FINISHED)) == []
        assert [e["type"] for e in quiet.flush_finish()] == ["RUN_FINISHED"]
        # flush is once-only
        assert quiet.flush_finish() == []

        busy = _t()
        busy.translate(_record(EventType.TOOL_CALL_START, toolCallId="c", toolCallName="t"))
        assert busy.translate(_record(EventType.RUN_FINISHED)) == []
        out = busy.flush_finish()
        assert [e["type"] for e in out] == ["CUSTOM", "RUN_FINISHED"]
        assert out[0]["name"] == "aimms.proposalsRefresh"

    def test_post_finish_tail_precedes_the_flushed_finish(self) -> None:
        translator = _t()
        frames = []
        for record in (
            _record(EventType.RUN_FINISHED),
            _record(EventType.QUESTION, kind="clarification_question", interrupt_id="i1"),
            _record(EventType.STATE_DELTA, kind="entity_manifest", entities=[]),
        ):
            frames.extend(translator.translate(record))
        frames.extend(translator.flush_finish())
        assert [f["type"] for f in frames] == ["CUSTOM", "CUSTOM", "RUN_FINISHED"]

    def test_error_after_finish_supersedes_the_buffered_finish(self) -> None:
        translator = _t()
        assert translator.translate(_record(EventType.RUN_FINISHED)) == []
        out = translator.translate(_record(EventType.RUN_ERROR, message="late failure"))
        assert [e["type"] for e in out] == ["CUSTOM", "RUN_ERROR"]
        assert translator.flush_finish() == []

    def test_internal_error_rides_custom_only(self) -> None:
        """ERROR is non-terminal internally; mapping it to spec RUN_ERROR
        terminated the client run before the localized RUN_ERROR arrived."""
        out = _t().translate(_record(EventType.ERROR, message="transient", code="oops"))
        assert [e["type"] for e in out] == ["CUSTOM"]
        assert out[0]["name"] == "aimms.error"

    def test_step_events_pass_and_arm_the_nudge(self) -> None:
        translator = _t()
        out = translator.translate(_record(EventType.STEP_STARTED, stepName="context"))
        assert out == [{"type": "STEP_STARTED", "timestamp": TS_MS, "stepName": "context"}]
        assert translator.saw_tool_events is True

    def test_skipped_members_emit_nothing(self) -> None:
        for member in _SKIPPED:
            assert _t().translate(_record(member, content="LEAK-CANARY")) == []


class TestGoldenTranscript:
    """A stored classic transcript translates to byte-exact spec frames."""

    def test_golden(self) -> None:
        classic = [
            _record(EventType.RUN_STARTED, workflow_id="wf8", agent_name="root"),
            _record(EventType.AGENT_THINKING, message="Gathering context..."),
            _record(EventType.TEXT_MESSAGE_START, messageId="m1"),
            _record(EventType.TEXT_MESSAGE_CONTENT, messageId="m1", delta="We have "),
            _record(EventType.TEXT_MESSAGE_CONTENT, messageId="m1", delta="42 parts."),
            _record(EventType.TEXT_MESSAGE_END, messageId="m1"),
            _record(EventType.RUN_FINISHED, response_state="complete"),
        ]
        translator = _t()
        frames = [encode_sse(translator.run_started_frame())]
        for record in classic:
            frames.extend(encode_sse(event) for event in translator.translate(record))
        frames.extend(encode_sse(event) for event in translator.flush_finish())
        expected = [
            'data: {"type":"RUN_STARTED","threadId":"thread_req","runId":"run_req"}\n\n',
            f'data: {{"type":"TEXT_MESSAGE_START","timestamp":{TS_MS},"messageId":"m1","role":"assistant"}}\n\n',
            f'data: {{"type":"TEXT_MESSAGE_CONTENT","timestamp":{TS_MS},"messageId":"m1","delta":"We have "}}\n\n',
            f'data: {{"type":"TEXT_MESSAGE_CONTENT","timestamp":{TS_MS},"messageId":"m1","delta":"42 parts."}}\n\n',
            f'data: {{"type":"TEXT_MESSAGE_END","timestamp":{TS_MS},"messageId":"m1"}}\n\n',
            f'data: {{"type":"RUN_FINISHED","timestamp":{TS_MS},"threadId":"thread_req","runId":"run_req"}}\n\n',
        ]
        assert frames == expected


def test_custom_channels_are_frozen_and_generated() -> None:
    """The channel tuple is the S43 contract for AimmsCustomChannel."""
    assert CUSTOM_CHANNELS == (
        "aimms.error",
        "aimms.toolStatus",
        "aimms.question",
        "aimms.entities",
        "aimms.mediaEvidence",
        "aimms.provenance",
        "aimms.stateDelta",
        "aimms.proposalsRefresh",
        "aimms.hitl",
        "aimms.custom",
    )
