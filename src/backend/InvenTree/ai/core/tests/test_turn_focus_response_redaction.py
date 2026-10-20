"""RV02-A: current-actor reauthorization of the recorded turn-focus projection.

The normalized turn result (and its ``aimms.threadFocus`` echo) projects the
turn's RECORDED focus snapshot on the complete, replayed and incomplete paths.
RV02-A: before that projection leaves the service, the recorded target is
re-authorized as the CURRENT actor through the existing Django user
rehydration plus ``ai.core.focus.resolution.resolve_focus_candidate`` (the
real ``assets``/``tasks`` authorized readers). A recorded target the actor can
no longer read — grant revoked after intake, missing/deleted/out-of-scope
record, or an actor that cannot be rehydrated — must project ``focus: None``
while the ORIGINAL recorded ``focus_version`` is retained verbatim, the stored
snapshot JSON is never rewritten, and a resolution failure never fails an
already-persisted turn. A readable target keeps its recorded kind/id/label/
relationships/version exactly — never the thread's later current focus.

Real ORM rows, the real ``ThreadRepository``/intake/``_process_turn`` path and
the real authorized readers throughout (no resolver or reader mocking).
Providerless disclosure: no provider channel is installed; the workflow is a
local generator standing in for the provider, exactly as the existing
normalized-turn service tests disclose.

Scope: turn-result focus projection ONLY. History/question/replay-metadata
projection is a later tranche and is untouched here.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.focus import contract as focus_contract  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType, InMemoryEventEmitter  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn_service import (  # noqa: E402
    NormalizedTurnResult,
    NormalizedTurnService,
    TurnIncomplete,
)
from aichat.models import TurnState  # noqa: E402
from django.test import override_settings  # noqa: E402

FOCUS_VERSION = 7
TURN_CONTENT = "Describe the pump readings for today."

KINDS = ("machine", "work_order", "kanban_card")


@pytest.fixture(scope="module", autouse=True)
def _database():
    from ai.core.tests.focus_test_db import ensure_island_tables

    ensure_island_tables()
    yield


# --------------------------------------------------------------------------- #
# Real ORM fixtures (independently computed expectations; the outward payload
# is never an oracle).
# --------------------------------------------------------------------------- #


def _user():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username=f"rv02a-{uuid.uuid4().hex[:8]}", password="unused"
    )


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f"user:{user.pk}",
        actor=f"user:{user.pk}",
        user_pk=str(user.pk),
        username=user.username,
        authentication_method="django_session",
        scope="site:main",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _ghost_principal() -> AIPrincipal:
    """A real principal whose Django user row genuinely does not exist."""
    return AIPrincipal(
        subject="user:987654321",
        actor="user:987654321",
        user_pk="987654321",
        username="rv02a-ghost",
        authentication_method="django_session",
        scope="site:main",
        policy_version="1",
        is_staff=False,
        is_superuser=False,
    )


def _context(principal, correlation_id: str) -> TrustedTurnContext:
    return TrustedTurnContext(
        actor=principal.actor,
        server_policy_key="site:main",
        server_policy_hash="0" * 64,
        thread_namespace="unscoped",
        server_route_hints=("/chat",),
        allowed_capabilities=("chat.unscoped.read",),
        correlation_id=correlation_id,
        policy_version="1",
        untrusted_content="{}",
    )


def _machine_with_client(name: str):
    from assets.models import AssetMachine

    client_model = AssetMachine._meta.get_field("client").related_model
    client = client_model.objects.create(
        name=f"rv02a-client-{uuid.uuid4().hex[:8]}",
        code=f"rv02a-{uuid.uuid4().hex[:10]}",
    )
    machine = AssetMachine.objects.create(name=name, client=client)
    return machine, client


def _work_order_with_card(machine):
    from tasks.models import KanbanCard, WorkOrder

    base = (
        max(
            WorkOrder.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
            KanbanCard.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
            machine.pk,
            machine.client_id,
        )
        + 1000
    )
    reference = f"RV02A-WO-{uuid.uuid4().hex[:12]}"
    wo = WorkOrder.objects.create(
        pk=base + 1,
        title="Seal replacement on pump line 7",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=machine,
        reference=reference,
    )
    card = KanbanCard.objects.create(
        pk=base + 100,
        work_order=wo,
        title="Diagnose the seal leak",
        status="backlog",
    )
    assert wo.pk != card.pk
    return wo, card


def _recorded_focus(kind: str, machine, wo, card):
    """Canonical stored snapshot + independently computed expected payload.

    Labels/relationships come from the model rows themselves (machine name,
    work-order reference, card title), never from any outward projection.
    """
    if kind == "machine":
        label, machine_id, work_order_id, target_id = (
            machine.name,
            machine.pk,
            None,
            machine.pk,
        )
    elif kind == "work_order":
        label, machine_id, work_order_id, target_id = (
            wo.reference,
            wo.machine_id,
            wo.pk,
            wo.pk,
        )
    else:
        label, machine_id, work_order_id, target_id = (
            card.title,
            wo.machine_id,
            wo.pk,
            card.pk,
        )
    target = focus_contract.FocusTarget(
        kind=kind,
        id=target_id,
        label=label,
        machine_id=machine_id,
        work_order_id=work_order_id,
    )
    expected = {
        "kind": kind,
        "id": target_id,
        "label": label,
        "machine_id": machine_id,
        "work_order_id": work_order_id,
    }
    return focus_contract.focus_to_stored(target), expected


def _repository_for(user):
    from aichat.models import ThreadNamespace
    from aichat.services import ThreadRepository

    return ThreadRepository(
        actor=str(user.pk),
        scope_key="site:main",
        namespace=ThreadNamespace.UNSCOPED,
    )


def _thread_with_focus(user, stored: dict):
    """One real thread whose recorded focus is preset exactly as the focus PUT
    would persist it (through the real repository boundary)."""
    thread, _created = _repository_for(user).get_or_create(None, title="RV02A thread")
    thread.conversation_focus = stored
    thread.focus_version = FOCUS_VERSION
    thread.save(update_fields=["conversation_focus", "focus_version"])
    return thread


def _assert_stored_unchanged(thread, stored: dict, focus_version: int):
    """Thread focus AND the recorded per-turn snapshot stay byte-verbatim.

    ``stored is None`` covers cleared/absent focus (whose recorded snapshot
    carries ``{'focus': None, 'version': N}`` whenever N > 0).
    """
    from aichat.models import ChatThread, ChatTurn

    row = (
        ChatThread.objects
        .filter(pk=thread.pk)
        .values_list("conversation_focus", "focus_version")
        .first()
    )
    assert row is not None
    assert row[0] == (stored or {}), "stored conversation_focus was mutated"
    assert row[1] == focus_version, "focus_version was mutated"
    recorded_context = (
        ChatTurn.objects
        .filter(thread=thread)
        .order_by("created_at")
        .values_list("trusted_context", flat=True)
        .first()
    )
    snapshot = (recorded_context or {}).get("conversation_focus_snapshot")
    if not stored and focus_version == 0:
        assert snapshot is None, "legacy no-focus turn grew a recorded snapshot"
    elif not stored:
        assert snapshot == {"focus": None, "version": focus_version}
    else:
        assert snapshot == {"focus": stored, "version": focus_version}, (
            "recorded per-turn focus snapshot was rewritten"
        )


def _assert_recorded_snapshot_unchanged(thread, stored: dict):
    """Recorded per-turn snapshot stays verbatim (replay tests where the
    thread's CURRENT focus legitimately changed to the switch target)."""
    from aichat.models import ChatTurn

    recorded_context = (
        ChatTurn.objects
        .filter(thread=thread)
        .order_by("created_at")
        .values_list("trusted_context", flat=True)
        .first()
    )
    snapshot = (recorded_context or {}).get("conversation_focus_snapshot")
    assert snapshot == {"focus": stored, "version": FOCUS_VERSION}, (
        "recorded per-turn focus snapshot was rewritten"
    )


# --------------------------------------------------------------------------- #
# Providerless turn harness (disclosed: local generator, no provider channel).
# --------------------------------------------------------------------------- #


class _ProviderlessWorkflow:
    """Local generator standing in for the provider (no channel installed)."""

    def __init__(self, *, mid_turn=None, incomplete: bool = False):
        self.calls: list[dict] = []
        self.mid_turn = mid_turn
        self.incomplete = incomplete

    async def run_stream(self, **kwargs):
        self.calls.append(kwargs)
        emitter = kwargs["emitter"]
        thread_id = kwargs["thread_id"]
        await emitter.emit(
            AGUIEvent(event_type=EventType.RUN_STARTED, thread_id=thread_id, run_id="run-rv02a")
        )
        await emitter.emit(
            AGUIEvent(
                event_type=EventType.WORKFLOW_STARTED,
                data={"workflow_id": "wf1"},
                thread_id=thread_id,
                run_id="run-rv02a",
            )
        )
        yield "Normalized "
        if self.mid_turn is not None:
            self.mid_turn()
        if self.incomplete:
            raise TurnIncomplete("bounded diagnostic review ended")
        yield "response"
        await emitter.emit(
            AGUIEvent(event_type=EventType.RUN_FINISHED, thread_id=thread_id, run_id="run-rv02a")
        )


class _Capture:
    def __init__(self):
        self.events: list[AGUIEvent] = []

    async def handle(self, event: AGUIEvent) -> None:
        self.events.append(event)


def _run_turn(principal, thread_id, workflow, *, key: str, correlation_id: str):
    """One real ``process()`` turn through intake/repository/projection."""

    async def run():
        service = NormalizedTurnService(workflow_factory=lambda: workflow)
        emitter = InMemoryEventEmitter()
        capture = _Capture()
        unsubscribe = await emitter.subscribe(capture)
        try:
            result = await service.process(
                actor=principal,
                thread_id=thread_id,
                content=TURN_CONTENT,
                modality="text",
                trusted_context=_context(principal, correlation_id),
                modality_metadata={},
                idempotency_key=key,
                correlation_id=correlation_id,
                emitter=emitter,
            )
        finally:
            unsubscribe()
        return result, capture

    return asyncio.run(run())


def _echo_values(capture: _Capture) -> list:
    values = []
    for event in capture.events:
        data = getattr(event, "data", None) or {}
        if event.event_type == EventType.CUSTOM and data.get("name") == "aimms.threadFocus":
            values.append(data.get("value"))
    return values


def _grant_settings(grants):
    return override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: grants.get(actor.username, []),
    )


def _fixtures(kind: str):
    user = _user()
    machine, client = _machine_with_client(f"RV02A Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    stored, expected = _recorded_focus(kind, machine, wo, card)
    thread = _thread_with_focus(user, stored)
    return user, machine, client, wo, card, thread, stored, expected


# --------------------------------------------------------------------------- #
# RV02-A core: complete-path projection re-authorizes the recorded target.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"focus_kind_{k}")
def test_complete_withholds_recorded_label_when_grant_revoked_mid_turn(kind):
    """The genuine allowed->revoked case: the actor's grant exists at intake
    and is revoked while the providerless workflow yields, with the target
    rows still extant. The result and its echo must withhold the recorded
    label (``focus is None``) and retain the ORIGINAL recorded version; the
    stored snapshot and the persisted turn stay untouched."""
    user, _machine, client, _wo, _card, thread, stored, expected = _fixtures(kind)
    grants = {user.username: [{"client_id": client.pk}]}
    workflow = _ProviderlessWorkflow(mid_turn=lambda: grants.__setitem__(user.username, []))
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.focus is None, "recorded focus label leaked after revocation"
    assert result.focus_version == FOCUS_VERSION
    assert expected["label"] not in result.message
    assert expected["label"] not in json.dumps(result.canonical_response or {})
    assert expected["label"] not in json.dumps(result.route or {})
    assert result.response_state == "complete"
    assert result.message == "Normalized response"
    assert len(workflow.calls) == 1
    echoes = _echo_values(capture)
    assert echoes == [
        {
            "focus": None,
            "focus_version": FOCUS_VERSION,
            "focus_refresh_required": False,
        }
    ], "focus echo must withhold the label and keep the recorded version"
    assert expected["label"] not in json.dumps([e.to_sse() for e in capture.events])
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"focus_kind_{k}")
def test_complete_projects_recorded_identity_and_version_when_authorized(kind):
    """Positive control: a currently-authorized recorded target keeps its
    recorded kind/id/label/relationships/version exactly (independently
    computed from the model rows), and stored state is untouched."""
    user, _machine, client, _wo, _card, thread, stored, expected = _fixtures(kind)
    grants = {user.username: [{"client_id": client.pk}]}
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.focus == expected, "recorded focus identity was not preserved"
    assert result.focus_version == FOCUS_VERSION
    assert result.response_state == "complete"
    assert result.message == "Normalized response"
    assert len(workflow.calls) == 1
    echoes = _echo_values(capture)
    assert echoes == [
        {
            "focus": expected,
            "focus_version": FOCUS_VERSION,
            "focus_refresh_required": False,
        }
    ]
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


# --------------------------------------------------------------------------- #
# Replay: the ORIGINAL recorded binding, never the thread's later current
# focus; re-authorized as the CURRENT actor at replay time.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"focus_kind_{k}")
def test_replay_projects_original_recorded_focus_after_current_focus_switch(kind):
    """After a real focus switch through the repository seam, an exact retry
    still projects the ORIGINAL recorded binding and version (independently
    computed), and the switched current focus is untouched."""
    user, machine, client, wo, card, thread, stored, expected = _fixtures(kind)
    switch_kind = "kanban_card" if kind != "kanban_card" else "machine"
    _switch_stored, switch_expected = _recorded_focus(switch_kind, machine, wo, card)
    grants = {user.username: [{"client_id": client.pk}]}
    key = f"rv02a-{uuid.uuid4().hex}"
    correlation_id = str(uuid.uuid4())
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        first, _first_capture = _run_turn(
            _principal(user), thread.pk, workflow, key=key, correlation_id=correlation_id
        )
        switched = _repository_for(user).set_focus(
            thread.pk,
            {"kind": switch_expected["kind"], "id": switch_expected["id"]},
            expected_focus_version=FOCUS_VERSION,
        )
        replay, capture = _run_turn(
            _principal(user), thread.pk, workflow, key=key, correlation_id=correlation_id
        )

    assert first.focus == expected
    assert replay.replayed is True
    assert len(workflow.calls) == 1, "a replay must not execute the workflow again"
    assert (switched["focus"]["kind"], switched["focus"]["id"]) != (
        expected["kind"],
        expected["id"],
    ), "focus switch did not actually change the current focus"
    assert switched["focus_version"] == FOCUS_VERSION + 1
    assert replay.focus == expected, "replay replaced the recorded binding"
    assert replay.focus_version == FOCUS_VERSION
    echoes = _echo_values(capture)
    assert echoes == [
        {
            "focus": expected,
            "focus_version": FOCUS_VERSION,
            "focus_refresh_required": False,
        }
    ]
    _assert_recorded_snapshot_unchanged(thread, stored)
    from aichat.models import ChatThread

    current = (
        ChatThread.objects
        .filter(pk=thread.pk)
        .values_list("conversation_focus", "focus_version")
        .first()
    )
    assert current[1] == FOCUS_VERSION + 1, "switched current focus version was rewritten"
    assert current[0]["kind"] == switch_kind, "switched current focus was rewritten"


def test_replay_withholds_recorded_label_after_revocation_and_focus_switch():
    """Same replay shape, but the recorded target's grant is revoked after the
    switch: the label is withheld, the ORIGINAL recorded version retained."""
    user, machine, client, wo, card, thread, stored, expected = _fixtures("machine")
    switch_kind = "kanban_card"
    _switch_stored, switch_expected = _recorded_focus(switch_kind, machine, wo, card)
    grants = {user.username: [{"client_id": client.pk}]}
    key = f"rv02a-{uuid.uuid4().hex}"
    correlation_id = str(uuid.uuid4())
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        first, _first_capture = _run_turn(
            _principal(user), thread.pk, workflow, key=key, correlation_id=correlation_id
        )
        _repository_for(user).set_focus(
            thread.pk,
            {"kind": switch_expected["kind"], "id": switch_expected["id"]},
            expected_focus_version=FOCUS_VERSION,
        )
        grants[user.username] = []
        replay, capture = _run_turn(
            _principal(user), thread.pk, workflow, key=key, correlation_id=correlation_id
        )

    assert first.focus == expected
    assert replay.replayed is True
    assert replay.focus is None, "recorded focus label leaked on replay after revocation"
    assert replay.focus_version == FOCUS_VERSION
    assert expected["label"] not in json.dumps([e.to_sse() for e in capture.events])
    assert _echo_values(capture) == [
        {
            "focus": None,
            "focus_version": FOCUS_VERSION,
            "focus_refresh_required": False,
        }
    ]
    _assert_recorded_snapshot_unchanged(thread, stored)


# --------------------------------------------------------------------------- #
# Incomplete path: label withheld, ORIGINAL version retained, and the existing
# no-echo behavior on the incomplete path is preserved.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"focus_kind_{k}")
def test_incomplete_withholds_recorded_label_and_emits_no_focus_echo(kind):
    user, _machine, client, _wo, _card, thread, stored, expected = _fixtures(kind)
    grants = {user.username: [{"client_id": client.pk}]}
    workflow = _ProviderlessWorkflow(
        mid_turn=lambda: grants.__setitem__(user.username, []),
        incomplete=True,
    )
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.response_state == TurnState.INCOMPLETE
    assert result.focus is None, "recorded focus label leaked on the incomplete path"
    assert result.focus_version == FOCUS_VERSION
    assert expected["label"] not in json.dumps([e.to_sse() for e in capture.events])
    assert _echo_values(capture) == [], (
        "the incomplete path must keep its existing no-echo behavior"
    )
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


# --------------------------------------------------------------------------- #
# Legacy absent/cleared focus, missing and out-of-scope targets, and the
# un-rehydratable-actor fail-closed seam.
# --------------------------------------------------------------------------- #


def test_legacy_absent_focus_keeps_null_and_zero_version():
    user = _user()
    _machine, client = _machine_with_client(f"RV02A Pump {uuid.uuid4().hex[:8]}")
    thread, _created = _repository_for(user).get_or_create(None, title="RV02A legacy")
    grants = {user.username: [{"client_id": client.pk}]}
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert result.focus is None
    assert result.focus_version == 0
    assert result.message == "Normalized response"
    assert _echo_values(capture) == [
        {"focus": None, "focus_version": 0, "focus_refresh_required": False}
    ]
    _assert_stored_unchanged(thread, None, 0)


def test_cleared_focus_keeps_null_and_recorded_version():
    user = _user()
    _machine, client = _machine_with_client(f"RV02A Pump {uuid.uuid4().hex[:8]}")
    thread, _created = _repository_for(user).get_or_create(None, title="RV02A cleared")
    thread.conversation_focus = {}
    thread.focus_version = 5
    thread.save(update_fields=["conversation_focus", "focus_version"])
    grants = {user.username: [{"client_id": client.pk}]}
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        result, _capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert result.focus is None
    assert result.focus_version == 5, "cleared-focus version was not retained"
    _assert_stored_unchanged(thread, None, 5)


def test_missing_recorded_target_withholds_label_retaining_version():
    """A recorded target whose record does not exist (missing/deleted): the
    label is withheld and the recorded version retained."""
    user = _user()
    ghost_label = f"RV02A GHOST {uuid.uuid4().hex[:8]}"
    stored = focus_contract.focus_to_stored(
        focus_contract.FocusTarget(
            kind="machine",
            id=987654,
            label=ghost_label,
            machine_id=987654,
            work_order_id=None,
        )
    )
    thread = _thread_with_focus(user, stored)
    grants = {user.username: [{"client_id": 1}]}
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert result.focus is None, "missing recorded target leaked its label"
    assert result.focus_version == FOCUS_VERSION
    assert ghost_label not in json.dumps([e.to_sse() for e in capture.events])
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


def test_out_of_scope_recorded_target_withholds_label_retaining_version():
    """A real record the current actor may not read (grant covers another
    client only): label withheld, recorded version retained."""
    user = _user()
    machine, _client = _machine_with_client(f"RV02A Pump {uuid.uuid4().hex[:8]}")
    other_client = _machine_with_client(f"RV02A Other {uuid.uuid4().hex[:8]}")[1]
    stored, expected = _recorded_focus("machine", machine, None, None)
    thread = _thread_with_focus(user, stored)
    grants = {user.username: [{"client_id": other_client.pk}]}
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert result.focus is None, "out-of-scope recorded target leaked its label"
    assert result.focus_version == FOCUS_VERSION
    assert expected["label"] not in json.dumps([e.to_sse() for e in capture.events])
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


def test_projection_fails_closed_when_actor_cannot_be_rehydrated():
    """Seam-level control (disclosed): the real repository refuses to begin a
    turn for a nonexistent actor (``_lock_actor_for_write``), so the
    deleted-actor projection branch is exercised directly at the production
    seam with a real absent user row — no resolver/reader mocking. A readable
    actor keeps the recorded projection verbatim at the same seam."""
    helper = getattr(NormalizedTurnService, "_reauthorize_result_focus", None)
    assert helper is not None, "RV02-A projection seam is missing"
    user, _machine, client, _wo, _card, _thread, _stored, expected = _fixtures("machine")
    base = NormalizedTurnResult(
        thread_id="thread-rv02a-seam",
        turn_id="turn-rv02a-seam",
        message="Normalized response",
        focus=expected,
        focus_version=FOCUS_VERSION,
    )
    service = NormalizedTurnService(workflow_factory=lambda: None)
    grants = {user.username: [{"client_id": client.pk}]}

    ghosted = asyncio.run(helper(service, _ghost_principal(), base))
    assert ghosted.focus is None, "un-rehydratable actor leaked the recorded label"
    assert ghosted.focus_version == FOCUS_VERSION
    assert ghosted.message == "Normalized response", "turn result fields were mutated"

    with _grant_settings(grants):
        kept = asyncio.run(helper(service, _principal(user), base))
    assert kept.focus == expected, "authorized projection was not kept verbatim"
    assert kept.focus_version == FOCUS_VERSION


# --------------------------------------------------------------------------- #
# Tranche12 RV02-A bounded additions: allowed->revoked replay for the
# work-order and kanban-card recorded targets (the original machine control
# above is preserved verbatim and extended, not replaced), authorized
# incomplete-return positives for all three recorded kinds, and one thrown
# resolver/bridge fault control at the final projection (a raised generic
# exception only — never a faked authorization verdict).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["work_order", "kanban_card"], ids=lambda k: f"focus_kind_{k}")
def test_replay_withholds_label_after_revocation_and_switch_for_wo_and_card_kinds(
    kind,
):
    """Allowed->revoked replay with a real repository focus switch for the
    work-order and kanban-card recorded targets (the machine control stays in
    ``test_replay_withholds_recorded_label_after_revocation_and_focus_switch``).

    The actor's real grant is present at intake and through the positively
    projected first turn, the thread's current focus genuinely switches, then
    ONLY the actor's grant changes before the exact retry. The replay must
    withhold the recorded label (``focus is None``), retain the ORIGINAL
    recorded version/binding (never the switched focus), and leave target
    rows/owners, the recorded snapshot and the switched current focus
    untouched."""
    user, machine, client, wo, card, thread, stored, expected = _fixtures(kind)
    assert wo.pk != card.pk, "recorded card PK must differ from its parent WO PK"
    switch_kind = "machine" if kind == "kanban_card" else "kanban_card"
    _switch_stored, switch_expected = _recorded_focus(switch_kind, machine, wo, card)
    grants = {user.username: [{"client_id": client.pk}]}
    key = f"rv02a-{uuid.uuid4().hex}"
    correlation_id = str(uuid.uuid4())
    workflow = _ProviderlessWorkflow()
    with _grant_settings(grants):
        first, _first_capture = _run_turn(
            _principal(user), thread.pk, workflow, key=key, correlation_id=correlation_id
        )
        switched = _repository_for(user).set_focus(
            thread.pk,
            {"kind": switch_expected["kind"], "id": switch_expected["id"]},
            expected_focus_version=FOCUS_VERSION,
        )
        grants[user.username] = []
        replay, capture = _run_turn(
            _principal(user), thread.pk, workflow, key=key, correlation_id=correlation_id
        )

    # Pre-revocation readable/native canonical positive control.
    assert first.focus == expected, "pre-revocation projection was not the recorded target"
    assert first.focus_version == FOCUS_VERSION
    assert replay.replayed is True
    assert len(workflow.calls) == 1, "a replay must not execute the workflow again"
    assert (switched["focus"]["kind"], switched["focus"]["id"]) != (
        expected["kind"],
        expected["id"],
    ), "focus switch did not actually change the current focus"
    assert switched["focus_version"] == FOCUS_VERSION + 1
    assert replay.focus is None, "recorded focus label leaked on replay after revocation"
    assert replay.focus_version == FOCUS_VERSION
    assert expected["label"] not in replay.message
    assert expected["label"] not in json.dumps(replay.canonical_response or {})
    assert expected["label"] not in json.dumps(replay.route or {})
    assert expected["label"] not in json.dumps([e.to_sse() for e in capture.events])
    assert _echo_values(capture) == [
        {
            "focus": None,
            "focus_version": FOCUS_VERSION,
            "focus_refresh_required": False,
        }
    ], "focus echo must withhold the label and keep the recorded version"
    _assert_recorded_snapshot_unchanged(thread, stored)

    from aichat.models import ChatThread
    from tasks.models import KanbanCard, WorkOrder

    current = (
        ChatThread.objects
        .filter(pk=thread.pk)
        .values_list("conversation_focus", "focus_version")
        .first()
    )
    assert current[1] == FOCUS_VERSION + 1, "switched current focus version was rewritten"
    assert current[0]["kind"] == switch_kind, "switched current focus was rewritten"
    assert WorkOrder.objects.filter(pk=wo.pk, reference=wo.reference, machine=machine).exists(), (
        "work-order row/owner was mutated"
    )
    assert KanbanCard.objects.filter(pk=card.pk, work_order=wo, title=card.title).exists(), (
        "kanban-card row/owner was mutated"
    )


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"focus_kind_{k}")
def test_incomplete_projects_recorded_identity_and_version_when_authorized(kind):
    """Authorized incomplete-return positive control (complementing the
    revocation negative above) through the real ``process()``/``_process_turn``
    providerless workflow seam for all three recorded kinds: the recorded
    canonical kind/id/label/relationships/version are preserved, stored state
    is untouched, and the existing no-focus-echo incomplete behavior holds."""
    user, _machine, client, _wo, _card, thread, stored, expected = _fixtures(kind)
    grants = {user.username: [{"client_id": client.pk}]}
    workflow = _ProviderlessWorkflow(incomplete=True)
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )

    assert result.response_state == TurnState.INCOMPLETE
    assert result.focus == expected, "incomplete path dropped the recorded identity"
    assert result.focus_version == FOCUS_VERSION
    assert len(workflow.calls) == 1
    assert _echo_values(capture) == [], (
        "the incomplete path must keep its existing no-echo behavior"
    )
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


def test_projection_fails_closed_on_thrown_resolver_bridge_fault(monkeypatch):
    """Bounded thrown-fault control at the final projection: after a genuine
    allowed intake/workflow (positively projected first), a generic exception
    is thrown from the resolver/bridge boundary mid-turn (fault injection of a
    raised exception ONLY — never a faked authorization verdict). The
    projection fails closed (``focus is None``) with the ORIGINAL recorded
    version while the already-persisted turn is neither failed nor replayed
    and its result text/state/storage match the fault-free control turn."""
    from dataclasses import asdict

    from ai.core.focus import resolution as focus_resolution

    user, _machine, client, _wo, _card, thread, stored, expected = _fixtures("work_order")
    grants = {user.username: [{"client_id": client.pk}]}

    projection_frames = {}
    project = getattr(NormalizedTurnService, "_reauthorize_result_focus", None)
    if project is not None:

        async def _observe_projection(self, actor, result):
            before_fields = asdict(result)
            projected = await project(self, actor, result)
            projection_frames[result.turn_id] = (before_fields, asdict(projected))
            return projected

        monkeypatch.setattr(NormalizedTurnService, "_reauthorize_result_focus", _observe_projection)

    def _inject_thrown_fault():
        def _boom(_user, _candidate):
            raise RuntimeError("injected RV02-A resolver bridge fault")

        monkeypatch.setattr(focus_resolution, "resolve_focus_candidate", _boom)

    ok_workflow = _ProviderlessWorkflow()
    fault_workflow = _ProviderlessWorkflow(mid_turn=_inject_thrown_fault)
    with _grant_settings(grants):
        ok, _ok_capture = _run_turn(
            _principal(user),
            thread.pk,
            ok_workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
        faulted, fault_capture = _run_turn(
            _principal(user),
            thread.pk,
            fault_workflow,
            key=f"rv02a-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )

    assert ok.focus == expected, "the allowed control turn was not positively projected"
    assert faulted.focus is None, "thrown resolver fault disclosed the recorded label"
    assert faulted.focus_version == FOCUS_VERSION
    assert faulted.replayed is False, "a projection fault must not mark the turn replayed"
    assert faulted.response_state == ok.response_state == "complete"
    assert faulted.message == ok.message, "projection fault mutated the result text"
    assert set(projection_frames) == {ok.turn_id, faulted.turn_id}
    before_fields, after_fields = projection_frames[faulted.turn_id]
    assert before_fields["focus"] == expected
    assert after_fields["focus"] is None
    assert before_fields.keys() == after_fields.keys()
    assert {k: v for k, v in before_fields.items() if k != "focus"} == {
        k: v for k, v in after_fields.items() if k != "focus"
    }, "projection fault mutated a declared result field other than focus"
    assert expected["label"] not in json.dumps([e.to_sse() for e in fault_capture.events])
    assert _echo_values(fault_capture) == [
        {
            "focus": None,
            "focus_version": FOCUS_VERSION,
            "focus_refresh_required": False,
        }
    ]

    from aichat.models import ChatTurn

    rows = list(
        ChatTurn.objects
        .filter(thread=thread)
        .order_by("created_at")
        .values_list("trusted_context", flat=True)
    )
    assert len(rows) == 2, "the projected turns were not both durably persisted"
    for recorded_context in rows:
        snapshot = (recorded_context or {}).get("conversation_focus_snapshot")
        assert snapshot == {"focus": stored, "version": FOCUS_VERSION}, (
            "stored per-turn focus snapshot was rewritten by the projection"
        )
