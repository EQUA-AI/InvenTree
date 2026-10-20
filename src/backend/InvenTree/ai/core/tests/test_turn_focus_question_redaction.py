"""RV02 result + stored replay: current-actor projection of focus question cards.

The normalized turn result (``pending_question``) and the stored replay of the
persisted QUESTION event both project the turn's structured question card on
the complete, replayed and incomplete paths. For an outward
``source == "focus_selection"`` card every RECORDED option's literal id token
(``focus:<kind>:<id>``) must be readable by the CURRENT actor — real Django
user rehydration plus the existing authorized readers behind
``resolve_focus_candidate`` (via the accepted RV02-A seam
``_recorded_focus_readable_now``). Any unreadable, missing or malformed
recorded option, or a lookup/resolver failure, hides the WHOLE structured card:
``pending_question`` is withheld on the result, and the stored replay QUESTION
event keeps its envelope/type/identity/timestamp while withholding only its
structured card body. Fully readable cards and non-``focus_selection`` cards
stay VERBATIM on both surfaces. Stored canonical/pending/message/options/
identity/hash are never rewritten, the question slot is never re-armed or
popped by a retry, and an exact same-key retry executes the workflow and its
proposal exactly once.

Labels of REVOKED options must not be replaced by the thread's later current
focus or by the owner's authority: authorization is the current viewer over
each recorded option identity.

Real ORM rows, the real ``ThreadRepository``/intake/``_process_turn`` path, the
real ``build_pending_record``/``_arm_pending_question`` producer wiring and the
real authorized readers throughout (no resolver or reader mocking; the only
monkeypatch is a thrown resolver fault, and one observer wrapper around the
projection seam). The producer is raised through the real
``ai.core.questions.promotion`` seam with the exact native
``ai.core.focus.commands`` shapes (``FOCUS_QUESTION_SOURCE`` and
``focus:<kind>:<id>`` option ids with server-owned refs).
Providerless disclosure: no provider channel is installed; the workflow is a
local generator standing in for the provider, exactly as the existing
normalized-turn service tests disclose.

Scope: the outward RESULT card and the STORED REPLAY QUESTION projection only.
The live/new QUESTION event of a fresh turn is the separate RV02 live-arming
slice and is deliberately neither changed nor asserted here; plain stored
transcript text is preserved by design.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import asdict

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

# The question-card producer surface ships behind a dark flag. This tracer
# exercises the REAL arming pipeline (producer -> ``_arm_pending_question`` ->
# durable records), so the documented feature flag is enabled for this test
# process: ``get_settings()`` is env-backed and lru-cached, so the cache is
# rebuilt at test setup. No producer, reader or resolver is faked.
os.environ["AIMMS_FEATURE_QUESTION_CARDS"] = "true"

import django

django.setup()

import pytest  # noqa: E402
from ai.core.auth import AIPrincipal  # noqa: E402
from ai.core.config import get_settings  # noqa: E402
from ai.core.focus import resolution as focus_resolution  # noqa: E402
from ai.core.focus.commands import FOCUS_QUESTION_SOURCE  # noqa: E402
from ai.core.questions.promotion import set_question_proposal  # noqa: E402
from ai.core.streaming import AGUIEvent, EventType, InMemoryEventEmitter  # noqa: E402
from ai.core.trusted_context import TrustedTurnContext  # noqa: E402
from ai.core.turn_service import NormalizedTurnService  # noqa: E402
from django.test import override_settings  # noqa: E402

QUESTION_TEXT = "Which one do you mean?"
TURN_CONTENT = "Which pump did you mean?"

#: Native ``focus.commands`` recorded option kinds.
FOCUS_KINDS = ("machine", "work_order", "kanban_card")

#: (first option kind, second option kind) pairs covering all three kinds in
#: both option positions; ``work_order`` and ``kanban_card`` rows carry
#: distinct PKs (asserted in the fixture builder).
OPTION_PAIRS = (
    ("machine", "work_order"),
    ("work_order", "kanban_card"),
    ("kanban_card", "machine"),
)

#: Malformed recorded option identities that survive native arming (arming
#: only requires non-empty ``id``/``label``) but are never valid recorded
#: focus identities — each must hide the WHOLE card, never normalize.
MALFORMED_IDS = (
    "42",
    "focus:machine:abc",
    "focus:machine:007",
    "focus:widget:5",
    "focus:machine:5:extra",
    "focus::5",
    "focus:machine:",
)

#: A non-``focus_selection`` producer stamp: never a structured focus card.
NON_FOCUS_SOURCE = "task_clarify"

#: ``AGUIEvent.to_dict`` envelope keys; everything else is event body.
ENVELOPE_KEYS = ("type", "timestamp", "threadId", "runId", "agentName", "eventId")


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
        username=f"rv02q-{uuid.uuid4().hex[:8]}", password="unused"
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
        name=f"rv02q-client-{uuid.uuid4().hex[:8]}",
        code=f"rv02q-{uuid.uuid4().hex[:10]}",
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
    reference = f"RV02Q-WO-{uuid.uuid4().hex[:12]}"
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
    assert wo.pk != card.pk, "the card and its parent must keep distinct PKs"
    return wo, card


def _target(kind: str, machine, wo, card) -> tuple[str, int, str]:
    """(kind, identity, label) computed from the model rows themselves."""
    if kind == "machine":
        return ("machine", machine.pk, machine.name)
    if kind == "work_order":
        return ("work_order", wo.pk, wo.reference)
    return ("kanban_card", card.pk, card.title)


def _producer_options(targets) -> list[dict]:
    """Exact native ``focus.commands`` producer option shapes."""
    return [
        {
            "id": f"focus:{kind}:{identity}",
            "label": label,
            "ref": {"kind": kind, "id": identity},
        }
        for kind, identity, label in targets
    ]


def _wire_options(targets) -> list[dict]:
    """Independently computed ref-free wire shapes (``questions.schema``)."""
    return [
        {"id": f"focus:{kind}:{identity}", "label": label, "kind": ""}
        for kind, identity, label in targets
    ]


def _repository_for(user):
    from aichat.models import ThreadNamespace
    from aichat.services import ThreadRepository

    return ThreadRepository(
        actor=str(user.pk),
        scope_key="site:main",
        namespace=ThreadNamespace.UNSCOPED,
    )


def _thread_for(user, title: str):
    thread, _created = _repository_for(user).get_or_create(None, title=title)
    return thread


def _two_client_fixtures():
    """One actor, two extant machine/wo/card trees in two different clients.

    Every target row and its ownership stay extant for the whole test; only
    the actor's target-client grant is ever revoked.
    """
    user = _user()
    machine1, client1 = _machine_with_client(f"RV02Q Pump A {uuid.uuid4().hex[:8]}")
    wo1, card1 = _work_order_with_card(machine1)
    machine2, client2 = _machine_with_client(f"RV02Q Pump B {uuid.uuid4().hex[:8]}")
    wo2, card2 = _work_order_with_card(machine2)
    rows = {
        "machine": (machine1, machine2),
        "work_order": (wo1, wo2),
        "kanban_card": (card1, card2),
    }
    return user, client1, client2, rows


# --------------------------------------------------------------------------- #
# Durable-state and wire observability (real rows and the real cache slot).
# --------------------------------------------------------------------------- #


def _row_state(instance) -> dict:
    """Every stored field of one row except ``auto_now`` bookkeeping stamps."""
    return {
        field.name: getattr(instance, field.name)
        for field in instance._meta.fields
        if not getattr(field, "auto_now", False)
    }


def _pending_slot(thread_id):
    """Non-destructive read of the real cached pending-question slot."""
    from ai.core.questions.pending import CachedPendingQuestionStore
    from django.core.cache import cache

    return cache.get(CachedPendingQuestionStore._key(thread_id))


def _durable_state(thread) -> dict:
    from aichat.models import ChatTurn

    turn = ChatTurn.objects.filter(thread=thread).order_by("created_at").last()
    assert turn is not None
    return {
        "turn": _row_state(turn),
        "input_message": _row_state(turn.input_message),
        "output_message": _row_state(turn.output_message),
        "pending_slot": _pending_slot(thread.pk),
    }


def _stored_events(thread) -> list[dict]:
    from aichat.models import ChatTurn

    canonical = (
        ChatTurn.objects
        .filter(thread=thread)
        .order_by("created_at")
        .values_list("canonical_result", flat=True)
        .last()
    )
    assert isinstance(canonical, dict)
    return list(canonical.get("events") or [])


def _stored_question_record(thread) -> dict:
    records = [r for r in _stored_events(thread) if r.get("type") == "QUESTION"]
    assert len(records) == 1, f"expected exactly one stored QUESTION record, got {len(records)}"
    return records[0]


# --------------------------------------------------------------------------- #
# Providerless turn harness (disclosed: local generator, no provider channel).
# --------------------------------------------------------------------------- #


class _ProviderlessQuestionWorkflow:
    """Local generator standing in for the provider (no channel installed).

    It raises the question proposal through the real promotion producer seam
    using the exact native ``focus.commands`` shapes; the real
    ``_arm_pending_question``/``build_pending_record`` wiring then runs inside
    the real turn.
    """

    def __init__(self, *, options, source: str = FOCUS_QUESTION_SOURCE, mid_turn=None):
        self.calls: list[dict] = []
        self.options = options
        self.source = source
        self.mid_turn = mid_turn

    async def run_stream(self, **kwargs):
        self.calls.append(kwargs)
        emitter = kwargs["emitter"]
        thread_id = kwargs["thread_id"]
        await emitter.emit(
            AGUIEvent(event_type=EventType.RUN_STARTED, thread_id=thread_id, run_id="run-rv02q")
        )
        await emitter.emit(
            AGUIEvent(
                event_type=EventType.WORKFLOW_STARTED,
                data={"workflow_id": "focus_clarify"},
                thread_id=thread_id,
                run_id="run-rv02q",
            )
        )
        set_question_proposal({
            "source": self.source,
            "question_text": QUESTION_TEXT,
            "options": [dict(option) for option in self.options],
        })
        yield "Normalized "
        if self.mid_turn is not None:
            self.mid_turn()
        yield "response"
        await emitter.emit(
            AGUIEvent(event_type=EventType.RUN_FINISHED, thread_id=thread_id, run_id="run-rv02q")
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


def _question_events(capture: _Capture) -> list[AGUIEvent]:
    return [event for event in capture.events if event.event_type == EventType.QUESTION]


def _replayed_views(capture: _Capture) -> list[dict]:
    """Outward replay record views (the focus echo is a separate channel)."""
    views = []
    for event in capture.events:
        data = getattr(event, "data", None) or {}
        if event.event_type == EventType.CUSTOM and data.get("name") == "aimms.threadFocus":
            continue
        views.append(event.to_dict())
    return views


def _withheld_view(record: dict) -> dict:
    """Envelope-only view: the structured card body is withheld."""
    return {key: value for key, value in record.items() if key in ENVELOPE_KEYS}


def _grant_settings(grants):
    get_settings.cache_clear()
    return override_settings(
        AIMMS_MACHINE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_AI_READ_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: grants.get(actor.username, []),
    )


def _observe_projection(monkeypatch, frames: list):
    """Observe the ORIGINAL projector with real ``asdict`` before/after."""
    project = getattr(NormalizedTurnService, "_reauthorize_result_question", None)
    if project is None:  # pre-fix source: the seam does not exist yet
        return

    async def _observe(self, actor, result):
        before_fields = asdict(result)
        projected = await project(self, actor, result)
        frames.append((before_fields, asdict(projected)))
        return projected

    monkeypatch.setattr(NormalizedTurnService, "_reauthorize_result_question", _observe)


def _assert_other_fields_identical(before_fields: dict, after_fields: dict, *, exempt: set):
    assert before_fields.keys() == after_fields.keys()
    for field_name in before_fields:
        if field_name in exempt:
            continue
        assert before_fields[field_name] == after_fields[field_name], (
            f"projection mutated declared result field {field_name!r}"
        )


# --------------------------------------------------------------------------- #
# The tracer: readable control first, then a single option's target grant is
# revoked and the EXACT same-key retry must withhold both outward cards while
# storage, the pending slot and exactly-once execution stay untouched.
# --------------------------------------------------------------------------- #


def test_result_and_replay_withhold_focus_question_card_after_single_option_grant_revocation(
    monkeypatch,
):
    """Genuine allowed->revoked case with both targets and ownership extant.

    Turn one (control): both recorded options readable — the result card, the
    QUESTION wire event and the durable records carry the full card.
    Then the actor's grant for the SECOND option's client is revoked (rows
    untouched) and the exact same-key retry must: run the workflow/proposal
    exactly once, withhold ``result.pending_question`` entirely, and replay the
    stored QUESTION event with its envelope/identity/timestamp byte-verbatim
    while withholding the structured card body — every unrelated replay event
    byte-verbatim, and durable canonical/pending/message/hash untouched.
    """
    frames: list = []
    _observe_projection(monkeypatch, frames)

    user, client1, client2, rows = _two_client_fixtures()
    targets = [
        _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0]),
        _target("machine", rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1]),
    ]
    label2 = targets[1][2]
    thread = _thread_for(user, "RV02Q tracer")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    key = f"rv02q-{uuid.uuid4().hex}"
    correlation_id = str(uuid.uuid4())
    principal = _principal(user)

    with _grant_settings(grants):
        control, control_capture = _run_turn(
            principal, thread.pk, workflow, key=key, correlation_id=correlation_id
        )

    # --- control: the readable card is projected verbatim everywhere ---
    card = control.pending_question
    assert isinstance(card, dict), "the readable control did not project the card"
    assert card["question_text"] == QUESTION_TEXT
    assert card["source"] == FOCUS_QUESTION_SOURCE
    assert card["options"] == _wire_options(targets), "recorded option identities were not verbatim"
    wire = _question_events(control_capture)
    assert len(wire) == 1, "expected exactly one QUESTION wire event"
    assert wire[0].to_dict() == _stored_question_record(thread), (
        "QUESTION wire event and the stored record diverged"
    )
    stored = _durable_state(thread)
    stored_record = _stored_question_record(thread)
    assert card == {k: v for k, v in stored_record.items() if k not in ENVELOPE_KEYS}, (
        "result card and stored QUESTION body diverged"
    )
    slot = stored["pending_slot"]
    assert isinstance(slot, dict) and slot["interrupt_id"] == card["interrupt_id"]
    assert slot["source"] == FOCUS_QUESTION_SOURCE
    assert [o["label"] for o in slot["options"]] == [t[2] for t in targets], (
        "pending slot did not keep the recorded options"
    )

    # --- revoke ONE option's target grant (rows and ownership stay extant) ---
    grants[user.username] = [{"client_id": client1.pk}]
    workflow2 = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        replay, replay_capture = _run_turn(
            principal, thread.pk, workflow2, key=key, correlation_id=correlation_id
        )

    # Exactly-once: the retry must not execute the workflow or its proposal.
    assert replay.replayed is True
    assert len(workflow.calls) == 1, "the exact retry re-executed the workflow"
    assert workflow2.calls == [], "the exact retry ran a second workflow/proposal"

    # Result: the whole structured card is withheld; every other field stays.
    assert replay.pending_question is None, (
        "result.pending_question structured card leaked after option grant revocation"
    )
    assert label2 not in json.dumps(asdict(replay)), "revoked option label leaked on the result"
    control_fields, replay_fields = asdict(control), asdict(replay)
    _assert_other_fields_identical(
        control_fields, replay_fields, exempt={"pending_question", "replayed"}
    )

    # Stored replay QUESTION projection: envelope kept, body withheld.
    replay_question = _question_events(replay_capture)
    assert len(replay_question) == 1, "expected exactly one replayed QUESTION event"
    assert replay_question[0].event_type == EventType.QUESTION
    assert replay_question[0].event_id == stored_record["eventId"]
    assert replay_question[0].timestamp.isoformat() == stored_record["timestamp"]
    assert replay_question[0].thread_id == stored_record["threadId"]
    assert replay_question[0].run_id == stored_record["runId"]
    assert replay_question[0].agent_name == stored_record["agentName"]
    assert replay_question[0].data == {}, (
        "replay QUESTION structured card body leaked after option grant revocation"
    )
    assert label2 not in json.dumps(replay_question[0].to_dict()), (
        "revoked option label leaked in the replay QUESTION body"
    )

    # Unrelated/non-focus replay stays exact; only the card body is withheld.
    expected_views = [
        _withheld_view(record) if record.get("type") == "QUESTION" else record
        for record in _stored_events(thread)
    ]
    assert _replayed_views(replay_capture) == expected_views, (
        "replay deviated from the stored events beyond the withheld card body"
    )

    # No rearm/pop/storage rewrite: durable state and the pending slot are
    # byte-verbatim and the slot still holds the ORIGINAL question.
    assert _durable_state(thread) == stored, (
        "stored canonical/message/pending/hash changed across the exact retry"
    )

    # Field preservation of the observed original projector (real asdict).
    assert len(frames) == 2, f"expected both projections observed, got {len(frames)}"
    control_before, control_after = frames[0]
    _assert_other_fields_identical(control_before, control_after, exempt={"pending_question"})
    assert control_after["pending_question"] == card, "control card was not verbatim"
    replay_before, replay_after = frames[1]
    assert replay_before["pending_question"] == card, "pre-projection replay card was not recorded"
    assert replay_after["pending_question"] is None
    _assert_other_fields_identical(replay_before, replay_after, exempt={"pending_question"})


# --------------------------------------------------------------------------- #
# All three recorded option kinds (distinct PKs for card vs parent).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind_pair", OPTION_PAIRS, ids=lambda p: f"options_{p[0]}_{p[1]}")
def test_result_and_replay_withhold_card_for_mixed_option_kinds(kind_pair):
    """The tracer shape over work_order/kanban_card identities as well: one
    readable option plus one revoked option hides the WHOLE card on the result
    and in the replay body, while the readable control stays verbatim."""
    user, client1, client2, rows = _two_client_fixtures()
    kind1, kind2 = kind_pair
    first = _target(kind1, rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0])
    second = _target(kind2, rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1])
    targets = [first, second]
    assert first[1] != second[1], "the two option identities must be distinct PKs"
    thread = _thread_for(user, f"RV02Q kinds {kind1}-{kind2}")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    key = f"rv02q-{uuid.uuid4().hex}"
    correlation_id = str(uuid.uuid4())
    principal = _principal(user)

    with _grant_settings(grants):
        control, _control_capture = _run_turn(
            principal, thread.pk, workflow, key=key, correlation_id=correlation_id
        )
    assert control.pending_question is not None
    assert control.pending_question["options"] == _wire_options(targets)

    grants[user.username] = [{"client_id": client1.pk}]
    with _grant_settings(grants):
        replay, replay_capture = _run_turn(
            principal, thread.pk, workflow, key=key, correlation_id=correlation_id
        )
    assert replay.replayed is True
    assert len(workflow.calls) == 1, "the exact retry re-executed the workflow"
    assert replay.pending_question is None, (
        "result card leaked for mixed recorded option kinds after revocation"
    )
    replay_question = _question_events(replay_capture)
    assert len(replay_question) == 1
    assert replay_question[0].data == {}, (
        "replay QUESTION body leaked for mixed recorded option kinds after revocation"
    )
    assert second[2] not in json.dumps(replay_question[0].to_dict())


# --------------------------------------------------------------------------- #
# Non-focus cards are not structured focus cards: VERBATIM everywhere.
# --------------------------------------------------------------------------- #


def test_non_focus_question_card_stays_verbatim_on_result_and_replay():
    user = _user()
    _machine, client = _machine_with_client(f"RV02Q Pump C {uuid.uuid4().hex[:8]}")
    thread = _thread_for(user, "RV02Q non-focus")
    options = [
        {"id": "opt-1", "label": "Seal kit A"},
        {"id": "opt-2", "label": "Seal kit B"},
    ]
    workflow = _ProviderlessQuestionWorkflow(options=options, source=NON_FOCUS_SOURCE)
    key = f"rv02q-{uuid.uuid4().hex}"
    correlation_id = str(uuid.uuid4())
    principal = _principal(user)
    grants = {user.username: [{"client_id": client.pk}]}
    with _grant_settings(grants):
        control, _control_capture = _run_turn(
            principal, thread.pk, workflow, key=key, correlation_id=correlation_id
        )
    assert control.pending_question is not None
    assert control.pending_question["source"] == NON_FOCUS_SOURCE
    assert control.pending_question["options"] == [
        {"id": "opt-1", "label": "Seal kit A", "kind": ""},
        {"id": "opt-2", "label": "Seal kit B", "kind": ""},
    ]

    # Even with EVERY grant revoked, a non-focus card stays verbatim.
    grants[user.username] = []
    with _grant_settings(grants):
        replay, replay_capture = _run_turn(
            principal, thread.pk, workflow, key=key, correlation_id=correlation_id
        )
    assert replay.replayed is True
    assert len(workflow.calls) == 1
    assert replay.pending_question == control.pending_question, (
        "non-focus card was not verbatim on the result"
    )
    stored_record = _stored_question_record(thread)
    replay_question = _question_events(replay_capture)
    assert len(replay_question) == 1
    assert replay_question[0].to_dict() == stored_record, (
        "non-focus replay QUESTION body was not verbatim"
    )


# --------------------------------------------------------------------------- #
# Malformed recorded identities hide the whole card (never normalized).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_id", MALFORMED_IDS, ids=lambda i: f"bad_id_{i!r}")
def test_malformed_recorded_option_identity_hides_whole_card(bad_id):
    user, client1, client2, rows = _two_client_fixtures()
    good = _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0])
    options = _producer_options([good])
    options.append({"id": bad_id, "label": "Seal kit X", "ref": {"kind": "machine", "id": 999999}})
    thread = _thread_for(user, f"RV02Q malformed {bad_id!r}")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow = _ProviderlessQuestionWorkflow(options=options)
    principal = _principal(user)

    with _grant_settings(grants):
        result, _capture = _run_turn(
            principal,
            thread.pk,
            workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert result.pending_question is None, (
        f"malformed recorded identity {bad_id!r} did not hide the whole card"
    )
    stored_record = _stored_question_record(thread)
    assert bad_id in json.dumps(stored_record), (
        "the malformed card must stay stored byte-verbatim (storage is never rewritten)"
    )


# --------------------------------------------------------------------------- #
# Complete-path wiring: a fresh turn whose grant is revoked mid-turn hides the
# result card while storage stays verbatim. The live QUESTION event of a fresh
# turn belongs to the separate RV02 live-arming slice and is not asserted.
# --------------------------------------------------------------------------- #


def test_complete_path_hides_result_card_after_mid_turn_revocation():
    user, client1, client2, rows = _two_client_fixtures()
    targets = [
        _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0]),
        _target("machine", rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1]),
    ]
    thread = _thread_for(user, "RV02Q complete revoke")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow = _ProviderlessQuestionWorkflow(
        options=_producer_options(targets),
        mid_turn=lambda: grants.__setitem__(user.username, [{"client_id": client1.pk}]),
    )
    with _grant_settings(grants):
        result, _capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert len(workflow.calls) == 1
    assert result.replayed is False
    assert result.pending_question is None, (
        "complete-path result card leaked after mid-turn grant revocation"
    )
    stored_record = _stored_question_record(thread)
    assert targets[1][2] in json.dumps(stored_record), (
        "stored QUESTION record must stay byte-verbatim (storage is never rewritten)"
    )


# --------------------------------------------------------------------------- #
# Thrown resolver fault: fail closed, with every other result field preserved
# (real asdict before/after the observed original projector) and the resolver
# fault actually observed (a raised exception only — never a faked verdict).
# --------------------------------------------------------------------------- #


def test_question_projection_fails_closed_on_thrown_resolver_bridge_fault(monkeypatch):
    frames: list = []
    _observe_projection(monkeypatch, frames)

    user, client1, client2, rows = _two_client_fixtures()
    targets = [
        _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0]),
        _target("machine", rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1]),
    ]
    thread = _thread_for(user, "RV02Q resolver fault")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    fault_calls: list = []

    def _inject_thrown_fault():
        def _boom(_user, _candidate):
            fault_calls.append((_user, _candidate))
            raise RuntimeError("injected RV02 resolver bridge fault")

        monkeypatch.setattr(focus_resolution, "resolve_focus_candidate", _boom)

    ok_workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    fault_workflow = _ProviderlessQuestionWorkflow(
        options=_producer_options(targets), mid_turn=_inject_thrown_fault
    )
    with _grant_settings(grants):
        ok, _ok_capture = _run_turn(
            _principal(user),
            thread.pk,
            ok_workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
        faulted, _fault_capture = _run_turn(
            _principal(user),
            thread.pk,
            fault_workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )

    assert ok.pending_question is not None, "the allowed control card was not positively projected"
    assert fault_calls, "the injected resolver fault was never observed (resolver not consulted)"
    assert faulted.pending_question is None, (
        "thrown resolver fault disclosed the structured question card"
    )
    assert faulted.replayed is False
    assert faulted.response_state == ok.response_state == "complete"
    assert faulted.message == ok.message, "projection fault mutated the result text"
    assert len(frames) == 2, f"expected both projections observed, got {len(frames)}"
    ok_before, ok_after = frames[0]
    _assert_other_fields_identical(ok_before, ok_after, exempt={"pending_question"})
    fault_before, fault_after = frames[1]
    assert isinstance(fault_before["pending_question"], dict)
    assert fault_after["pending_question"] is None
    _assert_other_fields_identical(fault_before, fault_after, exempt={"pending_question"})


# --------------------------------------------------------------------------- #
# The thread's LATER current focus never substitutes for the recorded options.
# --------------------------------------------------------------------------- #


def test_later_current_focus_does_not_substitute_for_withheld_card():
    user, client1, client2, rows = _two_client_fixtures()
    targets = [
        _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0]),
        _target("machine", rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1]),
    ]
    thread = _thread_for(user, "RV02Q later focus")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    key = f"rv02q-{uuid.uuid4().hex}"
    correlation_id = str(uuid.uuid4())
    principal = _principal(user)
    with _grant_settings(grants):
        control, _control_capture = _run_turn(
            principal, thread.pk, workflow, key=key, correlation_id=correlation_id
        )
    assert control.pending_question is not None

    # The thread's current focus switches to the still-readable target...
    with _grant_settings(grants):
        switched = _repository_for(user).set_focus(
            thread.pk,
            {"kind": "machine", "id": targets[0][1]},
            expected_focus_version=0,
        )
    assert switched["focus"]["id"] == targets[0][1]
    # ...and then the second option's target grant is revoked.
    grants[user.username] = [{"client_id": client1.pk}]
    with _grant_settings(grants):
        replay, replay_capture = _run_turn(
            principal, thread.pk, workflow, key=key, correlation_id=correlation_id
        )
    assert replay.replayed is True
    assert len(workflow.calls) == 1
    assert replay.pending_question is None, (
        "later current focus substituted for the withheld recorded question card"
    )
    replay_question = _question_events(replay_capture)
    assert len(replay_question) == 1
    assert replay_question[0].data == {}
    recorded_labels = [t[2] for t in targets]
    for label in recorded_labels:
        assert label not in json.dumps(replay_question[0].to_dict()), (
            "recorded option identity leaked on replay"
        )
