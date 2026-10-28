"""Fresh same-case sources cannot be replaced by old chat or browser claims."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ai.core.focus.context import TurnFocusContext
from ai.core.tools.capture_ledger import bind_tool_captures
from ai.core.tools.inventree.read.machines import get_machine_maintenance_history
from ai.core.tools.inventree.read.maintenance import (
    get_work_order_closeout,
    get_work_order_repair_state,
)
from ai.core.workflows.focused_maintenance_read import (
    _requested,
    prepare_focused_maintenance_read,
    project_maintenance_source_coordinates,
)

QUERY = "Read E05 and summarize the maintenance history for this work order."


@pytest.fixture
def current_sources(monkeypatch):
    focus = TurnFocusContext("work_order", 84, "WO-84 Pump 08", 44, 84, 5)
    principal = SimpleNamespace(user_pk="7", scope="site:test")
    context = {
        "modality": "voice",
        "conversation_focus": {
            "kind": "work_order",
            "id": 84,
            "work_order_id": 84,
            "machine_id": 44,
            "focus_version": 5,
        },
        "conversation_history": [{"role": "assistant", "content": "OLD_APPROVED_PHYSICAL_WORK"}],
    }
    source = {
        "status": "ok",
        "work_order_id": 84,
        "machine_id": 44,
        "repair_packet_id": 27,
        "lifecycle_version": 3,
        "packet_revision": "r2",
        "sources": {
            "prepared_e05": {
                "status": "ok",
                "id": 210,
                "sha256": "a" * 64,
                "content": "Prepared source only; not actual physical work.",
                "prepared": True,
            }
        },
        "source_reads": [
            {"reader": "test_native_reader", "result_sha256": "b" * 64},
            {
                "reader": "tasks.services.focused_maintenance_sources._prepared_e05",
                "status": "ok",
                "attachment_id": 210,
                "source_sha256": "a" * 64,
            },
        ],
    }
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return copy.deepcopy(source)

    monkeypatch.setattr("ai.core.auth.get_current_principal", lambda: principal)
    monkeypatch.setattr("ai.core.focus.context.current_turn_focus", lambda: focus)
    monkeypatch.setattr(
        "ai.core.workflows.focused_maintenance_read._thread_focus_current",
        lambda thread_id, *_: thread_id == "thread-test",
    )
    monkeypatch.setattr("ai.core.analysis.scope_context.current_turn_scope", lambda: None)
    monkeypatch.setattr("ai.core.analysis.scope_context.scope_miss_for_machine", lambda _: None)
    monkeypatch.setattr(
        "tasks.services.focused_maintenance_sources.read_focused_maintenance_sources", read
    )
    return SimpleNamespace(
        focus=focus,
        principal=principal,
        context=context,
        source=source,
        calls=calls,
        offered=[
            get_machine_maintenance_history,
            get_work_order_closeout,
            get_work_order_repair_state,
        ],
        ledger=bind_tool_captures(),
    )


@pytest.mark.parametrize(
    "query",
    [
        QUERY,
        "Show E-zero-five for this work.",
        "What does E05 say for work order 84?",
        "Show the latest maintenance for machine 44.",
        "Read the maintenance history for Pump 08.",
    ],
)
def test_explicit_current_case_source_requests(query, current_sources):
    assert _requested(query, current_sources.focus)


@pytest.mark.parametrize(
    "query",
    [
        "Read E05 for work order 85.",
        "Show maintenance history for machine 45.",
        "Show maintenance history for Pump 09.",
        "Compare maintenance history across machines.",
        "Show maintenance history for machine 44 Pump 09.",
        "Read maintenance history for Pump Alpha.",
        "Show maintenance history at West Station.",
        "Read E05 and approve this work.",
        "Read E05, start the pump.",
        "Read E05. Save a repair scope.",
        "Do not read E05.",
        "Show E03.",
        "What is the weather?",
        "E05 is mentioned in the last answer.",
        "What is the history of France?",
    ],
)
def test_writes_other_cases_and_unrequested_sources_do_not_trigger_preread(query, current_sources):
    assert not _requested(query, current_sources.focus)


@pytest.mark.parametrize("modality", ["voice", "text"])
@pytest.mark.asyncio
async def test_fresh_bundle_replaces_history_and_records_exact_native_coordinates(
    current_sources, modality
):
    fixture = current_sources
    fixture.context["modality"] = modality
    messages, audit = await prepare_focused_maintenance_read(
        QUERY, fixture.context, fixture.offered, thread_id="thread-test"
    )
    assert fixture.calls == [
        {
            "actor_id": "7",
            "work_order_id": 84,
            "machine_id": 44,
            "include_history": True,
            "include_e05": True,
        }
    ]
    assert audit["reader_invoked"] and audit["status"] == "ok"
    assert audit["focus_version"] == 5 and audit["lifecycle_version"] == 3
    assert audit["llm_function_call"] is False
    assert audit is fixture.ledger.server_source_reads[0]
    assert "OLD_APPROVED_PHYSICAL_WORK" not in " ".join(row.text for row in messages)
    model_source = json.loads(messages[-1].text.split("] ", 1)[1])
    assert model_source["sources"]["prepared_e05"]["id"] == 210
    assert model_source["sources"]["prepared_e05"]["prepared"] is True
    assert (
        model_source["sources"]["prepared_e05"]["content"]
        == fixture.source["sources"]["prepared_e05"]["content"]
    )
    assert "sha256" not in json.dumps(model_source)
    assert "a" * 64 not in json.dumps(model_source)
    assert "source_reads" not in " ".join(row.text for row in messages)
    assert "test_native_reader" not in " ".join(row.text for row in messages)
    assert audit["source_reads"] == fixture.source["source_reads"]
    assert "not actual physical work" not in json.dumps(audit)


@pytest.mark.parametrize(
    "failure",
    [
        "not_offered",
        "scope_denied",
        "scope_error",
        "wrong_thread",
        "changed_thread",
        "reader_error",
        "wrong_work",
        "wrong_machine",
        "oversize",
    ],
)
@pytest.mark.asyncio
async def test_unavailable_sources_never_fall_back_to_prior_chat_or_other_readers(
    current_sources, monkeypatch, failure
):
    fixture = current_sources
    offered = fixture.offered
    thread_id = "thread-test"
    if failure == "not_offered":
        offered = []
    elif failure == "scope_denied":
        monkeypatch.setattr(
            "ai.core.analysis.scope_context.scope_miss_for_machine",
            lambda _: {"error": "scope_miss"},
        )
    elif failure == "scope_error":

        def fail():
            raise RuntimeError("private scope data")

        monkeypatch.setattr("ai.core.analysis.scope_context.current_turn_scope", fail)
    elif failure == "wrong_thread":
        thread_id = "another-thread"
    elif failure == "changed_thread":
        answers = iter([True, False])
        monkeypatch.setattr(
            "ai.core.workflows.focused_maintenance_read._thread_focus_current",
            lambda *_: next(answers),
        )
    elif failure == "reader_error":

        def fail(**_kwargs):
            raise PermissionError("private source data")

        monkeypatch.setattr(
            "tasks.services.focused_maintenance_sources.read_focused_maintenance_sources", fail
        )
    elif failure == "wrong_work":
        fixture.source["work_order_id"] = 85
    elif failure == "wrong_machine":
        fixture.source["machine_id"] = 45
    else:
        fixture.source["sources"]["body"] = "x" * (129 * 1024)
    messages, audit = await prepare_focused_maintenance_read(
        QUERY, fixture.context, offered, thread_id=thread_id
    )
    assert audit["status"] == "unavailable"
    assert json.loads(messages[-1].text.split("] ", 1)[1]) == {"status": "unavailable"}
    assert "OLD_APPROVED" not in " ".join(row.text for row in messages)
    assert "private source" not in json.dumps(audit)


@pytest.mark.parametrize(
    "failure",
    ["native_focus_missing", "public_work_mismatch", "public_version_mismatch", "anonymous"],
)
@pytest.mark.asyncio
async def test_mismatched_native_focus_yields_toolless_unavailable_instead_of_general_lookup(
    current_sources, monkeypatch, failure
):
    fixture = current_sources
    if failure == "native_focus_missing":
        monkeypatch.setattr("ai.core.focus.context.current_turn_focus", lambda: None)
    elif failure == "anonymous":
        monkeypatch.setattr("ai.core.auth.get_current_principal", lambda: None)
    elif failure == "public_work_mismatch":
        fixture.context["conversation_focus"]["work_order_id"] = 85
    else:
        fixture.context["conversation_focus"]["focus_version"] = 4
    messages, audit = await prepare_focused_maintenance_read(
        QUERY, fixture.context, fixture.offered, thread_id="thread-test"
    )
    assert audit["status"] == "unavailable" and not audit["reader_invoked"]
    assert fixture.calls == []
    assert "OLD_APPROVED" not in " ".join(row.text for row in messages)


@pytest.mark.asyncio
async def test_missing_e05_remains_unavailable_beside_separate_native_projection(current_sources):
    current_sources.source["sources"]["prepared_e05"] = {
        "status": "unavailable",
        "label": "P08-TEMP-E05",
    }
    messages, audit = await prepare_focused_maintenance_read(
        QUERY, current_sources.context, current_sources.offered, thread_id="thread-test"
    )
    source = json.loads(messages[-1].text.split("] ", 1)[1])
    assert source["sources"]["prepared_e05"] == {"status": "unavailable", "label": "P08-TEMP-E05"}
    assert audit["status"] == "ok"
    assert "Never claim a literal E05 attachment read" in messages[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("modality", ["voice", "text"])
async def test_wf8_performs_one_native_bundle_read_before_one_toolless_model_call(
    current_sources, monkeypatch, modality
):
    from ai.core.config import get_settings
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow

    settings = get_settings().model_copy(
        update={
            "feature_capability_broker_enforce": False,
            "feature_capability_broker_shadow": False,
        }
    )
    monkeypatch.setattr("ai.core.workflows.wf8_lookup.get_settings", lambda: settings)
    monkeypatch.setattr(
        "ai.core.tools.rbac.tools_for_current_user", AsyncMock(return_value=current_sources.offered)
    )
    reply = SimpleNamespace(
        text="Native same-case source answer.",
        response_id="test-model-response",
        usage_details=None,
        raw_representation=None,
    )
    agent = SimpleNamespace(run=AsyncMock(return_value=reply))
    workflow = T1LookupWorkflow()
    current_sources.context["modality"] = modality
    workflow._agent_deployments["voice" if modality == "voice" else "default"] = (
        "test-fast-deployment"
    )
    monkeypatch.setattr(workflow, "_get_agent", AsyncMock(return_value=agent))
    result = await workflow.execute(QUERY, context=current_sources.context, thread_id="thread-test")
    assert result.success and agent.run.await_count == 1
    assert len(current_sources.calls) == 1
    assert agent.run.await_args.kwargs["tools"] == []
    assert "OLD_APPROVED" not in " ".join(row.text for row in agent.run.await_args.args[0])
    assert (
        result.data["server_source_read"]["model_generation"]["configured_deployment"]
        == "test-fast-deployment"
    )


@pytest.mark.asyncio
async def test_native_history_receipt_survives_canonical_terminal_persistence(
    current_sources, monkeypatch
):
    from ai.core.turn import execution, finalize
    from ai.core.turn.state import TurnRun

    class Workflow:
        async def run_stream(self, **_kwargs):
            await prepare_focused_maintenance_read(
                QUERY, current_sources.context, current_sources.offered, thread_id="thread-test"
            )
            yield "Current same-case source answer."

    async def call_sync(function, *args, **kwargs):  # noqa: RUF029 - Match the awaited service callback protocol.
        return function(*args, **kwargs)

    durable = {}

    def terminal(turn_id, **kwargs):
        durable.update(json.loads(json.dumps(kwargs, default=str)))
        return SimpleNamespace(pk=turn_id)

    service = SimpleNamespace(
        workflow_factory=Workflow,
        build_context_bundle=AsyncMock(return_value=SimpleNamespace(replay_dict=lambda: [])),
        _call_sync=call_sync,
    )
    run = TurnRun(
        actor=SimpleNamespace(user_pk="7"),
        trusted_context=SimpleNamespace(locale="en"),
        content=QUERY,
        modality="voice",
        correlation_id="test-read",
        idempotency_key="test-read",
        server_pinned_workflow=None,
        server_generation_target=None,
        trusted={},
        metadata={},
        repository=SimpleNamespace(terminal=terminal),
        thread=SimpleNamespace(pk="thread-test"),
        turn=SimpleNamespace(pk="turn-test"),
    )
    run.capture = SimpleNamespace(workflow_id="wf8", events=[])
    monkeypatch.setattr(
        execution,
        "_bind_turn_capture_and_scope",
        AsyncMock(return_value=(current_sources.ledger, None)),
    )
    monkeypatch.setattr(execution, "_assemble_workflow_context", lambda *_: current_sources.context)
    monkeypatch.setattr(finalize, "_terminal_output_metadata", lambda value: value)
    canonical = await execution._run_legacy_workflow(service, run)
    canonical["events"] = []
    await finalize.persist_terminal(
        service, run, canonical, state="complete", output_content=canonical["message"]
    )
    saved = durable["canonical_result"]["server_source_reads"]
    assert saved == current_sources.ledger.server_source_reads
    assert saved[0]["lifecycle_version"] == 3 and saved[0]["focus_version"] == 5
    assert saved[0]["source_reads"][0]["reader"] == "test_native_reader"
    assert saved[0]["llm_function_call"] is False


NATIVE_E05_SHA256 = "1491f0346c71df7379c9f6e5c316f6ba831c42aa1015e26e8275ecd396060978"
ACTUAL_DEFECTIVE_SHA256 = "1491f0346c71df737c9f6e5c316f6ba831c42aa1015e26e8275ecd396060978"


def native_citation_audit():
    return {
        "status": "ok",
        "source_reads": [
            {
                "reader": "tasks.services.focused_maintenance_sources._prepared_e05",
                "status": "ok",
                "attachment_id": 210,
                "source_sha256": NATIVE_E05_SHA256,
            }
        ],
    }


@pytest.mark.parametrize("claimed", [ACTUAL_DEFECTIVE_SHA256, NATIVE_E05_SHA256, "f" * 64])
def test_actual_mistyped_or_freeform_hash_claim_is_withheld_instead_of_silently_repaired(claimed):
    audit = native_citation_audit()
    text = f"E05 (`P08-TEMP-E05`, source ID210, SHA-256 `{claimed}`) is prepared exercise evidence."
    result = project_maintenance_source_coordinates(text, audit)
    assert "generated summary" in result and "withheld" in result
    assert "source ID210" not in result
    assert ACTUAL_DEFECTIVE_SHA256 not in result
    assert result.count(NATIVE_E05_SHA256) == 1
    assert "/api/attachment/210/download/" in result
    assert audit["source_coordinate_projection"]["model_hash_claim_withheld"] is True


@pytest.mark.parametrize("failure", ["no_source", "invalid_native_hash", "ambiguous_source"])
def test_unknown_source_coordinates_cannot_be_invented_from_the_model_hash(failure):
    audit = native_citation_audit()
    if failure == "no_source":
        audit["status"] = "unavailable"
    elif failure == "invalid_native_hash":
        audit["source_reads"][0]["source_sha256"] = ACTUAL_DEFECTIVE_SHA256
    else:
        audit["source_reads"].append(copy.deepcopy(audit["source_reads"][0]))
    result = project_maintenance_source_coordinates(
        f"E05 SHA-256 `{ACTUAL_DEFECTIVE_SHA256}`.", audit
    )
    assert "/api/attachment/" not in result
    assert NATIVE_E05_SHA256 not in result
    assert "withheld" in result


def test_only_native_renderer_adds_exact_file_hash_to_a_clean_answer():
    audit = native_citation_audit()
    result = project_maintenance_source_coordinates(
        "E05 [[E05]] is prepared only; current checks remain unrecorded.", audit
    )
    assert "[[E05]]" not in result
    assert "current checks remain unrecorded" in result
    assert result.count(NATIVE_E05_SHA256) == 1
    assert audit["source_coordinate_projection"]["model_hash_claim_withheld"] is False
    from ai.core.turn.responses import _plain_spoken_text

    spoken = _plain_spoken_text(result)
    assert NATIVE_E05_SHA256 not in spoken
    assert "current checks remain unrecorded" in spoken


@pytest.mark.parametrize("bad_hash", [False, True])
@pytest.mark.asyncio
async def test_streaming_buffers_native_source_answer_until_coordinate_validation(
    current_sources, monkeypatch, bad_hash
):
    from ai.core.workflows.wf8_lookup import T1LookupWorkflow, _PreparedLookupRun

    audit = native_citation_audit()
    text = (
        f"E05 SHA-256 `{ACTUAL_DEFECTIVE_SHA256}`."
        if bad_hash
        else "Prepared E05 [[E05]]; checks remain unrecorded."
    )

    async def stream(*_args, **_kwargs):  # noqa: RUF029 - Match the provider's async stream protocol.
        for chunk in (text[:18], text[18:]):
            yield SimpleNamespace(text=chunk, contents=[])

    prepared = _PreparedLookupRun(
        agent=SimpleNamespace(run_stream=stream),
        run_input="source fixture",
        runtime_tools=[],
        modality="text",
        enforce=False,
        maintenance_source_read=audit,
    )
    workflow = T1LookupWorkflow()
    monkeypatch.setattr(workflow, "_prepare_run", AsyncMock(return_value=prepared))
    chunks = [
        chunk
        async for chunk in workflow.execute_streaming(
            QUERY, context=current_sources.context, thread_id="thread-test"
        )
    ]
    assert len(chunks) == 1
    assert ACTUAL_DEFECTIVE_SHA256 not in chunks[0]
    assert chunks[0].count(NATIVE_E05_SHA256) == 1
    assert ("withheld" in chunks[0]) is bad_hash
