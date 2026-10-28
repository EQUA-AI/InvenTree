"""Actual local reader receipts stay distinct from provider claims and source text."""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from ai.core.reasoning.luna_diagnostics import (
    AuthorizedRecord,
    LunaDiagnosticsAdapter,
    ToolLoopBudget,
)
from ai.core.tests.test_diagnostic_tool_registry import NOW, _context, _Reader
from ai.core.tests.test_luna_diagnostics import (
    _canonical_json,
    _Client,
    _config,
    _envelope,
    _response,
    _tool_call,
)
from ai.core.tools.diagnostics import (
    PACKET_READ_CAPABILITY,
    DiagnosticAuthorizationError,
    DiagnosticReadResult,
    DiagnosticRecordRoot,
    EvidenceClaim,
    ReaderResult,
    get_diagnostic_tool_registry,
)


class SourceReader(_Reader):
    def read(self, **_kwargs):
        return ReaderResult(
            evidence=(
                EvidenceClaim(
                    source_type="prepared_inspection_record",
                    id="209",
                    revision="a" * 64,
                    locator="work-order:84:attachment:209#P08-TEMP-E03",
                    as_of=NOW,
                    authorization_class="maintenance_scope",
                    claim="PRIVATE_SOURCE_BODY",
                    untrusted=True,
                ),
            )
        )


def test_receipt_describes_fresh_authorized_read_without_entering_model_payload(caplog):
    registry = get_diagnostic_tool_registry(reader=SourceReader(), clock=lambda: NOW)
    with caplog.at_level("INFO", logger="ai.core.tools.diagnostics"):
        result = registry.execute(
            "get_machine_context",
            {"machine_id": 11, "expected_revision": "machine-r3"},
            context=_context(),
        )
    assert isinstance(result, DiagnosticReadResult)
    receipt = result.read_receipt
    assert receipt["kind"] == "native_diagnostic_read"
    assert receipt["reader"] == "get_machine_context"
    assert receipt["entity_id"] == 11 and receipt["entity_revision"] == "machine-r3"
    assert receipt["authorization_check_id"] and receipt["context_id"]
    assert receipt["sources"] == [
        {
            "source_type": "prepared_inspection_record",
            "id": "209",
            "revision": "a" * 64,
        }
    ]
    assert (
        receipt["result_sha256"]
        == hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert "PRIVATE_SOURCE_BODY" not in json.dumps(receipt)
    assert "PRIVATE_SOURCE_BODY" not in caplog.text
    assert "native_diagnostic_read" in caplog.text
    assert "read_receipt" not in json.dumps(result)
    assert "authorization_check_id" not in json.dumps(result)


def test_denied_call_cannot_produce_successful_reader_receipt(caplog):
    registry = get_diagnostic_tool_registry(reader=SourceReader(), clock=lambda: NOW)
    with (
        caplog.at_level("INFO", logger="ai.core.tools.diagnostics"),
        pytest.raises(DiagnosticAuthorizationError),
    ):
        registry.execute(
            "get_machine_context",
            {"machine_id": 12, "expected_revision": "machine-r3"},
            context=_context(),
        )
    assert "diagnostics.read" not in caplog.text


def adapter(context):
    identity = context.record_roots[0].entity_id
    client = _Client([
        _response(
            calls=[
                _tool_call(
                    arguments=json.dumps({
                        "machine_id": identity,
                        "expected_revision": "machine-r3",
                    })
                )
            ]
        ),
        _response(text=_canonical_json()),
    ])
    return LunaDiagnosticsAdapter(
        provider_config=_config(),
        budget=ToolLoopBudget(),
        tool_registry=get_diagnostic_tool_registry(reader=SourceReader(), clock=lambda: NOW),
        client_factory=lambda: client,
    ), client


@pytest.mark.asyncio
async def test_actual_adapter_retains_receipt_on_outcome_and_does_not_replay_it_to_provider():
    context = _context()
    service, client = adapter(context)
    result = await service.reason(
        envelope=_envelope(tools=("get_machine_context",)),
        tool_context=context,
    )
    trace = result.provenance.to_dict()["tool_reads"]
    assert len(trace) == 1 and trace[0]["entity_id"] == 11
    assert trace[0]["sources"][0]["id"] == "209"
    assert "authorization_check_id" not in json.dumps(client.responses.calls)
    # No retained receipt is allowed to cross into the next invocation.
    client.responses.outputs = [_response(text=_canonical_json())]
    again = await service.reason(envelope=_envelope(), tool_context=context)
    assert "tool_reads" not in again.provenance.to_dict()


@pytest.mark.asyncio
async def test_concurrent_invocations_keep_their_own_exact_reader_roots():
    first = _context()
    second = replace(first, record_roots=(replace(first.record_roots[0], entity_id=12),))
    left, _ = adapter(first)
    right, _ = adapter(second)
    results = await asyncio.gather(
        left.reason(envelope=_envelope(tools=("get_machine_context",)), tool_context=first),
        right.reason(envelope=_envelope(tools=("get_machine_context",)), tool_context=second),
    )
    assert [[read["entity_id"] for read in item.provenance.tool_reads] for item in results] == [
        [11],
        [12],
    ]


def inspection_context():
    return replace(
        _context(),
        capabilities=(PACKET_READ_CAPABILITY,),
        record_roots=(
            DiagnosticRecordRoot(
                entity_type="repair_packet",
                entity_id=27,
                expected_revision="packet-r2",
                linked_machine_id=44,
            ),
        ),
        focused_repair_packet_id=27,
        prepared_inspection_requested=True,
    )


def inspection_envelope():
    return _envelope(tools=("get_requested_prepared_inspection",)).model_copy(
        update={
            "repair_packet_id": 27,
            "authorized_records": (
                AuthorizedRecord(
                    entity_type="repair_packet",
                    entity_id=27,
                    expected_revision="packet-r2",
                    linked_machine_id=44,
                ),
            ),
        }
    )


@pytest.mark.asyncio
async def test_requested_e03_is_first_read_and_uses_exact_server_argument_pair():
    client = _Client([
        _response(
            calls=[
                _tool_call(
                    name="get_requested_prepared_inspection",
                    arguments=json.dumps({
                        "repair_packet_id": 27,
                        "expected_revision": "packet-r2",
                    }),
                )
            ]
        ),
        _response(text=_canonical_json()),
    ])
    registry = get_diagnostic_tool_registry(reader=SourceReader(), clock=lambda: NOW)
    service = LunaDiagnosticsAdapter(
        provider_config=_config(),
        budget=ToolLoopBudget(),
        tool_registry=registry,
        client_factory=lambda: client,
    )
    result = await service.reason(envelope=inspection_envelope(), tool_context=inspection_context())
    first, second = client.responses.calls
    assert first["tool_choice"] == {"type": "function", "name": "get_requested_prepared_inspection"}
    assert "tool_choice" not in second
    props = first["tools"][0]["parameters"]["properties"]
    assert props["repair_packet_id"]["enum"] == [27]
    assert props["expected_revision"]["enum"] == ["packet-r2"]
    assert (
        "enum"
        not in registry.provider_tools(context=inspection_context())[0]["parameters"]["properties"][
            "repair_packet_id"
        ]
    )
    assert result.provenance.tool_names == ("get_requested_prepared_inspection",)
    assert result.provenance.tool_reads[0]["entity_id"] == 27


@pytest.mark.asyncio
async def test_provider_ignoring_required_source_cannot_return_an_unread_source_answer():
    client = _Client([_response(text=_canonical_json())])
    service = LunaDiagnosticsAdapter(
        provider_config=_config(),
        budget=ToolLoopBudget(),
        tool_registry=get_diagnostic_tool_registry(reader=SourceReader(), clock=lambda: NOW),
        client_factory=lambda: client,
    )
    result = await service.reason(envelope=inspection_envelope(), tool_context=inspection_context())
    assert result.provenance.outcome_code == "requested_source_not_read"
    assert result.response.response_state.value == "incomplete"
    assert result.provenance.tool_reads == ()
