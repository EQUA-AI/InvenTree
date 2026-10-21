"""RV01: voice-session create/status/end never echo a stored focus label
that the CALLING principal cannot currently read.

The stored ``conversation_focus`` projection carries a label captured at
confirmation time. Session ownership and target read authorization are
separate: the route payload must re-authorize the stored candidate against
the existing current-actor readers (``ai.core.focus.resolution`` -> the
``assets``/``tasks`` authorized readers) at payload time, exactly like
``aichat.services.threads.ChatThreadService.get_focus``. A revoked, deleted
or out-of-scope target withholds the label entirely (``focus`` is null) on
every create/status/end response, while a currently-authorized target keeps
its label and canonical identity unchanged, stored JSON and ``focus_version``
are never mutated, and the existing owner/409-stamp denial controls are
untouched.

Real handlers, real ORM rows and the real readers throughout; only the
provider/network side is absent by construction (no channel is installed).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core.auth import AIPrincipal, principal_context  # noqa: E402
from ai.core.focus import contract as focus_contract  # noqa: E402
from ai.core.voice import routes  # noqa: E402
from ai.core.voice.routes import (  # noqa: E402
    VoiceSessionCreateRequest,
    create_voice_session,
    end_voice_session,
    get_voice_session,
)
from django.test import override_settings  # noqa: E402
from fastapi import HTTPException  # noqa: E402

HOST_SETTING = "aimms-foundry.services.ai.azure.com"
WITHHELD_LABEL = "CLASSIFIED PUMP LINE 7"
FOCUS_VERSION = 7


@pytest.fixture(scope="module", autouse=True)
def _database():
    from ai.core.tests.focus_test_db import ensure_island_tables

    ensure_island_tables()
    yield


# --------------------------------------------------------------------------- #
# Minimal route harness (self-contained; the realtime module's migrate lane is
# deliberately never imported or launched).
# --------------------------------------------------------------------------- #


def _user():
    from ai.core.tests.focus_test_db import grant_work_view
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username=f"rv01-{uuid.uuid4().hex[:8]}", password="unused"
    )

    return grant_work_view(user)


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f"user:{user.pk}",
        actor=f"user:{user.pk}",
        user_pk=str(user.pk),
        username=user.username,
        authentication_method="session",
        scope="site:pilot",
        policy_version="policy-v1",
        is_staff=False,
        is_superuser=False,
    )


def _settings(**over):
    from ai.core.config import Settings

    aliased = {
        "FEATURE_VOICE_LIVE": True,
        "AZURE_VOICELIVE_ENDPOINT": HOST_SETTING,
    }
    aliased.update(over)
    return Settings(_env_file=None, **aliased)  # ty: ignore[unknown-argument]


def _run(principal, coroutine_factory, settings):
    with patch.object(routes, "get_settings", return_value=settings):
        token = principal_context.set(principal)
        try:
            return asyncio.run(coroutine_factory())
        finally:
            principal_context.reset(token)


def _create(principal, settings, thread):
    return _run(
        principal,
        lambda: create_voice_session(
            VoiceSessionCreateRequest(consent_version="consent-v3-memory", thread_id=thread.id)
        ),
        settings,
    )


# --------------------------------------------------------------------------- #
# Real ORM fixtures
# --------------------------------------------------------------------------- #


def _stored_focus(kind: str, target_id: int) -> dict:
    """A stored focus projection exactly as the focus PUT would persist it."""
    if kind == "machine":
        target = focus_contract.FocusTarget(
            kind=kind,
            id=target_id,
            label=WITHHELD_LABEL,
            machine_id=target_id,
            work_order_id=None,
        )
    elif kind == "work_order":
        target = focus_contract.FocusTarget(
            kind=kind,
            id=target_id,
            label=WITHHELD_LABEL,
            machine_id=4242,
            work_order_id=target_id,
        )
    else:
        target = focus_contract.FocusTarget(
            kind=kind,
            id=target_id,
            label=WITHHELD_LABEL,
            machine_id=4242,
            work_order_id=5252,
        )
    return focus_contract.focus_to_stored(target)


def _thread(user, stored: dict, focus_version: int = FOCUS_VERSION):
    from aichat.models import ChatThread

    return ChatThread.objects.create(
        owner=user,
        scope_key="site:pilot",
        scope_hash="0" * 64,
        conversation_focus=stored,
        focus_version=focus_version,
    )


def _assert_stored_unchanged(thread, stored: dict, focus_version: int):
    """The fix must never mutate stored focus text to hide an outward leak."""
    from aichat.models import ChatThread

    row = (
        ChatThread.objects
        .filter(id=thread.id)
        .values_list("conversation_focus", "focus_version")
        .first()
    )
    assert row is not None
    assert row[0] == stored, "stored conversation_focus was mutated"
    assert row[1] == focus_version, "focus_version was mutated"


def _machine_with_client(name: str):
    """One real machine under one real client, via the real models."""
    from assets.models import AssetMachine

    client_model = AssetMachine._meta.get_field("client").related_model
    client = client_model.objects.create(
        name=f"rv01-client-{uuid.uuid4().hex[:8]}",
        code=f"rv01-{uuid.uuid4().hex[:10]}",
    )
    machine = AssetMachine.objects.create(name=name, client=client)
    return machine, client


def _stored_focus_exact(kind: str, target_id: int, label: str, machine_id, work_order_id) -> dict:
    """A stored projection whose label/relationships equal the canonical rows
    (used where the stored label legitimately matches the live reader label)."""
    target = focus_contract.FocusTarget(
        kind=kind,
        id=target_id,
        label=label,
        machine_id=machine_id,
        work_order_id=work_order_id,
    )
    return focus_contract.focus_to_stored(target)


def _work_order_with_card(machine):
    """One real work order + one real card via the real models, distinct PKs
    and a unique reference so the expected label is a fixture value."""
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
    reference = f"RV01-WO-{uuid.uuid4().hex[:12]}"
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


# --------------------------------------------------------------------------- #
# RV01 core regression: revoked/unavailable target label withheld everywhere.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind", ["machine", "work_order", "kanban_card"], ids=lambda k: f"focus_kind_{k}"
)
def test_revoked_or_unavailable_focus_label_is_withheld_across_create_status_end(kind):
    """The stored label must never leave create/status/end when the current
    readers deny the target (here: a deleted/nonexistent record). Stored
    text, hash-relevant JSON and ``focus_version`` are preserved."""
    user = _user()
    settings = _settings()
    principal = _principal(user)
    stored = _stored_focus(kind, 987654)  # no such record: readers deny it
    thread = _thread(user, stored)

    created = _create(principal, settings, thread)
    assert created["focus_version"] == FOCUS_VERSION
    assert created["focus"] is None, "revoked target label leaked on create"
    assert WITHHELD_LABEL not in json.dumps(created)
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)

    fetched = _run(principal, lambda: get_voice_session(created["id"]), settings)
    assert fetched["focus"] is None, "revoked target label leaked on status"
    assert WITHHELD_LABEL not in json.dumps(fetched)
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)

    ended = _run(principal, lambda: end_voice_session(created["id"]), settings)
    assert ended["focus"] is None, "revoked target label leaked on end"
    assert WITHHELD_LABEL not in json.dumps(ended)
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


def test_existing_but_out_of_scope_focus_target_label_is_withheld():
    """A real record the current actor may not read: label withheld (the
    readers decide; session ownership never grants target access)."""
    user = _user()
    machine, _client = _machine_with_client("Pump Station No. 1")
    other = _machine_with_client("Other Tenant Line")[1]
    stored = _stored_focus("machine", machine.pk)
    thread = _thread(user, stored)
    principal = _principal(user)
    with override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: [{"client_id": other.pk}],
    ):
        created = _create(principal, _settings(), thread)
        assert created["focus"] is None, "out-of-scope label leaked on create"
        assert WITHHELD_LABEL not in json.dumps(created)
        fetched = _run(principal, lambda: get_voice_session(created["id"]), _settings())
        assert fetched["focus"] is None, "out-of-scope label leaked on status"
        assert WITHHELD_LABEL not in json.dumps(fetched)
        ended = _run(principal, lambda: end_voice_session(created["id"]), _settings())
        assert ended["focus"] is None, "out-of-scope label leaked on end"
        assert WITHHELD_LABEL not in json.dumps(ended)
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


# --------------------------------------------------------------------------- #
# Unchanged positive and denial controls.
# --------------------------------------------------------------------------- #


def test_currently_authorized_focus_label_and_identity_unchanged():
    """A currently-authorized target keeps its label and canonical identity."""
    user = _user()
    machine, client = _machine_with_client("Authorized Pump Station")
    stored = _stored_focus("machine", machine.pk)
    # Stored label equals the live resolved label (same label/identity).
    stored["label"] = "Authorized Pump Station"
    thread = _thread(user, stored)
    principal = _principal(user)
    with override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: [{"client_id": client.pk}],
    ):
        created = _create(principal, _settings(), thread)
        fetched = _run(principal, lambda: get_voice_session(created["id"]), _settings())
        ended = _run(principal, lambda: end_voice_session(created["id"]), _settings())
    expected = {
        "kind": "machine",
        "id": machine.pk,
        "label": "Authorized Pump Station",
        "machine_id": machine.pk,
        "work_order_id": None,
    }
    assert created["focus"] == expected
    assert fetched["focus"] == expected
    assert ended["focus"] == expected, "authorized machine end lost its identity"
    assert created["focus_version"] == FOCUS_VERSION
    assert fetched["focus_version"] == FOCUS_VERSION
    assert ended["focus_version"] == FOCUS_VERSION
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


def test_no_focus_thread_keeps_legacy_null_shape():
    """A thread with no stored focus keeps the legacy null/0 shape."""
    user = _user()
    thread = _thread(user, {}, focus_version=0)
    principal = _principal(user)
    created = _create(principal, _settings(), thread)
    assert created["focus"] is None
    assert created["focus_version"] == 0
    assert WITHHELD_LABEL not in json.dumps(created)


def test_foreign_session_denial_controls_unchanged():
    """A non-owner still cannot even learn the session exists."""
    owner = _user()
    stranger = _user()
    thread = _thread(owner, _stored_focus("machine", 987654))
    created = _create(_principal(owner), _settings(), thread)
    for call in (
        lambda: get_voice_session(created["id"]),
        lambda: end_voice_session(created["id"]),
    ):
        with pytest.raises(HTTPException) as exc:
            _run(_principal(stranger), call, _settings())
        assert exc.value.status_code == 404
        assert exc.value.detail == "VOICE_SESSION_FORBIDDEN"


def test_stale_focus_stamp_still_refuses_create_409():
    """The 409 stamp guard is unchanged even when the label is withheld."""
    user = _user()
    thread = _thread(user, _stored_focus("machine", 987654), focus_version=7)
    with pytest.raises(HTTPException) as exc:
        _run(
            _principal(user),
            lambda: create_voice_session(
                VoiceSessionCreateRequest(
                    consent_version="consent-v3-memory",
                    thread_id=thread.id,
                    expected_focus_version=99,
                )
            ),
            _settings(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "VOICE_FOCUS_CHANGED"
    _assert_stored_unchanged(thread, _stored_focus("machine", 987654), 7)


# --------------------------------------------------------------------------- #
# Real authorized work_order / kanban_card create-status-end positive controls.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["work_order", "kanban_card"], ids=lambda k: f"focus_kind_{k}")
def test_authorized_work_order_card_focus_identity_unchanged(kind):
    """Real authorized work_order/kanban_card rows: create/status/end echo the
    canonical label/identity (fixture values asserted directly), with the card
    PK and its parent work-order PK kept distinct."""
    user = _user()
    machine, client = _machine_with_client(f"Authorized WO Pump Station {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    assert wo.pk != card.pk and wo.pk != machine.pk and card.pk != machine.pk
    if kind == "work_order":
        target_id, label, wo_id = wo.pk, wo.reference, wo.pk
    else:
        target_id, label, wo_id = card.pk, card.title, wo.pk
    stored = _stored_focus_exact(kind, target_id, label, machine.pk, wo_id)
    thread = _thread(user, stored)
    principal = _principal(user)
    with override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda _actor: [{"client_id": client.pk}],
    ):
        created = _create(principal, _settings(), thread)
        fetched = _run(principal, lambda: get_voice_session(created["id"]), _settings())
        ended = _run(principal, lambda: end_voice_session(created["id"]), _settings())
    expected = {
        "kind": kind,
        "id": target_id,
        "label": label,
        "machine_id": machine.pk,
        "work_order_id": wo_id,
    }
    for payload in (created, fetched, ended):
        assert payload["focus"] == expected
        assert payload["focus_version"] == FOCUS_VERSION
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)


# --------------------------------------------------------------------------- #
# Genuine authorized-to-revoked transition AFTER session creation.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind", ["machine", "work_order", "kanban_card"], ids=lambda k: f"focus_kind_{k}"
)
def test_authorized_to_revoked_transition_withholds_label_after_create(kind):
    """A real actor that COULD read the target at create time loses only its
    own grant afterwards: status/end withhold the label everywhere while the
    target rows still exist and the stored focus JSON, hash-relevant identity
    and ``focus_version`` stay untouched. Revocation is per-actor through the
    real ``AIMMS_MAINTENANCE_SCOPE_RESOLVER`` seam keyed by the actor's real
    identity; feature flags stay on (factory grant seam - a disclosed test
    seam, not authenticated/live authorization)."""
    from assets import ai_read as assets_ai_read
    from assets.models import AssetMachine
    from tasks import ai_read as tasks_ai_read
    from tasks.models import KanbanCard, WorkOrder

    user = _user()
    machine, client = _machine_with_client(f"Revoked Pump Station Line 3 {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    assert len({machine.pk, wo.pk, card.pk}) == 3  # focus identities distinct
    if kind == "machine":
        target_id, label, machine_id, wo_id = machine.pk, machine.name, machine.pk, None
    elif kind == "work_order":
        target_id, label, machine_id, wo_id = wo.pk, wo.reference, machine.pk, wo.pk
    else:
        target_id, label, machine_id, wo_id = card.pk, card.title, machine.pk, wo.pk
    stored = _stored_focus_exact(kind, target_id, label, machine_id, wo_id)
    thread = _thread(user, stored)
    principal = _principal(user)
    grants: dict = {user.username: [{"client_id": client.pk}]}
    with override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: grants.get(actor.username, []),
    ):
        created = _create(principal, _settings(), thread)
        expected = {
            "kind": kind,
            "id": target_id,
            "label": label,
            "machine_id": machine_id,
            "work_order_id": wo_id,
        }
        # The actor really held the grant at create time.
        assert created["focus"] == expected
        assert created["focus_version"] == FOCUS_VERSION

        # Revoke ONLY this actor's grant after session creation.
        grants[user.username] = []
        if kind == "machine":
            assert assets_ai_read.authorized_machine(user, machine.pk) is None
        elif kind == "work_order":
            assert tasks_ai_read.authorized_work_order(user, wo.pk) is None
        else:
            assert tasks_ai_read.authorized_kanban_card(user, card.pk) is None

        fetched = _run(principal, lambda: get_voice_session(created["id"]), _settings())
        assert fetched["focus"] is None, "revoked label leaked on status"
        assert fetched["focus_version"] == FOCUS_VERSION
        assert label not in json.dumps(fetched)

        ended = _run(principal, lambda: end_voice_session(created["id"]), _settings())
        assert ended["focus"] is None, "revoked label leaked on end"
        assert ended["focus_version"] == FOCUS_VERSION
        assert label not in json.dumps(ended)

    # The target rows still exist - nothing was deleted to hide the label.
    assert AssetMachine.objects.filter(pk=machine.pk).exists()
    assert WorkOrder.objects.filter(pk=wo.pk).exists()
    assert KanbanCard.objects.filter(pk=card.pk).exists()
    _assert_stored_unchanged(thread, stored, FOCUS_VERSION)
