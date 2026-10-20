"""Conversation-focus contract: typed candidates and canonical projections.

The focus is a DISTINCT concept from analysis scope and authorization: it is
the particular machine, board card ("task") or work order the conversation is
currently about. This module pins the stdlib-only contract shared by the
Django plane (``aichat.services.threads``) and the AI plane:

* a candidate reference is ``{kind, id}`` with a strict positive integer id;
* the canonical projection is server-resolved (labels, machine/work-order
  relationships) — a client id is a candidate, never a grant;
* stored payloads fail closed: anything malformed reads as "no focus", never
  as another numbered record;
* board-column status and governed work-order lifecycle status are separate
  fields and are never conflated.
"""

from __future__ import annotations

import pytest
from ai.core.focus import contract

# ---- candidate normalization ---------------------------------------------


def test_normalize_accepts_each_kind_with_positive_int_id() -> None:
    for kind in contract.FOCUS_KINDS:
        candidate = contract.normalize_focus_request({"kind": kind, "id": 7})
        assert candidate.kind == kind
        assert candidate.id == 7


def test_focus_kinds_are_exactly_machine_card_and_work_order() -> None:
    assert contract.FOCUS_KINDS == ("machine", "kanban_card", "work_order")


def test_normalize_rejects_bool_and_non_positive_ids() -> None:
    for bad in (True, False, 0, -1, 1.0, "3", None, [], {}):
        with pytest.raises(contract.FocusValidationError):
            contract.normalize_focus_request({"kind": "machine", "id": bad})


def test_normalize_rejects_unknown_or_missing_kind() -> None:
    for bad in ("task", "workorder", "", None, 3):
        with pytest.raises(contract.FocusValidationError):
            contract.normalize_focus_request({"kind": bad, "id": 1})
    with pytest.raises(contract.FocusValidationError):
        contract.normalize_focus_request({"id": 1})


def test_normalize_rejects_non_mapping_and_extra_keys() -> None:
    with pytest.raises(contract.FocusValidationError):
        contract.normalize_focus_request([("kind", "machine")])
    with pytest.raises(contract.FocusValidationError):
        contract.normalize_focus_request({"kind": "machine", "id": 1, "grant": True})


def test_task_and_card_aliases_are_not_contract_kinds() -> None:
    """The UI may label a card "Task", but the wire kind stays kanban_card."""
    assert "task" not in contract.FOCUS_KINDS
    assert "card" not in contract.FOCUS_KINDS


# ---- canonical projection ------------------------------------------------


def _card_target() -> contract.FocusTarget:
    return contract.FocusTarget(
        kind="kanban_card",
        id=11,
        label="Diagnose the pump",
        machine_id=3,
        work_order_id=5,
    )


def test_focus_payload_carries_dual_identity_without_hashes_or_versions() -> None:
    payload = contract.focus_payload(_card_target())
    assert payload == {
        "kind": "kanban_card",
        "id": 11,
        "label": "Diagnose the pump",
        "machine_id": 3,
        "work_order_id": 5,
    }
    assert "hash" not in payload
    assert "version" not in payload


def test_stored_payload_round_trips_and_degrades_to_no_focus() -> None:
    stored = contract.focus_to_stored(_card_target())
    assert stored["schema_version"] == contract.SCHEMA_VERSION
    assert contract.focus_from_stored(stored) == _card_target()
    # Fail closed: empty or malformed storage reads as NO focus, never as a
    # different numbered record.
    assert contract.focus_from_stored({}) is None
    assert contract.focus_from_stored(None) is None
    for bad in (
        {"schema_version": 99, "kind": "machine", "id": 1},
        {"schema_version": 1, "kind": "nope", "id": 1},
        {"schema_version": 1, "kind": "machine", "id": 0},
        {"schema_version": 1, "kind": "machine", "id": True},
        "junk",
        [],
    ):
        assert contract.focus_from_stored(bad) is None


def test_machine_target_resolves_its_own_identity() -> None:
    target = contract.FocusTarget(
        kind="machine",
        id=3,
        label="Influent Pump Station No. 1",
        machine_id=3,
        work_order_id=None,
    )
    payload = contract.focus_payload(target)
    assert payload["machine_id"] == 3
    assert payload["work_order_id"] is None


def test_work_order_target_keeps_work_order_id_distinct_from_machine_id() -> None:
    target = contract.FocusTarget(
        kind="work_order",
        id=5,
        label="WO-1042",
        machine_id=3,
        work_order_id=5,
    )
    payload = contract.focus_payload(target)
    assert payload["id"] == 5
    assert payload["work_order_id"] == 5
    assert payload["machine_id"] == 3


def test_card_identity_is_never_the_work_order_identity() -> None:
    """A card id is its own PK; the parent work order rides a separate field."""
    payload = contract.focus_payload(_card_target())
    assert payload["id"] != payload["work_order_id"]
    assert payload["id"] != payload["machine_id"]


# ---- board column vs governed lifecycle ----------------------------------


def test_board_status_and_lifecycle_status_are_separate_fields() -> None:
    """A board-column change must never imply a governed lifecycle transition."""
    read = contract.card_read_payload(
        kanban_card_id=11,
        work_order_id=5,
        machine_id=3,
        title="Diagnose the pump",
        card_kind="subtask",
        board_column_status="in-progress",
        work_order_lifecycle_status="draft",
        work_order_reference="WO-1042",
    )
    assert read["kanban_card_id"] == 11
    assert read["work_order_id"] == 5
    assert read["machine_id"] == 3
    assert read["board_column_status"] == "in-progress"
    assert read["work_order_lifecycle_status"] == "draft"
    assert read["work_order_lifecycle_status"] != read["board_column_status"]


def test_card_read_payload_never_bare_id() -> None:
    """No ambiguous bare ``id`` — both identities are named explicitly."""
    read = contract.card_read_payload(
        kanban_card_id=11,
        work_order_id=5,
        machine_id=None,
        title="Seal kit",
        card_kind="procurement",
        board_column_status="backlog",
        work_order_lifecycle_status="active",
        work_order_reference=None,
    )
    assert "id" not in read
    assert read["work_order_reference"] is None
    assert read["machine_id"] is None


def test_focus_rejection_is_generic() -> None:
    """Unknown and unauthorized must be indistinguishable to a caller."""
    exc = contract.FocusRejected()
    assert str(exc) == "Focus update rejected"
    assert not isinstance(exc, contract.FocusValidationError)
