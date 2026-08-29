"""S7 WP-C4: snapshot v1, deterministic plan-lite, analytics retrieval bodies.

The tasks side is faked at the module seam (``tasks.ai_analytics``), the
same posture as the executor-seam patches in ``test_analysis_executor`` —
the real ORM behavior is pinned by ``tasks/tests/test_ai_analytics.py``.
"""

from __future__ import annotations

import datetime
import sys
import types
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from ai.core.analysis.evidence import EvidenceStore
from ai.core.analysis.executor import _retrieve_aggregate, _retrieve_trend
from ai.core.analysis.intent import held_back_intents
from ai.core.analysis.plans import (
    build_aggregate_plan,
    build_trend_plan,
    default_trend_window,
)
from ai.core.analysis.snapshot import (
    AnalysisRetrievalIncomplete,
    build_manifest,
    operand_hash,
)

AS_OF = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)


class TestPlans:
    def test_aggregate_grouping_keywords(self) -> None:
        assert build_aggregate_plan("work orders by priority")["grouping"] == "priority"
        assert (
            build_aggregate_plan("corrective vs preventive counts")["grouping"] == "work_order_type"
        )
        assert build_aggregate_plan("how many per machine")["grouping"] == "machine"

    def test_person_grouping_is_a_deliberate_refusal_probe(self) -> None:
        # §8.3: identities are never grouped. The mapper emits the
        # unsupported name ON PURPOSE so the server allow-list refuses it.
        assert build_aggregate_plan("work orders per technician")["grouping"] == "performer"

    def test_date_field_domain_defaults(self) -> None:
        assert (
            build_aggregate_plan("repairs completed by machine")["date_field"]
            == "actual_completed_at"
        )
        assert build_aggregate_plan("scheduled work by priority")["date_field"] == "scheduled_start"
        assert build_aggregate_plan("open work orders")["date_field"] == "created_at"

    def test_trend_population_and_bucket(self) -> None:
        plan = build_trend_plan("maintenance records per quarter")
        assert plan["population_type"] == "maintenance_records"
        assert plan["date_field"] == "date"
        assert plan["bucket"] == "quarter"
        default = build_trend_plan("work orders over time")
        assert default["population_type"] == "work_orders"
        assert default["bucket"] == "month"

    def test_default_trend_window_is_rolling_and_half_open(self) -> None:
        start, end = default_trend_window(datetime.date(2026, 8, 29))
        assert start == "2025-08-01"
        assert end == "2026-09-01"


class TestSnapshotPrimitives:
    def test_operand_hash_detects_every_kind_of_change(self) -> None:
        base = [(1, "a"), (2, "b")]
        assert operand_hash(base) == operand_hash(list(base))
        assert operand_hash(base) != operand_hash([(1, "a")])  # deletion
        assert operand_hash(base) != operand_hash([(1, "a"), (2, "c")])  # version
        assert operand_hash(base) != operand_hash([(1, "a"), (2, "b"), (3, "d")])

    def test_manifest_counts_and_projects(self) -> None:
        manifest = build_manifest(
            snapshot_id="snap_x",
            operands=[(1, "a"), (2, "b")],
            sources={"work_order": {"high_watermark": "hw"}},
            plan={"intent": "fleet_aggregate"},
            as_of="2026-08-29T12:00:00+00:00",
            notes=("row_pinned_only",),
        )
        assert manifest.operand_count == 2
        assert manifest.operand_hash == operand_hash([(1, "a"), (2, "b")])
        blob = manifest.to_dict()
        assert blob["plan"] == {"intent": "fleet_aggregate"}
        assert blob["notes"] == ["row_pinned_only"]


class TestHoldbackParsing:
    def test_csv_parses_and_defaults_empty(self) -> None:
        settings = SimpleNamespace(
            aimms_analysis_intent_holdback=" fleet_aggregate , trend_analysis ,"
        )
        assert held_back_intents(settings) == {"fleet_aggregate", "trend_analysis"}
        assert held_back_intents(SimpleNamespace()) == frozenset()


# --- retrieval bodies against a faked tasks seam ---------------------------


class _FakeAnalyticsError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _aggregate_result(**overrides):
    result = {
        "operation": "aggregate_work_orders",
        "population_type": "work_orders",
        "available": True,
        "grouping": "machine",
        "population_count": 3,
        "evaluated_count": 3,
        "complete_population": True,
        "date_field": "created_at",
        "timezone": "UTC",
        "groups": [
            {"key": 12, "label": "Feed Pump", "group_count": 2},
            {"key": 15, "label": "Inverter Hall", "group_count": 1},
        ],
        "total_group_count": 2,
        "groups_truncated": False,
        "remainder_group_count": 0,
        "remainder_count": 0,
        "unassigned_machine_count": 0,
        "applied_filters": {"date_field": "created_at", "timezone": "UTC"},
        "high_watermark": "2026-08-29T11:00:00+00:00",
    }
    result.update(overrides)
    return result


def _profile_result():
    return {
        "operation": "work_order_dataset_profile",
        "population_type": "work_orders",
        "available": True,
        "population_count": 3,
        "complete_population": True,
        "date_field": "created_at",
        "timezone": "UTC",
        "date_min": "2026-01-15T03:00:00+00:00",
        "date_max": "2026-02-10T09:00:00+00:00",
        "null_date_count": 0,
        "unassigned_machine_count": 0,
        "distinct_machine_count": 2,
        "lifecycle_status_counts": {},
        "work_order_type_counts": {},
        "applied_filters": {},
        "high_watermark": "2026-08-29T11:00:00+00:00",
    }


def _timeline_result(**overrides):
    result = {
        "operation": "timeline",
        "population_type": "work_orders",
        "available": True,
        "bucket": "month",
        "population_count": 2,
        "evaluated_count": 2,
        "complete_population": True,
        "date_field": "created_at",
        "timezone": "UTC",
        "buckets": [
            {"bucket": "2026-01-01", "group_count": 1},
            {"bucket": "2026-02-01", "group_count": 1},
        ],
        "bucket_count": 2,
        "null_date_count": 0,
        "applied_filters": {"date_field": "created_at", "timezone": "UTC"},
        "high_watermark": "2026-08-29T11:00:00+00:00",
    }
    result.update(overrides)
    return result


@pytest.fixture
def fake_analytics(monkeypatch):
    """Install a stub ``tasks.ai_analytics`` and a settings-free clock."""
    module = types.ModuleType("tasks.ai_analytics")
    module.AnalyticsRequestError = _FakeAnalyticsError
    module.calls = []

    versions = {"available": True, "overflow": False, "rows": [(41, "v1"), (42, "v1")]}
    module.version_sequence = [versions, versions]

    def work_order_operand_versions(user, **kwargs):
        module.calls.append(("versions", kwargs))
        if module.version_sequence:
            return module.version_sequence.pop(0)
        return versions

    def maintenance_record_operand_versions(user, **kwargs):
        module.calls.append(("record_versions", kwargs))
        if module.version_sequence:
            return module.version_sequence.pop(0)
        return versions

    module.work_order_operand_versions = work_order_operand_versions
    module.maintenance_record_operand_versions = maintenance_record_operand_versions
    module.get_work_order_dataset_profile = lambda *_args, **_kwargs: _profile_result()
    module.aggregate_work_orders = lambda user, **kwargs: module.aggregate_impl(user, **kwargs)
    module.aggregate_impl = lambda *_args, **_kwargs: _aggregate_result()

    def get_work_order_timeline(user, **kwargs):
        module.calls.append(("timeline", kwargs))
        return _timeline_result(
            population_type=kwargs.get("population", "work_orders"),
        )

    module.get_work_order_timeline = get_work_order_timeline
    module.plant_timezone = lambda: (ZoneInfo("UTC"), "UTC")

    package = types.ModuleType("tasks")
    package.ai_analytics = module
    monkeypatch.setitem(sys.modules, "tasks", package)
    monkeypatch.setitem(sys.modules, "tasks.ai_analytics", module)
    monkeypatch.setattr("django.utils.timezone.now", lambda: AS_OF)
    return module


def _run() -> SimpleNamespace:
    return SimpleNamespace(query_plan=None)


class TestRetrieveAggregate:
    def test_happy_path_builds_facts_set_and_manifest(self, fake_analytics) -> None:
        store = EvidenceStore()
        run = _run()
        result = _retrieve_aggregate(
            object(), store, scope=None, query="work orders per machine", run=run
        )
        assert result["available"]
        kinds = [fact.kind for fact in store.facts.values()]
        assert kinds.count("dataset_profile") == 1
        assert kinds.count("group_row") == 2
        assert len(store.evidence_sets) == 1
        pending = next(iter(store.evidence_sets.values()))
        assert [member[2] for member in pending.members] == ["41", "42"]
        assert pending.snapshot_hash == run.query_plan.operand_hash
        assert run.query_plan.plan["grouping"] == "machine"
        assert run.query_plan.notes == ("row_pinned_only",)
        calculation = next(iter(store.calculations.values()))
        assert calculation.operation == "group_count"
        assert calculation.complete_population
        assert (store.coverage_meta() or {})["timezone"] == "UTC"

    def test_snapshot_divergence_retries_once_then_types(self, fake_analytics) -> None:
        changed = {"available": True, "overflow": False, "rows": [(41, "v2")]}
        base = {"available": True, "overflow": False, "rows": [(41, "v1")]}
        # scan A, recheck B -> retry; scan B, recheck B -> success.
        fake_analytics.version_sequence = [base, changed, changed, changed]
        store = EvidenceStore()
        run = _run()
        _retrieve_aggregate(object(), store, scope=None, query="counts", run=run)
        assert run.query_plan.operand_hash == operand_hash([(41, "v2")])

        # Never settles: A, B, C, D -> typed snapshot_changed.
        fake_analytics.version_sequence = [
            {"available": True, "overflow": False, "rows": [(41, version)]}
            for version in ("a", "b", "c", "d")
        ]
        with pytest.raises(AnalysisRetrievalIncomplete) as caught:
            _retrieve_aggregate(object(), EvidenceStore(), scope=None, query="counts", run=_run())
        assert caught.value.code == "snapshot_changed"

    def test_overflow_is_population_cap_exceeded(self, fake_analytics) -> None:
        fake_analytics.version_sequence = [{"available": True, "overflow": True, "rows": []}]
        with pytest.raises(AnalysisRetrievalIncomplete) as caught:
            _retrieve_aggregate(object(), EvidenceStore(), scope=None, query="counts", run=_run())
        assert caught.value.code == "population_cap_exceeded"

    def test_vocabulary_errors_map_onto_wire_codes(self, fake_analytics) -> None:
        def _refuse(user, **kwargs):
            raise _FakeAnalyticsError("grouping_unavailable", "no such grouping")

        fake_analytics.aggregate_impl = _refuse
        with pytest.raises(AnalysisRetrievalIncomplete) as caught:
            _retrieve_aggregate(
                object(), EvidenceStore(), scope=None, query="per technician", run=_run()
            )
        assert caught.value.code == "grouping_unavailable"

    def test_unavailable_population_builds_nothing(self, fake_analytics) -> None:
        fake_analytics.version_sequence = [{"available": False, "rows": [], "overflow": False}]
        store = EvidenceStore()
        run = _run()
        _retrieve_aggregate(object(), store, scope=None, query="counts", run=run)
        assert not store.facts
        assert not store.evidence_sets
        assert run.query_plan is None
        assert (store.coverage_meta() or {})["incomplete_reason"] == "analytics_unavailable"


class TestRetrieveTrend:
    def test_default_window_bounds_an_open_question(self, fake_analytics) -> None:
        store = EvidenceStore()
        run = _run()
        _retrieve_trend(object(), store, scope=None, query="work orders over time", run=run)
        timeline_calls = [kwargs for name, kwargs in fake_analytics.calls if name == "timeline"]
        assert timeline_calls, "timeline was never called"
        assert timeline_calls[0]["date_from"] is not None
        assert timeline_calls[0]["date_to"] is not None
        assert run.query_plan.plan["window_default"] == "last_12_months"
        kinds = [fact.kind for fact in store.facts.values()]
        assert kinds.count("group_row") == 2
        calculation = next(iter(store.calculations.values()))
        assert calculation.operation == "bucket_count"

    def test_records_population_uses_record_operands(self, fake_analytics) -> None:
        store = EvidenceStore()
        run = _run()
        _retrieve_trend(
            object(),
            store,
            scope=None,
            query="maintenance records over time",
            run=run,
        )
        scan_names = [name for name, _kwargs in fake_analytics.calls]
        assert "record_versions" in scan_names
        pending = next(iter(store.evidence_sets.values()))
        assert pending.source_class == "maintenance_record"
        assert {member[1] for member in pending.members} == {"maintenance_record"}
