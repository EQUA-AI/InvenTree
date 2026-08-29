"""S5 WP-A1: the per-turn analysis-scope carrier (`scope_context`).

The contract under test: a turn binds exactly one scope context from the S1
snapshot (rebind-always, like the capture ledger), legacy/absent snapshots
bind an inert or empty context, the snapshot id is turn-stable, and the
context propagates into worker threads the way tool bodies actually run
(``asyncio.to_thread`` copies the binding context).
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.analysis.scope import (
    MODE_ALL_AUTHORIZED,
    MODE_EXPLICIT,
    SCHEMA_VERSION,
    SOURCE_CLASSES,
)
from ai.core.analysis.scope_context import (
    bind_turn_scope,
    current_turn_scope,
    resolve_scope_serials,
    turn_scope_context,
)

_FLAGS = SimpleNamespace(
    feature_ai_thread_scope_shadow=True,
    feature_ai_thread_scope_enforce=False,
)


def _snapshot(mode: str = MODE_EXPLICIT, machine_ids=(7, 3), version: int = 2) -> dict:
    scope: dict = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "machine_ids": list(machine_ids),
        "date_window": {"from": None, "to": None},
        "source_classes": list(SOURCE_CLASSES),
        "display_label": "Pump bay",
    }
    return {"scope": scope, "version": version, "hash": "a" * 64}


@pytest.fixture(autouse=True)
def _reset_context():
    token = turn_scope_context.set(None)
    yield
    turn_scope_context.reset(token)


@pytest.fixture(autouse=True)
def _settings():
    with mock.patch("ai.core.config.get_settings", return_value=_FLAGS):
        yield


class TestBind:
    def test_explicit_snapshot_binds_full_context(self) -> None:
        context = bind_turn_scope(_snapshot(), thread_pk=11, turn_pk=99)
        assert context is not None
        assert current_turn_scope() is context
        assert context.mode == MODE_EXPLICIT
        assert context.machine_ids == frozenset({3, 7})
        assert context.scope_version == 2
        assert context.scope_hash == "a" * 64
        assert context.thread_pk == 11
        assert context.explicit is True
        assert context.active is True  # shadow flag on
        assert context.shadow is True and context.enforce is False

    def test_fleet_mode_binds_inert_context(self) -> None:
        context = bind_turn_scope(
            _snapshot(mode=MODE_ALL_AUTHORIZED, machine_ids=()), thread_pk=11, turn_pk=99
        )
        assert context is not None
        assert context.mode == MODE_ALL_AUTHORIZED
        assert context.explicit is False
        assert context.active is False

    def test_absent_snapshot_binds_none(self) -> None:
        bind_turn_scope(_snapshot(), thread_pk=1, turn_pk=1)
        assert bind_turn_scope(None, thread_pk=1, turn_pk=2) is None
        assert current_turn_scope() is None

    def test_untyped_legacy_snapshot_binds_none(self) -> None:
        assert (
            bind_turn_scope({"scope": {}, "version": 0, "hash": ""}, thread_pk=1, turn_pk=1) is None
        )

    def test_malformed_stored_scope_degrades_to_legacy_inert(self) -> None:
        snapshot = {"scope": {"schema_version": 99, "mode": "??"}, "version": 3, "hash": "b" * 64}
        context = bind_turn_scope(snapshot, thread_pk=1, turn_pk=1)
        # A hash/version exists, so the context is bound for telemetry — but
        # the malformed payload fails closed to legacy and never activates.
        assert context is not None
        assert context.explicit is False
        assert context.active is False

    def test_rebind_replaces_previous_turn(self) -> None:
        first = bind_turn_scope(_snapshot(version=1), thread_pk=1, turn_pk=1)
        second = bind_turn_scope(_snapshot(version=2), thread_pk=1, turn_pk=2)
        assert current_turn_scope() is second
        assert first is not second


class TestSnapshotId:
    def test_stable_within_a_turn_distinct_across_turns(self) -> None:
        one = bind_turn_scope(_snapshot(), thread_pk=1, turn_pk=41)
        again = bind_turn_scope(_snapshot(), thread_pk=1, turn_pk=41)
        other_turn = bind_turn_scope(_snapshot(), thread_pk=1, turn_pk=42)
        assert one.snapshot_id == again.snapshot_id
        assert one.snapshot_id != other_turn.snapshot_id
        assert one.snapshot_id.startswith("snap_")

    def test_changes_with_scope_version(self) -> None:
        v2 = bind_turn_scope(_snapshot(version=2), thread_pk=1, turn_pk=41)
        v3 = bind_turn_scope(_snapshot(version=3), thread_pk=1, turn_pk=41)
        assert v2.snapshot_id != v3.snapshot_id


class TestPropagation:
    def test_context_reaches_to_thread_workers(self) -> None:
        """Tool bodies run via asyncio.to_thread; the binding must follow."""

        async def scenario() -> str | None:
            bind_turn_scope(_snapshot(), thread_pk=5, turn_pk=6)
            observed = await asyncio.to_thread(lambda: current_turn_scope())
            return observed.snapshot_id if observed else None

        inner = asyncio.run(scenario())
        assert inner is not None

    def test_enforce_flag_is_read_at_bind_time(self) -> None:
        enforced = SimpleNamespace(
            feature_ai_thread_scope_shadow=False,
            feature_ai_thread_scope_enforce=True,
        )
        with mock.patch("ai.core.config.get_settings", return_value=enforced):
            context = bind_turn_scope(_snapshot(), thread_pk=1, turn_pk=1)
        assert context.enforce is True
        assert context.shadow is False
        assert context.active is True


class TestSerialResolution:
    def test_serials_default_empty_and_failures_stay_empty(self) -> None:
        context = bind_turn_scope(_snapshot(), thread_pk=1, turn_pk=1)
        assert context.machine_serials == frozenset()
        # Resolution failure (no authorized machines / no assets app in the
        # island) narrows to empty rather than raising.
        assert resolve_scope_serials(SimpleNamespace(), [1, 2]) == frozenset()

    def test_serials_carried_when_supplied(self) -> None:
        context = bind_turn_scope(
            _snapshot(), thread_pk=1, turn_pk=1, serials=frozenset({"SR-100"})
        )
        assert context.machine_serials == frozenset({"SR-100"})


class TestScopeMiss:
    def _bind(self, *, enforce: bool) -> None:
        flags = SimpleNamespace(
            feature_ai_thread_scope_shadow=not enforce,
            feature_ai_thread_scope_enforce=enforce,
        )
        with mock.patch("ai.core.config.get_settings", return_value=flags):
            bind_turn_scope(_snapshot(machine_ids=(3, 7)), thread_pk=2, turn_pk=9)

    def test_enforce_returns_a_recoverable_typed_miss(self) -> None:
        from ai.core.analysis.scope_context import scope_miss_for_machine

        self._bind(enforce=True)
        miss = scope_miss_for_machine(99)
        assert miss is not None
        assert miss["scope_miss"] is True
        assert miss["code"] == "out_of_analysis_scope"
        assert miss["scope_label"] == "Pump bay"
        assert "offer to change the scope" in miss["message"]

    def test_in_scope_and_machineless_records_pass(self) -> None:
        from ai.core.analysis.scope_context import scope_miss_for_machine

        self._bind(enforce=True)
        assert scope_miss_for_machine(3) is None
        assert scope_miss_for_machine(None) is None

    def test_shadow_lets_the_record_through(self) -> None:
        from ai.core.analysis.scope_context import scope_miss_for_machine

        self._bind(enforce=False)
        assert scope_miss_for_machine(99) is None

    def test_unscoped_turn_never_misses(self) -> None:
        from ai.core.analysis.scope_context import scope_miss_for_machine

        turn_scope_context.set(None)
        assert scope_miss_for_machine(99) is None


def test_legacy_workflow_binds_the_scope_for_every_modality() -> None:
    """Introspection pin: ONE shared helper binds ledger + scope (S10).

    Both text and voice turns execute _run_legacy_workflow, and the analysis
    branch shares the same helper — so neither branch can drift a binding.
    """
    import inspect

    from ai.core.turn import execution

    helper = inspect.getsource(execution._bind_turn_capture_and_scope)
    ledger_at = helper.index("bind_tool_captures()")
    scope_at = helper.index("bind_turn_scope(")
    assert ledger_at < scope_at, "scope binds beside (after) the capture ledger"
    assert "run.analysis_scope" in helper

    legacy = inspect.getsource(execution._run_legacy_workflow)
    assert "_bind_turn_capture_and_scope" in legacy
    assert "run.retrieval_snapshot" in legacy
    analysis = inspect.getsource(execution._run_analysis_branch)
    assert "_bind_turn_capture_and_scope" in analysis
