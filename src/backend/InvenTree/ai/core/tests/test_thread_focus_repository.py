"""Thread conversation focus: persistence, versions, snapshots, replay.

Repository behavior on real rows (test-only file-backed SQLite — see
``ai.core.tests.focus_test_db``): owner-only writes, optimistic
``expected_focus_version``, canonical no-op reselection, unchanged state on
every error, the per-turn focus snapshot bound under the SAME row lock as the
analysis scope, and replay carrying the ORIGINAL snapshot. The authorization
readers are the tested seam elsewhere (``test_focus_resolution`` and the
tasks lane); here they permit/deny so the repository policy is exercised.
"""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest
from ai.core.focus import contract
from ai.core.focus import resolution as focus_resolution
from ai.core.tests.focus_test_db import ensure_island_tables
from aichat.services.threads import (
    ANALYSIS_SCOPE_SNAPSHOT_KEY,
    FOCUS_SNAPSHOT_KEY,
    FocusVersionConflict,
    ThreadNotFound,
    ThreadRepository,
    canonical_request_fingerprint,
)


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()
    yield


@pytest.fixture
def actor_user():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(username=f"focus-owner-{os.urandom(4).hex()}")


@pytest.fixture
def repository(actor_user):
    return ThreadRepository(actor_user.pk, "site:main")


@pytest.fixture
def thread(repository):
    thread, _ = repository.get_or_create()
    return thread


@pytest.fixture(autouse=True)
def _deny_by_default(monkeypatch):
    """Readers deny everything unless a test installs its own (fail closed).

    Never rely on the real readers' import failing in the island: a denial
    here must be a policy denial, not an accident.
    """
    monkeypatch.setattr(focus_resolution, "read_machine", lambda _user, _ref: None)
    monkeypatch.setattr(focus_resolution, "read_work_order", lambda _user, _ref: None)
    monkeypatch.setattr(focus_resolution, "read_kanban_card", lambda _user, _ref: None)


@pytest.fixture
def allow_machines(monkeypatch):
    """Authorize machine ids 1..N as rows named after their id."""

    def read_machine(user, machine_id):
        return type("M", (), {"pk": machine_id, "name": f"Machine {machine_id}"})()

    monkeypatch.setattr(focus_resolution, "read_machine", read_machine)


def test_new_thread_reads_no_focus_at_version_zero(repository, thread) -> None:
    payload = repository.get_focus(thread.pk)
    assert payload == {"focus": None, "focus_version": 0, "focus_unavailable": False}


def test_set_focus_stores_canonical_projection_and_bumps_version(
    repository, thread, allow_machines
) -> None:
    result = repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    assert result == {
        "focus": {
            "kind": "machine",
            "id": 3,
            "label": "Machine 3",
            "machine_id": 3,
            "work_order_id": None,
        },
        "focus_version": 1,
        "focus_unavailable": False,
    }
    assert repository.get_focus(thread.pk) == result


def test_identical_reselection_is_canonical_noop(repository, thread, allow_machines) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    again = repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=1)
    assert again["focus_version"] == 1
    assert repository.get_focus(thread.pk)["focus_version"] == 1


def test_stale_expected_version_conflicts_and_leaves_state_unchanged(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    with pytest.raises(FocusVersionConflict):
        repository.set_focus(thread.pk, {"kind": "machine", "id": 4}, expected_focus_version=0)
    state = repository.get_focus(thread.pk)
    assert state["focus_version"] == 1
    assert state["focus"]["id"] == 3


def test_noop_reselection_with_stale_version_still_conflicts(
    repository, thread, allow_machines
) -> None:
    """Optimistic checks apply even when the content would not change."""
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    with pytest.raises(FocusVersionConflict):
        repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)


def test_clear_focus_returns_to_none_and_clearing_empty_is_noop(
    repository, thread, allow_machines
) -> None:
    cleared = repository.set_focus(thread.pk, None, expected_focus_version=0)
    assert cleared == {"focus": None, "focus_version": 0, "focus_unavailable": False}
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    cleared = repository.set_focus(thread.pk, None, expected_focus_version=1)
    assert cleared == {"focus": None, "focus_version": 2, "focus_unavailable": False}


def test_unauthorized_candidate_is_generic_and_leaves_state_unchanged(repository, thread) -> None:
    with pytest.raises(contract.FocusRejected):
        repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    assert repository.get_focus(thread.pk) == {
        "focus": None,
        "focus_version": 0,
        "focus_unavailable": False,
    }


def test_unauthorized_rejection_on_existing_focus_preserves_previous_focus(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    with pytest.raises(contract.FocusRejected):
        repository.set_focus(thread.pk, {"kind": "work_order", "id": 9}, expected_focus_version=1)
    state = repository.get_focus(thread.pk)
    assert state["focus_version"] == 1
    assert state["focus"]["id"] == 3


def test_malformed_request_is_rejected_without_state_change(repository, thread) -> None:
    for bad in (
        {"kind": "task", "id": 3},
        {"kind": "machine", "id": 0},
        {"kind": "machine"},
        "machine",
    ):
        with pytest.raises(contract.FocusValidationError):
            repository.set_focus(thread.pk, bad, expected_focus_version=0)
    assert repository.get_focus(thread.pk)["focus_version"] == 0


def test_focus_change_never_touches_analysis_scope_or_its_version(
    repository, thread, allow_machines
) -> None:
    scope = repository.set_scope(thread.pk, {"mode": "all_authorized_assets"}, expected_version=0)
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    after = repository.get_scope(thread.pk)
    assert after["version"] == scope["version"] == 1
    assert after["hash"] == scope["hash"]


def test_stranger_cannot_read_or_write_focus(actor_user, repository, thread) -> None:
    stranger_repo = ThreadRepository(actor_user.pk + 1000, "site:main")
    with pytest.raises(ThreadNotFound):
        stranger_repo.get_focus(thread.pk)
    with pytest.raises(ThreadNotFound):
        stranger_repo.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)


# ---- turn snapshots ------------------------------------------------------


def _begin(repository, thread, **kwargs):
    defaults = {
        "content": "what is its status?",
        "modality": "text",
        "trusted_context": {"actor": "x"},
        "modality_metadata": None,
        "idempotency_key": "key-1",
        "request_fingerprint": canonical_request_fingerprint(
            content="what is its status?",
            modality="text",
            trusted_context={"actor": "x"},
        ),
        "correlation_id": "corr-1",
    }
    defaults.update(kwargs)
    return repository.begin_turn(thread.pk, **defaults)


def test_begin_turn_snapshots_focus_with_the_turn(repository, thread, allow_machines) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    result = _begin(repository, thread)
    assert result.focus_snapshot == {
        "focus": contract.focus_to_stored(
            contract.FocusTarget(
                kind="machine", id=3, label="Machine 3", machine_id=3, work_order_id=None
            )
        ),
        "version": 1,
    }
    stored = (result.turn.trusted_context or {})[FOCUS_SNAPSHOT_KEY]
    assert stored == result.focus_snapshot
    assert ANALYSIS_SCOPE_SNAPSHOT_KEY not in (result.turn.trusted_context or {})


def test_begin_turn_without_focus_keeps_trusted_context_verbatim(repository, thread) -> None:
    result = _begin(repository, thread)
    assert result.focus_snapshot is None
    assert result.turn.trusted_context == {"actor": "x"}


def test_replay_returns_original_focus_snapshot_after_later_switch(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    first = _begin(repository, thread)
    repository.set_focus(thread.pk, {"kind": "machine", "id": 4}, expected_focus_version=1)
    replay = _begin(repository, thread)
    assert replay.replayed is True
    assert replay.focus_snapshot == first.focus_snapshot
    assert replay.focus_snapshot["focus"]["id"] == 3


def test_stale_expected_focus_version_is_admitted_nothing(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    with pytest.raises(FocusVersionConflict):
        _begin(repository, thread, expected_focus_version=0)
    from aichat.models import ChatMessage, ChatTurn

    assert ChatTurn.objects.filter(thread=thread).count() == 0
    assert ChatMessage.objects.filter(thread=thread).count() == 0


def test_matching_expected_focus_version_admits_the_turn(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    result = _begin(repository, thread, expected_focus_version=1)
    assert result.replayed is False
    assert result.focus_snapshot["version"] == 1


# ---- gap 5: a cleared focus snapshots its TRUE version, never 0 -----------


def test_begin_turn_snapshots_cleared_focus_at_true_version(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    repository.set_focus(thread.pk, None, expected_focus_version=1)
    result = _begin(repository, thread)
    assert result.focus_snapshot == {"focus": None, "version": 2}


def test_replay_after_clear_retains_the_original_null_snapshot(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    repository.set_focus(thread.pk, None, expected_focus_version=1)
    first = _begin(repository, thread)
    repository.set_focus(thread.pk, {"kind": "machine", "id": 4}, expected_focus_version=2)
    replay = _begin(repository, thread)
    assert replay.replayed is True
    assert replay.focus_snapshot == first.focus_snapshot == {"focus": None, "version": 2}


# ---- gap 3: per-record re-authorization; never echo a stale label --------


def test_get_focus_never_echoes_a_stale_label_after_revoke(
    repository, thread, allow_machines, monkeypatch
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    monkeypatch.setattr(focus_resolution, "read_machine", lambda _user, _ref: None)
    payload = repository.get_focus(thread.pk)
    assert payload == {"focus": None, "focus_version": 1, "focus_unavailable": True}
    assert "Machine 3" not in repr(payload)


def test_get_focus_available_flag_is_false_for_a_live_record(
    repository, thread, allow_machines
) -> None:
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    payload = repository.get_focus(thread.pk)
    assert payload["focus_unavailable"] is False
    assert payload["focus"]["label"] == "Machine 3"


def test_shared_thread_reader_gets_only_their_own_authorized_view(
    repository, thread, allow_machines, monkeypatch
) -> None:
    """A shared read of the thread never inherits the OWNER's record grant."""
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    from django.contrib.auth import get_user_model

    reader = get_user_model().objects.create_user(username=f"focus-shared-{os.urandom(4).hex()}")
    shared_repo = ThreadRepository(reader.pk, "site:main")
    # The shared reader may READ the thread (an authorized view of the
    # thread), but their own record grant is what counts below.
    from aichat.models import ChatThread

    monkeypatch.setattr(
        ThreadRepository,
        "get_readable",
        lambda _self, _tid: (ChatThread.objects.get(pk=thread.pk), True),
    )
    seen_users: list[object] = []

    def read_machine(user, ref):
        seen_users.append(getattr(user, "pk", None))
        return None

    monkeypatch.setattr(focus_resolution, "read_machine", read_machine)
    payload = shared_repo.get_focus(thread.pk)
    assert payload == {"focus": None, "focus_version": 1, "focus_unavailable": True}
    # Re-authorization ran as the CALLER, never as the owner.
    assert seen_users == [reader.pk]


# ---- gap 4: actual changes set aside armed decisions, fail closed --------


def _decision_env(monkeypatch, coordinator):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: SimpleNamespace(feature_voice_decision_coordinator=True),
    )
    monkeypatch.setattr("ai.core.decisions.coordinator.get_coordinator", lambda: coordinator)


def test_focus_change_sets_aside_the_pending_decision_before_writing(
    repository, thread, allow_machines, monkeypatch
) -> None:
    events: list[str] = []

    class _Coordinator:
        def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
            state = repository.get_focus(thread.pk)
            events.append(f"disarm:{reason}:{set_aside}:v{state['focus_version']}")
            return None

    _decision_env(monkeypatch, _Coordinator())
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    events.clear()
    repository.set_focus(thread.pk, {"kind": "machine", "id": 4}, expected_focus_version=1)
    # The set-aside ran BEFORE the version advanced (fail closed ordering).
    assert events == ["disarm:focus_switched:True:v1"]
    assert repository.get_focus(thread.pk)["focus"]["id"] == 4


def test_clear_also_sets_aside_the_pending_decision(
    repository, thread, allow_machines, monkeypatch
) -> None:
    calls: list[tuple] = []

    class _Coordinator:
        def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
            calls.append((thread_id, reason, set_aside))
            return None

    _decision_env(monkeypatch, _Coordinator())
    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    calls.clear()
    repository.set_focus(thread.pk, None, expected_focus_version=1)
    assert calls == [(thread.pk, "focus_switched", True)]


def test_coordinator_failure_fails_the_focus_change_closed(
    repository, thread, allow_machines, monkeypatch
) -> None:
    from ai.core.focus.contract import FocusSetAsideUnavailable

    class _Coordinator:
        def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
            from ai.core.decisions.store import DecisionStoreUnavailable

            raise DecisionStoreUnavailable("store contention")

    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    _decision_env(monkeypatch, _Coordinator())
    with pytest.raises(FocusSetAsideUnavailable):
        repository.set_focus(thread.pk, {"kind": "machine", "id": 4}, expected_focus_version=1)
    with pytest.raises(FocusSetAsideUnavailable):
        repository.set_focus(thread.pk, None, expected_focus_version=1)
    state = repository.get_focus(thread.pk)
    assert state["focus"]["id"] == 3
    assert state["focus_version"] == 1


def test_canonical_noop_reselection_needs_no_set_aside(
    repository, thread, allow_machines, monkeypatch
) -> None:
    class _Coordinator:
        def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
            raise AssertionError("no-op must not touch the decision rail")

    repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    _decision_env(monkeypatch, _Coordinator())
    again = repository.set_focus(thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=1)
    assert again["focus_version"] == 1
