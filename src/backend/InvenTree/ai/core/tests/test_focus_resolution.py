"""Focus candidate resolution: real readers, generic rejection, relationships.

The resolver is the ONLY path from a client candidate reference to a stored
focus. It dispatches each kind to the existing authorized reader
(``assets.ai_read.authorized_machine``, ``tasks.ai_read.authorized_work_order``,
``tasks.ai_read.authorized_kanban_card``) — the readers own the actor policy,
and a failure is reported identically whether the record is missing, foreign
or revoked. The reader seams are the tested boundary: the readers themselves
are pinned in the tasks/assets lanes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ai.core.focus import contract, resolution


def _deny(user, ref_id):
    return None


@pytest.fixture(autouse=True)
def _deny_by_default(monkeypatch):
    """Every reader denies unless a test installs its own (fail closed)."""
    monkeypatch.setattr(resolution, "read_machine", _deny)
    monkeypatch.setattr(resolution, "read_work_order", _deny)
    monkeypatch.setattr(resolution, "read_kanban_card", _deny)


def test_machine_candidate_resolves_canonical_identity(monkeypatch) -> None:
    seen = {}

    def read_machine(user, machine_id):
        seen["id"] = machine_id
        return SimpleNamespace(pk=machine_id, name="Influent Pump Station No. 1")

    monkeypatch.setattr(resolution, "read_machine", read_machine)
    target = resolution.resolve_focus_candidate(
        object(), contract.FocusCandidate(kind="machine", id=3)
    )
    assert seen["id"] == 3
    assert target == contract.FocusTarget(
        kind="machine",
        id=3,
        label="Influent Pump Station No. 1",
        machine_id=3,
        work_order_id=None,
    )


def test_work_order_candidate_resolves_label_and_machine_relationship(monkeypatch) -> None:
    def read_work_order(user, work_order_id):
        return SimpleNamespace(
            pk=work_order_id, reference="WO-1042", title="Pump rebuild", machine_id=3
        )

    monkeypatch.setattr(resolution, "read_work_order", read_work_order)
    target = resolution.resolve_focus_candidate(
        object(), contract.FocusCandidate(kind="work_order", id=5)
    )
    assert target == contract.FocusTarget(
        kind="work_order", id=5, label="WO-1042", machine_id=3, work_order_id=5
    )


def test_card_candidate_resolves_distinct_parent_work_order(monkeypatch) -> None:
    def read_kanban_card(user, card_id):
        work_order = SimpleNamespace(pk=5, reference="WO-1042", machine_id=3)
        card = SimpleNamespace(pk=card_id, title="Diagnose the pump", work_order_id=work_order.pk)
        return card, work_order

    monkeypatch.setattr(resolution, "read_kanban_card", read_kanban_card)
    target = resolution.resolve_focus_candidate(
        object(), contract.FocusCandidate(kind="kanban_card", id=11)
    )
    assert target.kind == "kanban_card"
    assert target.id == 11
    assert target.work_order_id == 5
    assert target.machine_id == 3
    assert target.id != target.work_order_id


def test_missing_or_unauthorized_candidate_is_generic_rejection() -> None:
    for kind in contract.FOCUS_KINDS:
        with pytest.raises(contract.FocusRejected):
            resolution.resolve_focus_candidate(object(), contract.FocusCandidate(kind=kind, id=999))


def test_work_order_without_reference_falls_back_to_title(monkeypatch) -> None:
    def read_work_order(user, work_order_id):
        return SimpleNamespace(
            pk=work_order_id, reference=None, title="Pump rebuild", machine_id=None
        )

    monkeypatch.setattr(resolution, "read_work_order", read_work_order)
    target = resolution.resolve_focus_candidate(
        object(), contract.FocusCandidate(kind="work_order", id=5)
    )
    assert target.label == "Pump rebuild"
    assert target.machine_id is None


def test_reader_broken_relationship_never_fabricates_a_work_order(monkeypatch) -> None:
    """A card whose parent cannot be read is rejected, not rebound to a WO id."""

    def read_kanban_card(user, card_id):
        return SimpleNamespace(pk=card_id, title="Orphan"), None

    monkeypatch.setattr(resolution, "read_kanban_card", read_kanban_card)
    with pytest.raises(contract.FocusRejected):
        resolution.resolve_focus_candidate(
            object(), contract.FocusCandidate(kind="kanban_card", id=11)
        )


def test_reader_exception_is_wrapped_as_generic_rejection(monkeypatch) -> None:
    def read_machine(user, machine_id):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(resolution, "read_machine", read_machine)
    with pytest.raises(contract.FocusRejected):
        resolution.resolve_focus_candidate(object(), contract.FocusCandidate(kind="machine", id=3))


# ---- bounded candidate search: reauthorization + has_more (gap 7) ---------


def test_candidate_search_reauthorizes_and_projects_canonical_labels(monkeypatch) -> None:
    """Scope-filtered hints are re-authorized; labels come from the READER."""
    monkeypatch.setattr(resolution, "search_machines", lambda _user, _query, _limit: [3, 9])

    def read_machine(user, machine_id):
        return None if machine_id == 9 else SimpleNamespace(pk=machine_id, name="Pump A")

    monkeypatch.setattr(resolution, "read_machine", read_machine)
    page = resolution.search_focus_candidates(object(), "machine", "pump")
    assert page.returned_count == 1
    assert page.has_more is False
    assert page.candidates[0] == contract.FocusTarget(
        kind="machine", id=3, label="Pump A", machine_id=3, work_order_id=None
    )


def test_candidate_search_fetches_limit_plus_one_and_reports_has_more(monkeypatch) -> None:
    """``has_more`` comes from an actual bounded limit+1 server fetch."""
    seen: list[int] = []

    def search_machines(user, query, limit):
        seen.append(limit)
        return [3, 4, 5]

    monkeypatch.setattr(resolution, "search_machines", search_machines)
    monkeypatch.setattr(
        resolution,
        "read_machine",
        lambda _user, machine_id: SimpleNamespace(pk=machine_id, name=f"Machine {machine_id}"),
    )
    page = resolution.search_focus_candidates(object(), "machine", "pump", limit=2)
    assert seen == [3]  # limit + 1, asserted server-side
    assert page.returned_count == 2
    assert page.has_more is True


def test_candidate_search_denials_are_generic_and_bounded(monkeypatch) -> None:
    def exploding(user, query, limit):
        raise RuntimeError("db gone")

    monkeypatch.setattr(resolution, "search_machines", exploding)
    page = resolution.search_focus_candidates(object(), "machine", "pump")
    assert page.candidates == ()
    assert page.has_more is False
    # A bare numeric id with no kind NEVER resolves across kinds.
    monkeypatch.setattr(resolution, "search_machines", lambda _user, _query, _limit: [3])
    empty = resolution.search_focus_candidates(object(), None, "", numeric_id=3)
    assert empty.candidates == ()


def test_raw_searchers_are_bounded_and_search_identity_fields(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(resolution, "_machine_scope_filter", lambda _user: None)

    class _Query:
        def filter(self, *args, **kwargs):
            captured["filter_kwargs"] = kwargs
            captured["filter_args"] = args
            return self

        def order_by(self, *args, **kwargs):
            return self

        def __getitem__(self, item):
            captured["slice"] = item
            return [SimpleNamespace(pk=3), SimpleNamespace(pk=4)]

    class _Manager:
        def filter(self, *args, **kwargs):
            captured["first_filter"] = kwargs
            return _Query()

    fake_model = SimpleNamespace(objects=_Manager())
    pks = resolution.search_machines(object(), "Pump 12", 2, _model=fake_model)
    assert pks == [3, 4]
    assert captured["slice"] == slice(None, 2, None)
    # Name AND serial are searched; the exact reference text is preserved.
    assert resolution.MACHINE_MATCH_FIELDS == ("name__icontains", "serial__icontains")
    assert resolution.WORK_ORDER_MATCH_FIELDS == (
        "reference__icontains",
        "title__icontains",
    )
