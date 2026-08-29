"""Unit tests for the S10 evidence store (WP-A2)."""

from __future__ import annotations

import json

from ai.core.analysis.evidence import (
    EVIDENCE_SET_MEMBER_CAP,
    EvidenceStore,
    FactValue,
    coverage_fact,
    fact_from_dataset_profile,
    fact_from_manual_citation,
    facts_from_group_rows,
    facts_from_inventory_rows,
    facts_from_maintenance_record_row,
    facts_from_work_order_row,
)

AS_OF = "2026-08-27T12:00:00+00:00"


def _work_order_row(**overrides) -> dict:
    row = {
        "work_order_id": 41,
        "reference": "WO-0041",
        "title": "Replace coolant filter",
        "board_status": "in_progress",
        "lifecycle_status": "released",
        "work_order_type": "corrective",
        "priority": "high",
        "assigned": True,
        "assigned_to": "technician",
        "machine_id": 12,
        "machine": "Feed Pump East",
        "due_date": "2026-09-01",
        "created_at": "2026-08-20T08:00:00+00:00",
        "updated_at": "2026-08-21T08:00:00+00:00",
        "actual_started_at": None,
        "actual_completed_at": None,
    }
    row.update(overrides)
    return row


class TestFactValueRendering:
    def test_rendering_is_deterministic_per_type(self) -> None:
        assert FactValue("int", 7).render() == "7"
        assert FactValue("bool", True).render() == "yes"
        assert FactValue("bool", False).render() == "no"
        assert FactValue("decimal", "12.500").render() == "12.5"
        assert FactValue("duration_days", 1).render() == "1 day"
        assert FactValue("duration_days", 14).render() == "14 days"
        assert FactValue("unit_quantity", 30, unit="bar").render() == "30 bar"
        assert FactValue("date", "2026-09-01").render() == "2026-09-01"
        assert FactValue("identifier", "WO-0041").render() == "WO-0041"

    def test_none_renders_as_not_recorded_never_imputed(self) -> None:
        assert FactValue("date", None).render() == "not recorded"
        assert FactValue("int", None).render() == "not recorded"


class TestStoreAccumulation:
    def test_work_order_adapter_produces_typed_fact(self) -> None:
        store = EvidenceStore()
        fact_id = facts_from_work_order_row(
            store,
            _work_order_row(),
            retrieval_id="ret_abc",
            as_of=AS_OF,
            source_revision="snap_1",
        )
        fact = store.facts[fact_id]
        assert fact.source_class == "work_order"
        assert fact.machine_id == 12
        assert set(fact.entity_refs) == {"workorder:41", "machine:12"}
        rendered = fact.rendered_values()
        assert rendered["reference"] == "WO-0041"
        assert rendered["actual_started_at"] == "not recorded"

    def test_inserted_value_index_covers_every_rendering(self) -> None:
        store = EvidenceStore()
        facts_from_work_order_row(
            store,
            _work_order_row(),
            retrieval_id="ret_abc",
            as_of=AS_OF,
            source_revision="snap_1",
        )
        store.add_calculation(
            operation="count",
            input_refs=("fact_1",),
            values={"count": FactValue("int", 3)},
        )
        index = store.inserted_value_index()
        assert "WO-0041" in index
        assert "3" in index
        assert "2026-09-01" in index

    def test_coverage_and_manual_and_inventory_adapters(self) -> None:
        store = EvidenceStore()
        coverage_fact(
            store,
            {"population_count": 403, "returned_count": 25, "complete_population": False},
            retrieval_id="ret_c",
            source_class="work_order",
            as_of=AS_OF,
        )
        manual = fact_from_manual_citation(
            store,
            {
                "document": "HX-200 Manual",
                "document_id": "MAN-HX200",
                "revision": "C",
                "chunk_id": "chunk-9",
                "section_path": "4.2",
            },
            retrieval_id="ret_m",
            as_of=AS_OF,
        )
        facts_from_inventory_rows(
            store,
            [{"document_id": "MAN-HX200", "revision": "C", "indexed": True, "chunks": 12}],
            retrieval_id="ret_i",
            as_of=AS_OF,
        )
        assert store.facts[manual].controlled is True
        index = store.inserted_value_index()
        assert {"403", "25", "no", "HX-200 Manual", "C", "yes", "12"} <= index
        assert store.retrieval_ids() == {"ret_c", "ret_m", "ret_i"}


class TestEvidenceSets:
    def test_member_cap_degrades_to_digest_only(self) -> None:
        store = EvidenceStore()
        pending = store.open_evidence_set(
            source_class="work_order",
            filters={"machine_ids": [12]},
            population_count=EVIDENCE_SET_MEMBER_CAP + 1,
            evaluated_count=EVIDENCE_SET_MEMBER_CAP + 1,
            complete_population=True,
        )
        pending.member_cap = 3  # keep the unit test cheap; semantics identical
        assert pending.add_member("work_order", "1")
        assert pending.add_member("work_order", "2")
        assert pending.add_member("work_order", "3")
        assert pending.add_member("work_order", "4") is False
        assert pending.supports_expansion is False
        assert [member[0] for member in pending.members] == [1, 2, 3]

    def test_persistence_spec_carries_members_but_digest_does_not(self) -> None:
        store = EvidenceStore()
        pending = store.open_evidence_set(
            source_class="work_order",
            filters={"machine_ids": [12]},
            population_count=2,
            evaluated_count=2,
            complete_population=True,
            calculation={"operation": "count", "result": "2"},
        )
        pending.add_member("work_order", "41", "v3")
        pending.add_member("work_order", "42", "v1")
        digest = pending.digest()
        assert "members" not in digest
        assert digest["handle"].startswith("set_")
        (spec,) = store.persistence_specs()
        assert spec["id"] == pending.handle
        assert spec["members"] == [(1, "work_order", "41", "v3"), (2, "work_order", "42", "v1")]


class TestSynthesisViewFence:
    def test_view_carries_digests_and_never_internal_fields(self) -> None:
        store = EvidenceStore()
        facts_from_work_order_row(
            store,
            _work_order_row(),
            retrieval_id="ret_abc",
            as_of=AS_OF,
            source_revision="snap_1",
        )
        pending = store.open_evidence_set(
            source_class="work_order",
            filters={"machine_ids": [12]},
            population_count=1,
            evaluated_count=1,
            complete_population=True,
        )
        pending.add_member("work_order", "41")
        store.record_envelope({
            "retrieval_id": "ret_abc",
            "coverage": {"population_count": 1, "returned_count": 1},
        })
        store.set_primary_coverage({"population_count": 1, "returned_count": 1})

        view = store.synthesis_view(template_keys=("analysis.record_count",))
        blob = json.dumps(view)
        assert "authorization_scope_hash" not in blob
        assert "members" not in blob
        assert '"41"' not in json.dumps(view["evidence_sets"])
        assert view["render_templates"] == ["analysis.record_count"]
        assert view["coverage"] == {"population_count": 1, "returned_count": 1}


class TestAnalyticsAdapters:
    """The S7 fact kinds: records, group rows, dataset profiles."""

    def test_maintenance_record_adapter_is_its_own_population(self) -> None:
        store = EvidenceStore()
        fact_id = facts_from_maintenance_record_row(
            store,
            {
                "record_id": 9,
                "machine_id": 12,
                "date": "2026-01-15",
                "summary": "[UNTRUSTED-CONTENT-BEGIN]\nService\n[UNTRUSTED-CONTENT-END]",
                "details": None,
                "updated_at": "2026-01-15T08:00:00+00:00",
            },
            retrieval_id="ret_rec",
            as_of=AS_OF,
            source_revision="snap_1",
        )
        fact = store.facts[fact_id]
        assert fact.kind == "maintenance_record"
        assert fact.source_class == "maintenance_record"
        assert fact.machine_id == 12
        assert fact.entity_refs == ("machine:12",)
        assert "details" not in fact.values

    def test_group_rows_cover_every_cell_and_only_known_cells(self) -> None:
        store = EvidenceStore()
        fact_ids = facts_from_group_rows(
            store,
            [
                {
                    "key": 12,
                    "label": "Feed Pump East",
                    "group_count": 7,
                    "smuggled_column": "EVIL-999",
                },
                {"key": 15, "label": "Inverter Hall", "group_count": 3},
            ],
            retrieval_id="ret_agg",
            as_of=AS_OF,
            source_class="work_order",
            source_revision="snap_1",
            grouping="machine",
        )
        assert len(fact_ids) == 2
        first = store.facts[fact_ids[0]]
        assert first.kind == "group_row"
        assert first.machine_id == 12
        assert first.entity_refs == ("machine:12",)
        index = store.inserted_value_index()
        assert {"7", "3", "Feed Pump East", "Inverter Hall"} <= index
        # A column outside the cell vocabulary never enters the store.
        assert "EVIL-999" not in index

    def test_bucket_rows_carry_no_machine_entity(self) -> None:
        store = EvidenceStore()
        fact_ids = facts_from_group_rows(
            store,
            [{"bucket": "2026-01-01", "group_count": 0}],
            retrieval_id="ret_tl",
            as_of=AS_OF,
            source_class="work_order",
            source_revision="snap_1",
            grouping="bucket",
        )
        fact = store.facts[fact_ids[0]]
        assert fact.entity_refs == ()
        assert fact.rendered_values()["group_count"] == "0"

    def test_dataset_profile_adapter_carries_honest_counts(self) -> None:
        store = EvidenceStore()
        fact_id = fact_from_dataset_profile(
            store,
            {
                "population_type": "work_orders",
                "population_count": 402,
                "null_date_count": 5,
                "unassigned_machine_count": 2,
                "distinct_machine_count": 37,
                "date_field": "actual_completed_at",
                "timezone": "America/New_York",
                "complete_population": True,
                "date_min": "2016-02-01T00:00:00+00:00",
                "date_max": "2026-02-10T09:00:00+00:00",
                "high_watermark": "2026-02-11T00:00:00+00:00",
            },
            retrieval_id="ret_prof",
            as_of=AS_OF,
        )
        fact = store.facts[fact_id]
        assert fact.kind == "dataset_profile"
        rendered = fact.rendered_values()
        assert rendered["population_count"] == "402"
        assert rendered["unassigned_machine_count"] == "2"
        assert rendered["complete_population"] == "yes"
        assert fact.source_revision == "2026-02-11T00:00:00+00:00"

    def test_new_calculation_operations_are_typed(self) -> None:
        store = EvidenceStore()
        calc_id = store.add_calculation(
            operation="duration_stats",
            input_refs=(),
            values={
                "qualifying_count": FactValue("int", 12),
                "median_minutes": FactValue("decimal", "95.0"),
            },
            complete_population=True,
        )
        calculation = store.calculations[calc_id]
        assert calculation.operation == "duration_stats"
        assert calculation.rendered_values()["median_minutes"] == "95"
