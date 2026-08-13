"""S45: token streaming — coalescing, the wf8 generator, and the guards."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.streaming import EventType
from ai.core.turn_service import coalesce_text_deltas


def _delta(message_id: str, delta: str) -> dict:
    return {
        "type": EventType.TEXT_MESSAGE_CONTENT.value,
        "messageId": message_id,
        "delta": delta,
    }


class TestCoalesceTextDeltas:
    def test_consecutive_deltas_merge_per_message_id(self) -> None:
        events = [
            {"type": EventType.TEXT_MESSAGE_START.value, "messageId": "m1"},
            _delta("m1", "The answer"),
            _delta("m1", " is"),
            _delta("m1", " 42"),
            {"type": EventType.TEXT_MESSAGE_END.value, "messageId": "m1"},
        ]
        out = coalesce_text_deltas(events)
        deltas = [r for r in out if r["type"] == EventType.TEXT_MESSAGE_CONTENT.value]
        assert len(deltas) == 1
        assert deltas[0]["delta"] == "The answer is 42"

    def test_mixed_message_ids_do_not_merge(self) -> None:
        events = [_delta("m1", "a"), _delta("m2", "b"), _delta("m2", "c")]
        out = coalesce_text_deltas(events)
        assert [r["delta"] for r in out] == ["a", "bc"]

    def test_snapshot_supersedes_and_is_dropped(self) -> None:
        events = [
            _delta("m1", "old "),
            _delta("m1", "paraphrase"),
            {
                "type": EventType.MESSAGES_SNAPSHOT.value,
                "messages": [{"role": "assistant", "content": "Which machine?"}],
            },
        ]
        out = coalesce_text_deltas(events)
        assert all(r["type"] != EventType.MESSAGES_SNAPSHOT.value for r in out)
        deltas = [r for r in out if r["type"] == EventType.TEXT_MESSAGE_CONTENT.value]
        assert len(deltas) == 1
        assert deltas[0]["delta"] == "Which machine?"

    def test_snapshot_supersedes_all_groups_split_by_tool_events(self) -> None:
        """S46 tool records split the deltas; superseded text must not survive."""
        events = [
            {"type": EventType.TEXT_MESSAGE_START.value, "messageId": "m1"},
            _delta("m1", "Let me check that. "),
            {"type": EventType.TOOL_CALL_START.value, "toolCallId": "c1"},
            {"type": EventType.TOOL_CALL_END.value, "toolCallId": "c1"},
            _delta("m1", "Stock is 40 units."),
            {"type": EventType.TEXT_MESSAGE_END.value, "messageId": "m1"},
            {
                "type": EventType.MESSAGES_SNAPSHOT.value,
                "messages": [{"role": "assistant", "content": "FINAL corrected answer."}],
            },
        ]
        out = coalesce_text_deltas(events)
        deltas = [r for r in out if r["type"] == EventType.TEXT_MESSAGE_CONTENT.value]
        assert [r["delta"] for r in deltas] == ["FINAL corrected answer."]
        # Tool records and the START/END frame survive in order.
        assert [r["type"] for r in out] == [
            EventType.TEXT_MESSAGE_START.value,
            EventType.TEXT_MESSAGE_CONTENT.value,
            EventType.TOOL_CALL_START.value,
            EventType.TOOL_CALL_END.value,
            EventType.TEXT_MESSAGE_END.value,
        ]

    def test_snapshot_only_supersedes_the_final_message(self) -> None:
        """An earlier message (other messageId) keeps its own text."""
        events = [
            _delta("social", "Hi there. "),
            _delta("m1", "raw "),
            _delta("m1", "paraphrase"),
            {
                "type": EventType.MESSAGES_SNAPSHOT.value,
                "messages": [{"role": "assistant", "content": "Final."}],
            },
        ]
        out = coalesce_text_deltas(events)
        assert [r["delta"] for r in out] == ["Hi there. ", "Final."]

    def test_snapshot_kept_when_no_text_delta_exists(self) -> None:
        """With no delta carrier the snapshot IS the turn's text — keep it."""
        snapshot = {
            "type": EventType.MESSAGES_SNAPSHOT.value,
            "messages": [{"role": "assistant", "content": "Which machine?"}],
        }
        events = [
            {"type": EventType.RUN_STARTED.value},
            snapshot,
            {"type": EventType.RUN_FINISHED.value},
        ]
        out = coalesce_text_deltas(events)
        assert snapshot in out

    def test_single_delta_turn_is_byte_stable(self) -> None:
        """Today's stored shape must pass through untouched."""
        events = [
            {"type": EventType.RUN_STARTED.value},
            {"type": EventType.TEXT_MESSAGE_START.value, "messageId": "m1"},
            _delta("m1", "whole answer"),
            {"type": EventType.TEXT_MESSAGE_END.value, "messageId": "m1"},
            {"type": EventType.RUN_FINISHED.value},
        ]
        assert coalesce_text_deltas(events) == events

    def test_non_text_events_are_preserved_in_order(self) -> None:
        events = [
            {"type": EventType.QUESTION.value, "question_text": "q"},
            _delta("m1", "a"),
            {"type": EventType.RUN_ERROR.value, "message": "x"},
            _delta("m1", "b"),
        ]
        out = coalesce_text_deltas(events)
        assert [r["type"] for r in out] == [
            EventType.QUESTION.value,
            EventType.TEXT_MESSAGE_CONTENT.value,
            EventType.RUN_ERROR.value,
            EventType.TEXT_MESSAGE_CONTENT.value,
        ]


class _FakeUpdate:
    def __init__(self, text: str, usage: dict | None = None):
        self.text = text
        if usage:
            self.usage_details = SimpleNamespace(**usage)


class _FakeAgent:
    def __init__(self, updates, error_at: int | None = None):
        self._updates = updates
        self._error_at = error_at

    async def run_stream(self, run_input, tools=None):
        for index, update in enumerate(self._updates):
            if self._error_at is not None and index == self._error_at:
                raise ConnectionError("socket down")
            yield update


def _workflow_with_prepared(agent) -> object:
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow, _PreparedLookupRun

    workflow = T1LookupWorkflow.__new__(T1LookupWorkflow)

    async def prepare(**kwargs):
        await asyncio.sleep(0)
        return _PreparedLookupRun(
            agent=agent,
            run_input="input",
            runtime_tools=[],
            modality="text",
            enforce=True,
        )

    workflow._prepare_run = prepare
    return workflow


def _collect(generator) -> list[str]:
    async def run():
        return [chunk async for chunk in generator]

    return asyncio.run(run())


class TestExecuteStreaming:
    def test_streams_chunks_in_order_and_records_usage(self) -> None:
        agent = _FakeAgent([
            _FakeUpdate("The answer"),
            _FakeUpdate(" is 42"),
            _FakeUpdate("", usage={"input_token_count": 5, "output_token_count": 2}),
        ])
        workflow = _workflow_with_prepared(agent)
        recorded = {}

        def record(source, metrics):
            recorded[source] = metrics

        with patch("ai.core.workflows.wf8_lookup.record_usage", side_effect=record):
            chunks = _collect(workflow.execute_streaming(query="how many parts?"))
        assert chunks == ["The answer", " is 42"]
        assert "wf8_lookup" in recorded

    def test_social_turn_yields_one_chunk_without_prep(self) -> None:
        from ai.core.workflows.wf8_lookup import T1LookupWorkflow

        workflow = T1LookupWorkflow.__new__(T1LookupWorkflow)
        chunks = _collect(workflow.execute_streaming(query="Hello."))
        assert len(chunks) == 1
        assert chunks[0]

    def test_stream_failure_raises_with_stamped_class(self) -> None:
        agent = _FakeAgent([_FakeUpdate("partial ")], error_at=0)
        workflow = _workflow_with_prepared(agent)
        with pytest.raises(RuntimeError) as excinfo:
            _collect(workflow.execute_streaming(query="how many parts?"))
        assert getattr(excinfo.value, "failure_class", None) == "provider_outage"

    def test_usage_extracted_from_real_maf_update_contents(self) -> None:
        """Real MAF updates carry usage as UsageContent in contents — the
        response-shaped ``usage_details`` attribute never exists on them."""
        from agent_framework import (
            AgentRunResponseUpdate,
            UsageContent,
            UsageDetails,
        )

        terminal = AgentRunResponseUpdate(
            contents=[
                UsageContent(
                    details=UsageDetails(
                        input_token_count=7,
                        output_token_count=3,
                        total_token_count=10,
                    )
                )
            ]
        )
        agent = _FakeAgent([_FakeUpdate("answer"), terminal])
        workflow = _workflow_with_prepared(agent)
        recorded = {}

        def record(source, metrics):
            recorded[source] = metrics

        with patch("ai.core.workflows.wf8_lookup.record_usage", side_effect=record):
            chunks = _collect(workflow.execute_streaming(query="how many parts?"))
        assert chunks == ["answer"]
        assert recorded["wf8_lookup"].get("input_token_count") == 7
        assert recorded["wf8_lookup"].get("output_token_count") == 3


class TestGuards:
    """Source-inspection: the branch guards that keep streaming scoped."""

    def test_root_guard_requires_flag_and_excludes_voice(self) -> None:
        import inspect

        from ai.core.workflows import root as root_module

        source = inspect.getsource(root_module.RootWorkflow)
        anchor = source.index("supports_streaming = hasattr(")
        guard = source[anchor : anchor + 700]
        assert "feature_token_streaming" in guard
        assert '!= "voice"' in guard

    def test_turn_service_reconciles_before_freezing_events(self) -> None:
        import inspect

        from ai.core import turn_service

        source = inspect.getsource(turn_service.NormalizedTurnService)
        assert "MESSAGES_SNAPSHOT" in source
        # Reconciliation must precede the canonical wrapper on the legacy path.
        assert source.index("message != streamed_text") < source.index(
            "response = _canonical_response_for_legacy(\n                    message"
        )

    def test_reconciliation_and_replacement_are_flag_gated(self) -> None:
        """Flags off, the classic wire/storage shape must stay byte-identical:
        no post-hoc question replacement (wf8 already applied its own — a
        second voice render would re-trim a trimmed proposal) and no
        MESSAGES_SNAPSHOT emit."""
        import inspect

        from ai.core import turn_service

        source = inspect.getsource(turn_service.NormalizedTurnService)
        gate = source.index("streaming_reconcile = (")
        assert "feature_token_streaming" in source[gate : gate + 300]
        assert "modality != TurnModality.VOICE" in source[gate : gate + 300]
        # Both consumers sit BEHIND the gate assignment.
        assert gate < source.index("if streaming_reconcile:")
        assert gate < source.index("if streaming_reconcile and message != streamed_text:")

    def test_root_failure_paths_close_the_open_step(self) -> None:
        """Every failure handler must pair STEP_STARTED before its error
        event — an unpaired step would persist in the failed turn's replay."""
        import inspect

        from ai.core.workflows import root as root_module

        source = inspect.getsource(root_module.RootWorkflow)
        # Success path + TimeoutError handler + generic Exception handler.
        assert source.count("await _step(None)") >= 3
