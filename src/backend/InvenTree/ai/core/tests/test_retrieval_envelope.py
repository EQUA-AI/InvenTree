"""S5 WP-A2: the §7.4 retrieval envelope contract.

Pins the model-visible/internal split: the envelope itself carries coverage
and source state, while the authorization scope hash rides ONLY the capture
ledger — a serialization sweep proves it can never appear in a model
payload.
"""

# ruff: noqa: E402

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.analysis.scope import SCHEMA_VERSION, SOURCE_CLASSES
from ai.core.analysis.scope_context import bind_turn_scope, turn_scope_context
from ai.core.contracts.retrieval import (
    NO_RELEVANT_PASSAGE,
    SOURCE_STATE_KEYS,
    build_envelope,
    coverage,
    record_envelope,
)
from ai.core.tools.capture_ledger import bind_tool_captures, tool_capture_ledger

_FLAGS = SimpleNamespace(
    feature_ai_thread_scope_shadow=True,
    feature_ai_thread_scope_enforce=False,
)


@pytest.fixture(autouse=True)
def _clean_context():
    scope_token = turn_scope_context.set(None)
    ledger_token = tool_capture_ledger.set(None)
    yield
    turn_scope_context.reset(scope_token)
    tool_capture_ledger.reset(ledger_token)


def _bind_scope():
    snapshot = {
        "scope": {
            "schema_version": SCHEMA_VERSION,
            "mode": "explicit_assets",
            "machine_ids": [4],
            "date_window": {"from": None, "to": None},
            "source_classes": list(SOURCE_CLASSES),
            "display_label": "Pump 4",
        },
        "version": 3,
        "hash": "c" * 64,
    }
    with mock.patch("ai.core.config.get_settings", return_value=_FLAGS):
        return bind_turn_scope(snapshot, thread_pk=9, turn_pk=77)


class TestCoverage:
    def test_display_truncated_derives_from_counts(self) -> None:
        block = coverage(population_count=400, returned_count=25, complete_population=False)
        assert block["display_truncated"] is True
        full = coverage(population_count=25, returned_count=25, complete_population=True)
        assert full["display_truncated"] is False

    def test_explicit_truncation_wins(self) -> None:
        block = coverage(
            population_count=602,
            returned_count=24,
            complete_population=True,  # server-side grouping evaluated all rows
            display_truncated=True,
        )
        assert block["complete_population"] is True
        assert block["display_truncated"] is True


class TestEnvelope:
    def test_snapshot_id_rides_from_the_bound_scope(self) -> None:
        context = _bind_scope()
        envelope = build_envelope(
            source_class="work_order", population_type="work_orders", operation="search"
        )
        assert envelope["snapshot_id"] == context.snapshot_id
        assert envelope["retrieval_id"].startswith("ret_")

    def test_unscoped_turn_has_null_snapshot(self) -> None:
        envelope = build_envelope(
            source_class="machine", population_type="machines", operation="search"
        )
        assert envelope["snapshot_id"] is None

    def test_source_state_is_normalized_to_the_a11_keys(self) -> None:
        envelope = build_envelope(
            source_class="controlled_document",
            population_type="document_chunks",
            operation="semantic_search",
            source_state={"indexed": True, "bogus": True},
        )
        assert set(envelope["source_state"]) == set(SOURCE_STATE_KEYS)
        assert envelope["source_state"]["indexed"] is True
        assert envelope["source_state"]["registered"] is False

    def test_zero_hit_warning_vocabulary(self) -> None:
        assert NO_RELEVANT_PASSAGE == "no_relevant_passage_retrieved"


class TestInternalSplit:
    def test_scope_hash_reaches_the_ledger_never_the_envelope(self) -> None:
        _bind_scope()
        ledger = bind_tool_captures()
        envelope = build_envelope(
            source_class="work_order",
            population_type="work_orders",
            operation="search",
            coverage=coverage(population_count=3, returned_count=3, complete_population=True),
        )
        record_envelope("search_work_orders", envelope, out_of_scope_count=1)

        # The model-visible half never carries the digest.
        assert "c" * 64 not in json.dumps(envelope)
        # The internal half does, with the tool identity and coverage.
        metas = ledger.retrieval_metas()
        assert len(metas) == 1
        assert metas[0]["tool_id"] == "search_work_orders"
        assert metas[0]["authorization_scope_hash"] == "c" * 64
        assert metas[0]["out_of_scope_count"] == 1
        assert metas[0]["retrieval_id"] == envelope["retrieval_id"]

    def test_recording_without_a_ledger_is_a_noop(self) -> None:
        envelope = build_envelope(
            source_class="machine", population_type="machines", operation="search"
        )
        record_envelope("search_machines", envelope)  # must not raise

    def test_ledger_meta_capacity_is_bounded(self) -> None:
        ledger = bind_tool_captures()
        envelope = build_envelope(
            source_class="machine", population_type="machines", operation="search"
        )
        for _ in range(100):
            record_envelope("search_machines", envelope)
        assert len(ledger.retrieval_metas()) <= 64
