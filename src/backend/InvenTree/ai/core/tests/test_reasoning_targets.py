"""Diagnostic roots are authority candidates, never an arbitrary primary target."""

from types import SimpleNamespace

import pytest
from ai.core.reasoning.targets import reasoning_target_ids, voice_work_diagnostic_records


def root(entity_id, *, kind="machine", linked=None, revision="r1", name=""):
    return SimpleNamespace(
        entity_type=kind,
        entity_id=entity_id,
        linked_machine_id=linked,
        expected_revision=revision,
        display_name=name,
    )


def focus(machine_id):
    return {
        "focus": {
            "schema_version": 1,
            "kind": "machine",
            "id": machine_id,
            "machine_id": machine_id,
            "work_order_id": None,
            "label": "Selected equipment",
        },
        "version": 1,
    }


def test_confirmed_focus_wins_over_an_unrelated_last_packet():
    records = [root(42), root(24), root(7, kind="repair_packet", linked=24)]
    assert reasoning_target_ids(records, focus(42)) == (42, None)
    assert [record.entity_id for record in records] == [42, 24, 7]


def test_machine_focus_does_not_select_a_possibly_unrelated_packet():
    records = [
        root(42),
        root(24),
        root(8, kind="repair_packet", linked=42),
        root(7, kind="repair_packet", linked=24),
    ]
    assert reasoning_target_ids(records, focus(42)) == (42, None)
    records.append(root(9, kind="repair_packet", linked=42))
    assert reasoning_target_ids(records, focus(42)) == (42, None)


@pytest.mark.parametrize("snapshot", [focus(99), focus(True), {}, {"focus": None}, []])
def test_missing_or_unauthorized_focus_does_not_fall_back_to_other_equipment(snapshot):
    assert reasoning_target_ids([root(42)], snapshot) == (None, None)


def test_multiple_machines_without_focus_have_no_arbitrary_primary():
    assert reasoning_target_ids([root(42), root(24)], None) == (None, None)


def test_single_machine_legacy_context_stays_unambiguous():
    assert reasoning_target_ids([root(42), root(7, kind="repair_packet", linked=42)], None) == (
        42,
        7,
    )


def work_focus():
    snapshot = focus(42)
    snapshot["focus"].update(kind="work_order", id=80, work_order_id=80)
    return snapshot


def voice_roots():
    return (
        root(42, revision="machine-r3", name="Cedar Station / Pump 08"),
        root(24, revision="machine-r8", name="Other Station / Pump 09"),
        root(7, kind="repair_packet", linked=42, revision="packet-r2"),
        root(8, kind="repair_packet", linked=24, revision="packet-r9"),
    )


def project(records=None, snapshot=None, **overrides):
    kwargs = {
        "focused_repair_packet_id": 7,
        "content": "For Pump 08, machine 42, give me three checks to diagnose cooling.",
        "modality": "voice",
    }
    kwargs.update(overrides)
    return voice_work_diagnostic_records(
        voice_roots() if records is None else records,
        work_focus() if snapshot is None else snapshot,
        **kwargs,
    )


def test_explicit_voice_diagnosis_projects_exact_bound_root_revision_pairs():
    records = voice_roots()
    selected = project(records)
    assert selected == (records[0], records[2])
    assert [(item.entity_id, item.expected_revision) for item in selected] == [
        (42, "machine-r3"),
        (7, "packet-r2"),
    ]
    assert selected[0] is records[0] and selected[1] is records[2]
    assert len(records) == 4


@pytest.mark.parametrize(
    "content",
    [
        "Compare machine 42 with machine 24 and suggest checks.",
        "For machine 42, compare its checks with Other Station / Pump 09.",
        "For machine 42, diagnose all pumps across the station.",
        "For machine 42, diagnose station health and give checks.",
        "For machine 42, suggest checks using Other Station / Pump 08 records.",
        "For machine 42, suggest checks for Pump 09.",
        "For machine 24, give me three diagnostic checks.",
        "For machine 42, list the recent records.",
    ],
)
def test_comparisons_station_other_assets_and_nonexplicit_turns_keep_authority(content):
    records = voice_roots()
    assert project(records, content=content) == records


@pytest.mark.parametrize(
    "content",
    [
        "Give three checks for this motor.",
        "E03 reports airside cooler deposits and no visible damage. What is the leading cause and are replacement parts justified?",
        "What is the suspected cause in this work order?",
    ],
)
def test_current_case_voice_followups_keep_exact_bound_revisions(content):
    records = voice_roots()
    assert project(records, content=content) == (records[0], records[2])


@pytest.mark.parametrize(
    "content",
    [
        "What is the leading cause for machine 24?",
        "What is the cause for Pump 09?",
        "What is the cause at Other Station?",
        "What are the causes across the station?",
        "Compare the cause to the other case.",
    ],
)
def test_followup_projection_preserves_explicit_other_case_requests(content):
    records = voice_roots()
    assert project(records, content=content) == records


@pytest.mark.parametrize(
    "changes",
    [
        {"modality": "text"},
        {"focused_repair_packet_id": None},
        {"focused_repair_packet_id": 8},
        {"focused_repair_packet_id": True},
    ],
)
def test_projection_requires_voice_and_successful_same_case_source_binding(changes):
    records = voice_roots()
    assert project(records, **changes) == records


def test_focus_and_root_swaps_never_repair_or_copy_revision_pairs():
    records = voice_roots()
    swapped = work_focus()
    swapped["focus"]["machine_id"] = 24
    assert project(records, snapshot=swapped) == records
    wrong = list(records)
    wrong[2] = root(7, kind="repair_packet", linked=24, revision="packet-r2")
    assert project(wrong) == tuple(wrong)
    duplicated = (*records, records[0])
    assert project(duplicated) == duplicated
