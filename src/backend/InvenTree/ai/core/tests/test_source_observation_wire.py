"""Unknown source coverage survives strict contracts and both event dialects."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from ai.core.analysis.wire import SourceObservationPayload
from pydantic import ValidationError


def payload():
    """Only an observed selected interval; no whole-archive assertion."""
    return {
        "schema_version": "aimms.source-observation.v1",
        "kind": "recorded_history",
        "population_count": None,
        "complete_population": False,
        "revision_complete": False,
        "returned_count": 1,
        "captured_at": "2026-11-13T12:00:00Z",
        "recorded_only": True,
        "window": [1000, 5000],
        "window_complete": False,
        "gaps": [{"window": [3000, 5000], "reason": "source_unavailable"}],
        "source_evidence_kind": "synthetic_fixture",
        "citations": [],
    }


def test_source_contract_retains_nulls_and_selected_window():
    """The same validated object serializes without false zero population."""
    value = payload()
    assert SourceObservationPayload.model_validate(value).model_dump(mode="json") == value


def test_json_chat_preserves_source_nulls_and_default_off_shape():
    """The non-streaming route adds only a validated source observation."""
    from ai.core.app import ChatResponse

    plain = {"thread_id": "fixture-thread", "message": "Fixture", "agent": "fixture"}
    assert "source_observation" not in ChatResponse(**plain).model_dump(mode="json")
    source = ChatResponse(**plain, source_observation=payload()).model_dump(mode="json")
    assert source["source_observation"] == payload()
    from aichat.management.commands.generate_wire_contract import Command

    assert (
        "source_observation?: SourceObservationPayload | SourceObservationUnavailable | null;"
        in Command()._render()
    )


def test_unminted_source_metadata_cannot_become_a_live_source_result():
    """A public shape or copied private-looking dict is not a captured observation."""
    from ai.core.integrations.source_reads import SourceReadError
    from ai.core.integrations.source_transcripts import PUBLIC_KEY, REF_KEY, live_canonical

    with pytest.raises(SourceReadError):
        asyncio.run(
            live_canonical(
                SimpleNamespace(extras={}),
                {
                    "message": "Unbound",
                    REF_KEY: {},
                    PUBLIC_KEY: payload(),
                },
                required=True,
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"population_count": 0},
        {"complete_population": 0},
        {"revision_complete": True},
        {"returned_count": True},
        {"returned_count": -1},
        {"returned_count": "1"},
        {"window": [5000, 1000]},
        {"window": [True, 5000]},
        {"window_complete": True},
        {"recorded_only": False},
        {"captured_at": "2026-11-13T12:00:00"},
        {"gaps": [{"window": [4000, 6000], "reason": "outside"}]},
    ],
)
def test_source_contract_rejects_coercion_and_incoherent_scope(changes):
    """Malformed coverage never becomes complete or empty source success."""
    value = copy.deepcopy(payload())
    value.update(changes)
    with pytest.raises(ValidationError):
        SourceObservationPayload.model_validate(value)
