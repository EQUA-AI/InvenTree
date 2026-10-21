"""RV02-B: history-only outward redaction of structured focus question cards.

``GET /threads/{thread_id}`` reloads each message's stored question card.
For outward STRUCTURED ``focus_selection`` question cards the card is projected
VERBATIM only when EVERY recorded option is still readable by the CURRENT
viewer (current-actor authority, never the thread owner's, snapshot
permissions, or the thread's later focus). If ANY recorded option is
unreadable, missing or malformed, or the actor lookup/resolver errors, the
WHOLE card is hidden (``question`` is None). Stored content, question text,
options, refs, ids and metadata are never rewritten; plain transcript text is
intentionally preserved, so disclosure is asserted against the structured
card/metadata fields being projected, not against all outward JSON.

Real ORM rows and the real readers throughout: the target identities are
re-authorized through the accepted ``NormalizedTurnService.
_recorded_focus_readable_now`` interface (real Django user rehydration plus
``ai.core.focus.resolution.resolve_focus_candidate``); no authorization mock
and no surrogate app/SDK module. Providerless: no provider is involved in
history reads.

Disclosed direct native handler: ``get_thread`` is invoked directly as an
async function (NOT authenticated ASGI). Route context injection supplies the
real ``AIPrincipal`` through ``get_current_principal``; the repository is the
app's real ``_repository(principal)`` factory (real scoped ORM
``ThreadRepository``). The only fixture injection is the current viewer's
identity; get_readable/resolver/reader/app logic are never replaced.

Scope: the history structured question card ONLY. Ongoing/replay result and
event projections remain later slices.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest  # noqa: E402
from ai.core import app as app_module  # noqa: E402
from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.focus import commands as focus_commands  # noqa: E402
from ai.core.questions.schema import build_pending_record  # noqa: E402
from aichat.models import ChatMessage  # noqa: E402
from django.test import override_settings  # noqa: E402
from fastapi import HTTPException  # noqa: E402

KINDS = ("machine", "work_order", "kanban_card")
QUESTION_TEXT = "Which one do you mean?"


@pytest.fixture(scope="module", autouse=True)
def _database():
    from ai.core.tests.focus_test_db import ensure_island_tables

    ensure_island_tables()
    yield


# --------------------------------------------------------------------------- #
# Real ORM fixtures (mirroring the frozen accepted RV02-A helpers, verbatim
# conventions; that accepted test file is NOT modified here).
# --------------------------------------------------------------------------- #


def _user():
    from ai.core.tests.focus_test_db import grant_work_view
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username=f"rv02b-{uuid.uuid4().hex[:8]}", password="unused"
    )

    return grant_work_view(user)


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


def _machine_with_client(name: str):
    from assets.models import AssetMachine

    client_model = AssetMachine._meta.get_field("client").related_model
    client = client_model.objects.create(
        name=f"rv02b-client-{uuid.uuid4().hex[:8]}",
        code=f"rv02b-{uuid.uuid4().hex[:10]}",
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
    wo = WorkOrder.objects.create(
        pk=base + 1,
        title="Seal replacement on pump line 7",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=machine,
        reference=f"RV02B-WO-{uuid.uuid4().hex[:12]}",
    )
    card = KanbanCard.objects.create(
        pk=base + 100,
        work_order=wo,
        title="Diagnose the seal leak",
        status="backlog",
    )
    assert wo.pk != card.pk
    return wo, card


def _grant_settings(grants):
    return override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: grants.get(actor.username, []),
    )


def _history_thread(user):
    """One real thread created through the app's own repository factory."""
    repository = app_module._repository(_principal(user))
    thread, _created = repository.get_or_create(None, title="RV02B history thread")
    return thread


def _target_identity(kind, machine, wo, card):
    """(id token, label, private ref) computed from the model rows only."""
    if kind == "machine":
        target_id, label = machine.pk, machine.name
    elif kind == "work_order":
        target_id, label = wo.pk, wo.reference
    else:
        target_id, label = card.pk, card.title
    return f"focus:{kind}:{target_id}", label, {"kind": kind, "id": target_id}


def _producer_options(kind, machine, wo, card, *, kind_override=None):
    option_id, label, ref = _target_identity(kind, machine, wo, card)
    return [
        {
            "id": option_id,
            "label": label,
            "ref": ref,
        }
    ]


def _focus_wire(thread, *, options):
    """The real producer wire: build_pending_record strips the private refs."""
    _record, wire = build_pending_record(
        thread_id=str(thread.pk),
        turn_id=f"turn-{uuid.uuid4().hex[:10]}",
        source=focus_commands.FOCUS_QUESTION_SOURCE,
        question_text=QUESTION_TEXT,
        options=options,
        origin_content="Switch focus to the pump",
        workflow="focus_switch",
        modality="text",
    )
    return wire


def _history_message(thread, *, sequence, content, metadata, role="assistant"):
    return ChatMessage.objects.create(
        thread=thread,
        sequence=sequence,
        role=role,
        content=content,
        metadata=metadata,
    )


def _get_thread(user, thread):
    """Direct native handler call with real principal route-context injection."""
    principal = _principal(user)

    async def call():
        return await app_module.get_thread(
            thread_id=str(thread.pk),
            include_messages=True,
            message_limit=50,
            before_sequence=None,
        )

    with mock.patch.object(app_module, "get_current_principal", lambda: principal):
        return asyncio.run(call())


def _message_rows(payload):
    return {row["sequence"]: row for row in payload["messages"]}


def _stored_metadata(message_id):
    return ChatMessage.objects.filter(pk=message_id).values_list("metadata", flat=True).first()


# --------------------------------------------------------------------------- #
# 1. Owner positive + genuine grant revocation (target rows still extant).
# --------------------------------------------------------------------------- #


def test_history_focus_question_verbatim_for_owner_machine():
    """Fully readable recorded option: the card projects byte-verbatim."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(user)
    wire = _focus_wire(thread, options=_producer_options("machine", machine, wo, card))
    stored_metadata = {"question": wire, "tool_name": "focus_switch"}
    message = _history_message(
        thread, sequence=1, content="Which machine?", metadata=stored_metadata
    )
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        payload = _get_thread(user, thread)
    row = _message_rows(payload)[1]
    assert row["question"] == wire, "authorized history card must project VERBATIM"
    assert row["content"] == "Which machine?"
    assert row["tool_name"] == "focus_switch"
    assert _stored_metadata(message.pk) == stored_metadata, "stored metadata mutated"


def test_history_focus_question_hidden_after_grant_revocation_machine():
    """The genuine allowed -> revoked case (machine kind): history positive,
    then the grant is revoked with the target rows still extant. The whole
    structured question card must be hidden while content and stored state
    stay byte-verbatim."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(user)
    wire = _focus_wire(thread, options=_producer_options("machine", machine, wo, card))
    stored_metadata = {"question": wire}
    message = _history_message(
        thread, sequence=1, content="Which machine?", metadata=stored_metadata
    )
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        positive = _get_thread(user, thread)
    assert _message_rows(positive)[1]["question"] == wire, "history positive first"

    grants[user.username] = []  # real revocation; the machine row stays extant
    with _grant_settings(grants):
        payload = _get_thread(user, thread)
    row = _message_rows(payload)[1]
    assert row["question"] is None, "structured focus question card leaked after revocation"
    assert row["content"] == "Which machine?", "transcript must stay preserved"
    structured = {key: value for key, value in row.items() if key != "content"}
    assert wire["options"][0]["label"] not in json.dumps(structured)
    assert _stored_metadata(message.pk) == stored_metadata, "stored metadata rewritten"


# --------------------------------------------------------------------------- #
# 2. Identical regression across machine / work_order / kanban_card
#    (deliberately unequal work-order and card PKs).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"focus_kind_{k}")
def test_history_focus_question_hidden_after_grant_revocation_all_kinds(kind):
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(user)
    wire = _focus_wire(thread, options=_producer_options(kind, machine, wo, card))
    stored_metadata = {"question": wire}
    message = _history_message(thread, sequence=1, content="Pick one.", metadata=stored_metadata)
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        assert _message_rows(_get_thread(user, thread))[1]["question"] == wire
    grants[user.username] = []
    with _grant_settings(grants):
        row = _message_rows(_get_thread(user, thread))[1]
    assert row["question"] is None, f"structured {kind} question card leaked after revocation"
    assert row["content"] == "Pick one."
    assert _stored_metadata(message.pk) == stored_metadata


# --------------------------------------------------------------------------- #
# 3. Partial denial hides the WHOLE card; missing/malformed identities fail
#    closed; non-focus/legacy cards and unauthorized 404 stay unchanged.
# --------------------------------------------------------------------------- #


def test_history_focus_question_mixed_options_hide_whole_card():
    user = _user()
    machine_a, client_a = _machine_with_client(f"RV02B Pump A {uuid.uuid4().hex[:8]}")
    machine_b, _client_b = _machine_with_client(f"RV02B Pump B {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine_a)
    thread = _history_thread(user)
    options = _producer_options("machine", machine_a, wo, card)
    id_b, label_b, ref_b = _target_identity("machine", machine_b, wo, card)
    options.append({"id": id_b, "label": label_b, "ref": ref_b})
    wire = _focus_wire(thread, options=options)
    stored_metadata = {"question": wire}
    message = _history_message(thread, sequence=1, content="Which one?", metadata=stored_metadata)
    grants = {user.username: [{"client_id": client_a.pk}]}  # machine_b denied
    with _grant_settings(grants):
        row = _message_rows(_get_thread(user, thread))[1]
    assert row["question"] is None, "partially denied card must hide the WHOLE card"
    assert row["content"] == "Which one?"
    assert label_b not in json.dumps({k: v for k, v in row.items() if k != "content"})
    assert _stored_metadata(message.pk) == stored_metadata


def test_history_focus_question_missing_target_fails_closed():
    """Valid literal option id for a row that does not exist: the real
    resolver errors/denies, so the card hides."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    _wo, _card = _work_order_with_card(machine)
    thread = _history_thread(user)
    missing_id = f"focus:machine:{machine.pk + 100000}"
    wire = _focus_wire(
        thread,
        options=[{"id": missing_id, "label": "Ghost pump", "ref": {"kind": "machine", "id": 0}}],
    )
    stored_metadata = {"question": wire}
    _message = _history_message(thread, sequence=1, content="Which one?", metadata=stored_metadata)
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        row = _message_rows(_get_thread(user, thread))[1]
    assert row["question"] is None, "missing target identity must fail closed"
    assert row["content"] == "Which one?"


MALFORMED_OPTIONS = {
    "missing_label": [{"id": "focus:machine:3", "ref": {"kind": "machine", "id": 3}}],
    "missing_id": [{"label": "Machine 3", "ref": {"kind": "machine", "id": 3}}],
    "no_focus_prefix": [{"id": "machine:3", "label": "Machine 3"}],
    "non_numeric_id": [{"id": "focus:machine:abc", "label": "Machine 3"}],
    "non_canonical_id": [{"id": "focus:machine:007", "label": "Machine 3"}],
    "unknown_kind": [{"id": "focus:frobnicator:3", "label": "Machine 3"}],
    "option_not_mapping": ["focus:machine:3"],
    "options_not_list": {"id": "focus:machine:3", "label": "Machine 3"},
}


@pytest.mark.parametrize("case", sorted(MALFORMED_OPTIONS), ids=lambda c: f"malformed_{c}")
def test_history_focus_question_malformed_options_fail_closed(case):
    """A recorded card whose options are missing/malformed hides WHOLE; the
    malformed tokens are never normalized into valid identities."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(user)
    wire = _focus_wire(thread, options=_producer_options("machine", machine, wo, card))
    malformed = dict(wire)
    malformed["options"] = copy.deepcopy(MALFORMED_OPTIONS[case])
    stored_metadata = {"question": malformed}
    message = _history_message(thread, sequence=1, content="Which one?", metadata=stored_metadata)
    grants = {user.username: [{"client_id": client.pk}]}  # fully granted; the card is malformed
    with _grant_settings(grants):
        row = _message_rows(_get_thread(user, thread))[1]
    assert row["question"] is None, f"malformed options ({case}) must fail closed"
    assert row["content"] == "Which one?"
    assert _stored_metadata(message.pk) == stored_metadata


def test_history_non_focus_and_legacy_cards_unchanged():
    """Non-focus question cards, legacy no-card messages and plain transcript
    text (including a focus label) are preserved unchanged."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    _wo, _card = _work_order_with_card(machine)
    thread = _history_thread(user)
    non_focus = {
        "kind": "clarification_question",
        "question_text": "Ship it or wait?",
        "source": "workflow_choice",
        "options": [{"id": "choice:ship", "label": "Ship", "kind": ""}],
    }
    legacy_text = f"Earlier transcript mentions {machine.name} verbatim."
    m1 = _history_message(thread, sequence=1, content="Workflow?", metadata={"question": non_focus})
    m2 = _history_message(thread, sequence=2, content=legacy_text, metadata={})
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        rows = _message_rows(_get_thread(user, thread))
    assert rows[1]["question"] == non_focus, "non-focus card must stay unchanged"
    assert rows[2]["question"] is None, "legacy no-card message stays without a card"
    assert rows[2]["content"] == legacy_text, "plain transcript text is preserved"
    assert machine.name in rows[2]["content"]
    assert _stored_metadata(m1.pk) == {"question": non_focus}
    assert _stored_metadata(m2.pk) == {}


def test_history_projection_preserves_stored_state_and_unrelated_fields():
    """Authorized card + question_resolution and unrelated metadata fields
    pass through exactly; stored metadata (with private refs) is byte-equal
    after the read."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(user)
    options = _producer_options("machine", machine, wo, card)
    wire = _focus_wire(thread, options=options)
    stored_metadata = {
        "question": wire,
        "question_resolution": {"outcome": "selected", "option_id": options[0]["id"]},
        "tool_name": "focus_switch",
        "workflow_id": "wf-rv02b",
        "response_state": "complete",
    }
    message = _history_message(
        thread, sequence=1, content="Which machine?", metadata=copy.deepcopy(stored_metadata)
    )
    before = _stored_metadata(message.pk)
    assert before["question"]["options"][0] == wire["options"][0]
    assert "ref" not in before["question"]["options"][0], "wire option carries no ref"
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        row = _message_rows(_get_thread(user, thread))[1]
    assert row["question"] == wire
    assert row["question_resolution"] == stored_metadata["question_resolution"]
    assert row["tool_name"] == "focus_switch"
    assert row["workflow_id"] == "wf-rv02b"
    assert row["response_state"] == "complete"
    assert row["content"] == "Which machine?"
    assert _stored_metadata(message.pk) == before, "stored metadata rewritten by a read"


def test_history_unauthorized_thread_scope_404_unchanged():
    """Scope-first 404 for a viewer with no access is preserved."""
    owner = _user()
    other = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(owner)
    wire = _focus_wire(thread, options=_producer_options("machine", machine, wo, card))
    _history_message(thread, sequence=1, content="Which machine?", metadata={"question": wire})
    grants = {owner.username: [{"client_id": client.pk}]}
    with _grant_settings(grants), pytest.raises(HTTPException) as excinfo:
        _get_thread(other, thread)
    assert excinfo.value.status_code == 404


# --------------------------------------------------------------------------- #
# 4. Tranche15 history correction controls: oversized numeric identity fails
#    closed (regression), shared-viewer CURRENT-actor authority beats owner
#    authority, and a resolver EXCEPTION fails closed. Real ORM rows, real
#    readers and the real resolver throughout — never an authorization mock.
# --------------------------------------------------------------------------- #


def test_history_focus_question_oversized_numeric_identity_fails_closed():
    """Oversized decimal identity (over the native ``int()`` digit limit) must
    fail CLOSED — the whole card hides — instead of raising out of the real
    ``get_thread`` projection. Fully readable positive control first, then the
    malformed saved option on the same real thread."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(user)
    wire = _focus_wire(thread, options=_producer_options("machine", machine, wo, card))
    stored_metadata = {"question": wire}
    message = _history_message(
        thread, sequence=1, content="Which machine?", metadata=stored_metadata
    )
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        positive = _get_thread(user, thread)
    assert _message_rows(positive)[1]["question"] == wire, "positive control first"

    oversized = dict(wire)
    option = dict(wire["options"][0])
    option["id"] = "focus:machine:" + "9" * 4301
    oversized["options"] = [option]
    stored_malformed = {"question": oversized}
    message2 = _history_message(thread, sequence=2, content="Which one?", metadata=stored_malformed)
    with _grant_settings(grants):
        row = _message_rows(_get_thread(user, thread))[2]
    assert row["question"] is None, "oversized numeric identity must fail closed"
    assert row["content"] == "Which one?", "transcript must stay preserved"
    assert _stored_metadata(message2.pk) == stored_malformed, "stored metadata rewritten"
    assert _stored_metadata(message.pk) == stored_metadata, "stored metadata rewritten"


def _shared_grant_settings():
    """The configured focus resolver bound to REAL per-actor grant rows."""
    from assets.models import ClientScopeGrant

    def resolver(actor):
        return [
            {"client_id": client_id}
            for client_id in ClientScopeGrant.objects.filter(
                user__username=actor.username
            ).values_list("client_id", flat=True)
        ]

    return override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=resolver,
        FEATURE_THREAD_SHARING=True,
    )


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"shared_focus_kind_{k}")
def test_history_focus_question_shared_viewer_authority_current_actor(kind):
    """Real shared thread (explicit analysis scope on allowed machine A, so the
    required shared boundary is client A): owner and shared viewer both see the
    fully readable card first; revoking ONLY the viewer's target-client grant
    (thread grant and required client-A grant extant) hides the card for the
    viewer while the real owner still projects it verbatim."""
    from ai.core.analysis import scope as analysis_scope
    from aichat.models import ChatThreadGrant
    from aichat.services.memory_sharing import required_shared_clients
    from assets.models import ClientScopeGrant

    owner = _user()
    viewer = _user()
    machine_a, client_a = _machine_with_client(f"RV02B Scope A {uuid.uuid4().hex[:8]}")
    machine_b, client_b = _machine_with_client(f"RV02B Target B {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine_b)
    thread = _history_thread(owner)
    thread.analysis_scope = {
        "schema_version": analysis_scope.SCHEMA_VERSION,
        "mode": analysis_scope.MODE_EXPLICIT,
        "machine_ids": [machine_a.pk],
    }
    thread.analysis_scope_version = 1
    thread.save(update_fields=["analysis_scope", "analysis_scope_version"])
    assert required_shared_clients(thread) == frozenset({client_a.code})

    wire = _focus_wire(thread, options=_producer_options(kind, machine_b, wo, card))
    stored_metadata = {"question": wire}
    message = _history_message(thread, sequence=1, content="Which one?", metadata=stored_metadata)
    ChatThreadGrant.objects.create(thread=thread, grantee=viewer, granted_by=owner)
    ClientScopeGrant.objects.create(user=owner, client=client_a)
    ClientScopeGrant.objects.create(user=owner, client=client_b)
    ClientScopeGrant.objects.create(user=viewer, client=client_a)
    viewer_target = ClientScopeGrant.objects.create(user=viewer, client=client_b)

    with _shared_grant_settings():
        owner_payload = _get_thread(owner, thread)
        viewer_payload = _get_thread(viewer, thread)
        assert owner_payload["shared"] is False
        assert _message_rows(owner_payload)[1]["question"] == wire, "owner positive"
        assert viewer_payload["shared"] is True, "get_readable must mark the grant path"
        assert _message_rows(viewer_payload)[1]["question"] == wire, "viewer positive"

        viewer_target.delete()  # genuine revocation of the target-client grant
        viewer_after = _get_thread(viewer, thread)
        assert viewer_after["shared"] is True, "shared-thread access must remain"
        row = _message_rows(viewer_after)[1]
        assert row["question"] is None, "viewer card leaked after target revocation"
        assert row["content"] == "Which one?", "transcript must stay preserved"
        owner_after = _message_rows(_get_thread(owner, thread))[1]
        assert owner_after["question"] == wire, "real owner must still see the card"
    assert _stored_metadata(message.pk) == stored_metadata, "stored metadata rewritten"


def test_history_focus_question_resolver_exception_fails_closed():
    """A resolver EXCEPTION (never a substituted authorization verdict) hides
    the whole card while the transcript, unrelated cards and stored state stay
    untouched; the ordinary successful path returns once the fault is gone."""
    user = _user()
    machine, client = _machine_with_client(f"RV02B Pump {uuid.uuid4().hex[:8]}")
    wo, card = _work_order_with_card(machine)
    thread = _history_thread(user)
    wire = _focus_wire(thread, options=_producer_options("machine", machine, wo, card))
    stored_metadata = {"question": wire, "tool_name": "focus_switch"}
    message = _history_message(
        thread, sequence=1, content="Which machine?", metadata=stored_metadata
    )
    non_focus = {
        "kind": "clarification_question",
        "question_text": "Ship it or wait?",
        "source": "workflow_choice",
        "options": [{"id": "choice:ship", "label": "Ship", "kind": ""}],
    }
    message2 = _history_message(
        thread, sequence=2, content="Unrelated turn", metadata={"question": non_focus}
    )
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        positive = _get_thread(user, thread)
    assert _message_rows(positive)[1]["question"] == wire, "paired readable first"

    from ai.core.focus import resolution as focus_resolution

    with (
        _grant_settings(grants),
        mock.patch.object(
            focus_resolution,
            "resolve_focus_candidate",
            side_effect=RuntimeError("resolver fault"),
        ) as resolver,
    ):
        rows = _message_rows(_get_thread(user, thread))
    assert resolver.called, "the real resolver must be invoked before fail-closed"
    assert rows[1]["question"] is None, "resolver exception must hide the whole card"
    assert rows[1]["content"] == "Which machine?"
    assert rows[1]["tool_name"] == "focus_switch"
    assert rows[2]["question"] == non_focus, "unrelated card must stay unchanged"
    assert _stored_metadata(message.pk) == stored_metadata, "stored metadata rewritten"
    assert _stored_metadata(message2.pk) == {"question": non_focus}

    with _grant_settings(grants):
        restored = _message_rows(_get_thread(user, thread))[1]
    assert restored["question"] == wire, "ordinary path after the fault is restored"
