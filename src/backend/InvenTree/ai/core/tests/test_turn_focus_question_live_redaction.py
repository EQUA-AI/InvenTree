"""RV02 live slice: current-actor projection of NEW outward QUESTION events.

A fresh turn that arms a ``focus_selection`` question emits a live ``QUESTION``
wire event to the outward emitter while the per-turn ``_EventCapture`` stores
the ORIGINAL event durably. This module covers exactly that live/new arming
surface: the outward live QUESTION card is projected on the CURRENT acting
viewer (real Django user rehydration plus the existing authorized readers
behind ``resolve_focus_candidate`` via the accepted RV02-A seam
``_recorded_focus_readable_now``). If EVERY recorded option's literal id token
(``focus:<kind>:<id>``) is not readable right now — unreadable, missing or
malformed option, or an actor-lookup/resolver failure — the WHOLE structured
card body is withheld from the outward live event (envelope UUID/timestamp/
thread/run/agent/type preserved). The DURABLE side keeps the stored canonical
QUESTION record, its body/options/IDs/hash and the pending slot byte-verbatim;
no workflow/effect/speech runs twice and every non-QUESTION event is forwarded
unchanged. Fully readable cards and non-``focus_selection`` cards stay
VERBATIM. Labels of revoked options are never replaced by the thread's later
current focus, and the acting viewer being the question's owner is never an
authority fallback: authorization is the current viewer over each recorded
option identity.

The accepted result/replay/history projections are untouched; the fresh-turn
result card is additionally asserted as a regression control only.

Real ORM rows, the real ``ThreadRepository``/intake/``_process_turn`` path, the
real ``build_pending_record``/``_arm_pending_question`` producer wiring and the
real authorized readers throughout (no resolver or reader mocking; the only
monkeypatch is a thrown resolver fault in the separately labeled
fault-injection section, and it targets the real resolver seam only).
Providerless disclosure: no provider channel is installed; the workflow is a
local generator standing in for the provider, exactly as the existing
normalized-turn service tests disclose. The question-card producer surface
ships behind the documented env-backed flag (``AIMMS_FEATURE_QUESTION_CARDS``,
``get_settings`` cache rebuilt at setup).

Reusable ORM fixtures/harness are imported from the accepted RV02 result+
replay tracer ``test_turn_focus_question_redaction`` (frozen, unchanged).
"""

from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

# Documented dark-flag producer surface: env-backed cached Pydantic config, so
# the flag is enabled before setup and ``_grant_settings`` rebuilds the cache.
os.environ["AIMMS_FEATURE_QUESTION_CARDS"] = "true"

import django

django.setup()

import pytest  # noqa: E402
from ai.core.focus.commands import FOCUS_QUESTION_SOURCE  # noqa: E402
from ai.core.streaming import EventType  # noqa: E402
from ai.core.tests.test_turn_focus_question_redaction import (  # noqa: E402
    FOCUS_KINDS,
    MALFORMED_IDS,
    NON_FOCUS_SOURCE,
    OPTION_PAIRS,
    QUESTION_TEXT,
    _grant_settings,
    _pending_slot,
    _principal,
    _producer_options,
    _ProviderlessQuestionWorkflow,
    _question_events,
    _repository_for,
    _run_turn,
    _stored_events,
    _stored_question_record,
    _target,
    _thread_for,
    _two_client_fixtures,
    _wire_options,
    _withheld_view,
)

# --------------------------------------------------------------------------- #
# Local wire observability over the accepted harness collectors.
# --------------------------------------------------------------------------- #


def _other_event_shapes(capture) -> list:
    """(event type, body key tuple) of every outward non-QUESTION event.

    The focus echo (``aimms.threadFocus``) is a separate channel and the
    live-slice contract only requires every OTHER workflow/text/lifecycle
    event to be forwarded unchanged, so volatile envelope values are excluded
    by comparing types and body-key shapes across runs.
    """
    shapes = []
    for event in capture.events:
        data = getattr(event, "data", None) or {}
        if event.event_type == EventType.QUESTION:
            continue
        if event.event_type == EventType.CUSTOM and data.get("name") == "aimms.threadFocus":
            continue
        shapes.append((event.event_type, tuple(sorted(data.keys()))))
    return shapes


def _live_question(capture):
    events = _question_events(capture)
    assert len(events) == 1, f"expected exactly one live QUESTION wire event, got {len(events)}"
    return events[0]


# --------------------------------------------------------------------------- #
# The tracer: readable live control, then a genuine grant revocation before a
# fresh NEW arming — outward WHOLE card withheld, durable original untouched.
# --------------------------------------------------------------------------- #


def test_live_question_card_withheld_after_grant_revocation_while_storage_stays_original():
    """Readable -> grant-revoked live/new-arm QUESTION on the CURRENT viewer.

    Turn one (control, full grants): the live outward QUESTION event is
    byte-verbatim with the stored record. Then the actor's grant for the
    SECOND option's client is revoked (all rows and ownership stay extant) and
    a FRESH turn on a NEW thread must arm the question again while the outward
    live QUESTION keeps ONLY its envelope (WHOLE structured body withheld),
    the stored canonical QUESTION record/pending slot keep the full original
    body/options/IDs, the fresh result card is still withheld, and every other
    outward event shape plus exactly-once execution/slot invariants hold.
    """
    user, client1, client2, rows = _two_client_fixtures()
    targets = [
        _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0]),
        _target("machine", rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1]),
    ]
    label2 = targets[1][2]
    principal = _principal(user)
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}

    # --- control: fully readable live card is projected verbatim everywhere ---
    thread1 = _thread_for(user, "RV02Q live control")
    workflow1 = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        control, control_capture = _run_turn(
            principal,
            thread1.pk,
            workflow1,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    live1 = _live_question(control_capture)
    stored1 = _stored_question_record(thread1)
    assert live1.to_dict() == stored1, "readable live QUESTION must stay verbatim with storage"
    assert live1.data != {} and stored1["options"] == _wire_options(targets)
    assert control.pending_question is not None, "readable control result card missing"

    # --- revoke ONE option's target grant (rows and ownership stay extant) ---
    grants[user.username] = [{"client_id": client1.pk}]
    thread2 = _thread_for(user, "RV02Q live revoked")
    workflow2 = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        revoked, revoked_capture = _run_turn(
            principal,
            thread2.pk,
            workflow2,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )

    # Exactly-once: one fresh workflow execution, one live event, one record.
    assert len(workflow2.calls) == 1
    assert len(_question_events(revoked_capture)) == 1
    assert len([r for r in _stored_events(thread2) if r.get("type") == "QUESTION"]) == 1

    # --- RED: the live outward QUESTION body must NOT leak ---
    live2 = _live_question(revoked_capture)
    stored2 = _stored_question_record(thread2)
    wire2 = live2.to_dict()
    assert live2.data == {}, (
        "live outward QUESTION structured card body leaked after option grant revocation"
    )
    assert label2 not in json.dumps(wire2), "revoked option label leaked in the live QUESTION body"
    assert wire2.get("type") == "QUESTION"

    # Same envelope identity/timestamp/thread/run/agent/type as the stored one.
    assert _withheld_view(stored2) == wire2, (
        "live outward QUESTION envelope diverged from the stored original event"
    )

    # Durable original QUESTION/pending/options/IDs/hash survive byte-verbatim.
    assert stored2["question_text"] == QUESTION_TEXT
    assert stored2["source"] == FOCUS_QUESTION_SOURCE
    assert stored2["options"] == _wire_options(targets), "stored recorded options were rewritten"
    assert stored2["interrupt_id"], "stored question identity/hash did not survive"
    assert label2 in json.dumps(stored2), (
        "stored QUESTION body must keep the revoked option verbatim"
    )
    slot2 = _pending_slot(thread2.pk)
    assert isinstance(slot2, dict) and slot2["interrupt_id"] == stored2["interrupt_id"]
    assert [o["label"] for o in slot2["options"]] == [t[2] for t in targets], (
        "pending slot did not keep the recorded options"
    )

    # The accepted fresh-result projection still hides the card (regression).
    assert revoked.pending_question is None, (
        "fresh result card leaked after option grant revocation"
    )
    assert revoked.replayed is False

    # Non-QUESTION workflow/text/lifecycle events forwarded unchanged.
    assert _other_event_shapes(revoked_capture) == _other_event_shapes(control_capture), (
        "non-QUESTION outward events changed alongside the live projection"
    )
    assert any(
        event.event_type == EventType.WORKFLOW_STARTED
        and event.data == {"workflow_id": "focus_clarify"}
        for event in revoked_capture.events
    ), "workflow/slot invariant (WORKFLOW_STARTED focus_clarify) was not preserved"


# --------------------------------------------------------------------------- #
# Positive readable controls per supported recorded kind (distinct PKs).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", FOCUS_KINDS, ids=lambda k: f"readable_{k}")
def test_live_question_card_verbatim_for_readable_control_per_kind(kind):
    """Both recorded options of one supported kind, all grants: VERBATIM live."""
    user, client1, client2, rows = _two_client_fixtures()
    first = _target(kind, rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0])
    second = _target(kind, rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1])
    targets = [first, second]
    assert first[1] != second[1], "the two option identities must be distinct PKs"
    thread = _thread_for(user, f"RV02Q live readable {kind}")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    live = _live_question(capture)
    stored = _stored_question_record(thread)
    assert live.to_dict() == stored, f"readable {kind} live QUESTION was not verbatim"
    assert live.data != {} and live.data["options"] == _wire_options(targets)
    assert result.pending_question is not None, "readable control result card missing"


# --------------------------------------------------------------------------- #
# All three kinds across both option positions (distinct card vs parent PKs):
# one unreadable recorded option hides the WHOLE live card.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind_pair", OPTION_PAIRS, ids=lambda p: f"options_{p[0]}_{p[1]}")
def test_live_question_card_whole_withheld_for_mixed_recorded_option_kinds(kind_pair):
    """Revoked second option's grant before the fresh arm hides the whole card."""
    user, client1, _client2, rows = _two_client_fixtures()
    kind1, kind2 = kind_pair
    first = _target(kind1, rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0])
    second = _target(kind2, rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1])
    targets = [first, second]
    assert first[1] != second[1], "the two option identities must be distinct PKs"
    thread = _thread_for(user, f"RV02Q live kinds {kind1}-{kind2}")
    grants = {user.username: [{"client_id": client1.pk}]}  # second option's client revoked
    workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    live = _live_question(capture)
    stored = _stored_question_record(thread)
    assert live.data == {}, f"live QUESTION body leaked for mixed recorded kinds {kind1}/{kind2}"
    assert _withheld_view(stored) == live.to_dict()
    assert second[2] not in json.dumps(live.to_dict())
    assert second[2] in json.dumps(stored), "stored original must keep the revoked option verbatim"
    assert result.pending_question is None


# --------------------------------------------------------------------------- #
# Non-focus cards are not structured focus cards: VERBATIM with zero grants.
# --------------------------------------------------------------------------- #


def test_live_non_focus_question_card_stays_verbatim_without_any_grants():
    user, _client1, _client2, _rows = _two_client_fixtures()
    options = [
        {"id": "opt-1", "label": "Seal kit A"},
        {"id": "opt-2", "label": "Seal kit B"},
    ]
    thread = _thread_for(user, "RV02Q live non-focus")
    grants = {user.username: []}  # every grant revoked: still never a focus card
    workflow = _ProviderlessQuestionWorkflow(options=options, source=NON_FOCUS_SOURCE)
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    live = _live_question(capture)
    stored = _stored_question_record(thread)
    assert live.to_dict() == stored, "non-focus live QUESTION body was not verbatim"
    assert live.data.get("source") == NON_FOCUS_SOURCE
    assert result.pending_question is not None, "non-focus result card must stay verbatim"
    assert result.pending_question["source"] == NON_FOCUS_SOURCE


# --------------------------------------------------------------------------- #
# Malformed recorded identities hide the whole live card (never normalized).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_id", MALFORMED_IDS, ids=lambda i: f"bad_id_{i!r}")
def test_live_malformed_recorded_option_identity_hides_whole_card(bad_id):
    user, client1, client2, rows = _two_client_fixtures()
    good = _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0])
    options = _producer_options([good])
    options.append({"id": bad_id, "label": "Seal kit X", "ref": {"kind": "machine", "id": 999999}})
    thread = _thread_for(user, f"RV02Q live malformed {bad_id!r}")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow = _ProviderlessQuestionWorkflow(options=options)
    with _grant_settings(grants):
        result, capture = _run_turn(
            _principal(user),
            thread.pk,
            workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    live = _live_question(capture)
    stored = _stored_question_record(thread)
    assert live.data == {}, (
        f"malformed recorded identity {bad_id!r} did not hide the whole live card"
    )
    assert _withheld_view(stored) == live.to_dict()
    assert bad_id in json.dumps(stored), (
        "the malformed card must stay stored byte-verbatim (storage is never rewritten)"
    )
    assert result.pending_question is None


# --------------------------------------------------------------------------- #
# Fault-injection section (SEPARATELY LABELED): a thrown fault at the REAL
# resolver seam fails closed; no projector/authorization verdict is faked.
# --------------------------------------------------------------------------- #


def test_live_question_projection_fails_closed_on_thrown_resolver_fault(monkeypatch):
    from ai.core.focus import resolution as focus_resolution

    user, client1, client2, rows = _two_client_fixtures()
    targets = [
        _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0]),
        _target("machine", rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1]),
    ]
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    principal = _principal(user)
    fault_calls: list = []

    def _boom(_user, _candidate):
        fault_calls.append((_user, _candidate))
        raise RuntimeError("injected RV02 live resolver bridge fault")

    ok_thread = _thread_for(user, "RV02Q live fault control")
    ok_workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        ok, _ok_capture = _run_turn(
            principal,
            ok_thread.pk,
            ok_workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert ok.pending_question is not None, "the allowed control card was not positively projected"

    monkeypatch.setattr(focus_resolution, "resolve_focus_candidate", _boom)
    fault_thread = _thread_for(user, "RV02Q live resolver fault")
    fault_workflow = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        faulted, fault_capture = _run_turn(
            principal,
            fault_thread.pk,
            fault_workflow,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    assert fault_calls, "the injected resolver fault was never observed (resolver not consulted)"
    live = _live_question(fault_capture)
    stored = _stored_question_record(fault_thread)
    assert live.data == {}, "thrown resolver fault disclosed the live structured question card"
    assert _withheld_view(stored) == live.to_dict()
    assert faulted.pending_question is None, (
        "thrown resolver fault disclosed the structured question card"
    )
    assert faulted.replayed is False
    assert faulted.response_state == ok.response_state == "complete"
    assert faulted.message == ok.message, "projection fault mutated the result text"


# --------------------------------------------------------------------------- #
# No owner-authority fallback and no later-current-focus substitution.
# --------------------------------------------------------------------------- #


def test_live_hidden_card_never_substituted_by_owner_or_later_focus():
    user, client1, client2, rows = _two_client_fixtures()
    targets = [
        _target("machine", rows["machine"][0], rows["work_order"][0], rows["kanban_card"][0]),
        _target("machine", rows["machine"][1], rows["work_order"][1], rows["kanban_card"][1]),
    ]
    labels = [t[2] for t in targets]
    principal = _principal(user)

    # (a) the acting viewer OWNS this question and loses every grant mid-turn:
    #     owner authority is never a fallback.
    thread_a = _thread_for(user, "RV02Q live owner fallback")
    grants = {user.username: [{"client_id": client1.pk}, {"client_id": client2.pk}]}
    workflow_a = _ProviderlessQuestionWorkflow(
        options=_producer_options(targets),
        mid_turn=lambda: grants.__setitem__(user.username, []),
    )
    with _grant_settings(grants):
        result_a, capture_a = _run_turn(
            principal,
            thread_a.pk,
            workflow_a,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    live_a = _live_question(capture_a)
    assert live_a.data == {}, "owner-authority fallback disclosed the live question card"
    assert result_a.pending_question is None
    for label in labels:
        assert label not in json.dumps(live_a.to_dict())
        assert label in json.dumps(_stored_question_record(thread_a))

    # (b) the thread's LATER current focus (a readable target) never
    #     substitutes for the withheld recorded options.
    thread_b = _thread_for(user, "RV02Q live later focus")
    grants[user.username] = [{"client_id": client1.pk}]
    with _grant_settings(grants):
        switched = _repository_for(user).set_focus(
            thread_b.pk,
            {"kind": "machine", "id": targets[0][1]},
            expected_focus_version=0,
        )
    assert switched["focus"]["id"] == targets[0][1]
    workflow_b = _ProviderlessQuestionWorkflow(options=_producer_options(targets))
    with _grant_settings(grants):
        result_b, capture_b = _run_turn(
            principal,
            thread_b.pk,
            workflow_b,
            key=f"rv02q-{uuid.uuid4().hex}",
            correlation_id=str(uuid.uuid4()),
        )
    live_b = _live_question(capture_b)
    stored_b = _stored_question_record(thread_b)
    assert live_b.data == {}, "later current focus substituted for the withheld live card"
    assert _withheld_view(stored_b) == live_b.to_dict()
    for label in labels:
        assert label not in json.dumps(live_b.to_dict()), "recorded option identity leaked live"
        assert label in json.dumps(stored_b), "stored original must keep every recorded option"
    assert result_b.pending_question is None
