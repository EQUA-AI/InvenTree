"""Spoken focus flow: switch, clarify, question-answer, set-aside.

End-to-end turn-path behavior on real thread rows (test-only SQLite):
a unique authorized candidate becomes confirmed focus with a server-resolved
readback; an ambiguous match arms the EXISTING persisted question mechanism
with server-owned refs; an unmatched reference and every non-confirmed
answer leave focus unchanged; a no-focus anaphor asks which target; and a
successful switch sets aside an unconfirmed pending decision through its
ACTUAL coordinator.
"""

# ruff: noqa: E402

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest
from ai.core.focus import commands
from ai.core.focus import resolution as focus_resolution
from ai.core.questions import promotion as question_promotion
from ai.core.questions.answers import QUESTION_ANSWER_POLICY_VERSION, AnswerInterpretation
from ai.core.questions.resolution import QuestionResolution
from ai.core.tests.focus_test_db import ensure_island_tables
from aichat.services.threads import ThreadRepository


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()
    yield


@pytest.fixture
def env(monkeypatch):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username=f"focus-flow-{os.urandom(4).hex()}")
    repository = ThreadRepository(user.pk, "site:main")
    thread, _ = repository.get_or_create()

    # Readers: machine N is always readable (fail-closed defaults for the
    # rest); search returns nothing unless a test installs rows.
    monkeypatch.setattr(
        focus_resolution,
        "read_machine",
        lambda _actor, ref: SimpleNamespace(pk=ref, name=f"Machine {ref}"),
    )
    monkeypatch.setattr(focus_resolution, "read_work_order", lambda _actor, _ref: None)
    monkeypatch.setattr(focus_resolution, "read_kanban_card", lambda _actor, _ref: None)
    monkeypatch.setattr(focus_resolution, "search_machines", lambda _actor, _query, _limit: [])
    monkeypatch.setattr(focus_resolution, "search_work_orders", lambda _actor, _query, _limit: [])
    monkeypatch.setattr(focus_resolution, "search_cards", lambda _actor, _query, _limit: [])

    disarmed = []

    class _Coordinator:
        def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
            disarmed.append((thread_id, reason, set_aside))
            return None

    coordinator = _Coordinator()
    monkeypatch.setattr("ai.core.decisions.coordinator.get_coordinator", lambda: coordinator)
    # The decision rail is ENABLED here so focus changes exercise the real
    # set-aside policy through the coordinator stub above.
    monkeypatch.setattr("ai.core.decisions.pipeline.enabled", lambda: True)

    class FakeService:
        def repository_factory(self, actor, trusted_context):
            return repository

        async def _call_sync(self, fn, *args, **kwargs):
            from asgiref.sync import sync_to_async

            return await sync_to_async(fn, thread_sensitive=True)(*args, **kwargs)

        def _abandon_pending_question(self, *, thread_id):
            state["abandoned"] = True

    state = {"abandoned": False}
    run = SimpleNamespace(
        content="",
        modality="text",
        thread=thread,
        turn=SimpleNamespace(pk="turn_flow_1"),
        actor=SimpleNamespace(user_pk=user.pk),
        trusted_context={},
        route=None,
        focus_snapshot=None,
        question_resolution=None,
    )
    return SimpleNamespace(
        user=user,
        repository=repository,
        thread=thread,
        run=run,
        service=FakeService(),
        disarmed=disarmed,
        state=state,
    )


def _maybe_handle(env):
    import asyncio

    return asyncio.run(commands.maybe_handle(env.service, env.run))


def _apply_answer(env):
    import asyncio

    return asyncio.run(commands.apply_question_answer(env.service, env.run))


def test_unique_switch_sets_focus_and_reads_back(env) -> None:
    env.run.content = "talk about machine 3"
    canonical = _maybe_handle(env)
    assert canonical is not None
    assert canonical["message"] == "Now talking about Machine 3."
    assert canonical["workflow_used"] == "focus_switch"
    state = env.repository.get_focus(env.thread.pk)
    assert state["focus_version"] == 1
    assert state["focus"]["id"] == 3
    # The focus turn consumed the pending-question window.
    assert env.state["abandoned"] is True


def test_switch_sets_aside_an_unconfirmed_decision_via_its_coordinator(env) -> None:
    env.run.content = "switch to machine 4"
    _maybe_handle(env)
    assert env.disarmed == [(env.thread.pk, "focus_switched", True)]


def test_unmatched_switch_leaves_focus_unchanged(env) -> None:
    env.run.content = "talk about work order 9"
    canonical = _maybe_handle(env)
    assert canonical is not None
    assert "not available" in canonical["message"] or "could not find" in canonical["message"]
    assert env.repository.get_focus(env.thread.pk) == {
        "focus": None,
        "focus_version": 0,
        "focus_unavailable": False,
    }
    assert env.disarmed == []


def test_ambiguous_switch_offers_server_owned_refs(env, monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(
        focus_resolution,
        "search_machines",
        lambda _actor, _query, _limit: [3, 4],
    )
    env.run.content = "talk about machine"

    async def _flow():
        # The proposal ContextVar is read in the SAME task context the
        # switch sets it in (exactly as the turn pipeline consumes it).
        canonical = await commands.maybe_handle(env.service, env.run)
        return canonical, question_promotion.pending_question_proposal.get()

    canonical, proposal = asyncio.run(_flow())
    assert canonical is not None
    assert proposal is not None
    assert proposal["source"] == commands.FOCUS_QUESTION_SOURCE
    refs = [option["ref"] for option in proposal["options"]]
    assert refs == [{"kind": "machine", "id": 3}, {"kind": "machine", "id": 4}]
    # Only the proposal — focus is NOT changed until a confirmed selection.
    assert env.repository.get_focus(env.thread.pk)["focus_version"] == 0


def test_truncated_candidate_page_is_never_a_false_unique(env, monkeypatch) -> None:
    """A bounded page that hit its limit must clarify, not pick one."""
    import asyncio

    monkeypatch.setattr(
        focus_resolution,
        "search_machines",
        lambda _actor, _query, _limit: [3, 4, 5, 6, 7],
    )
    env.run.content = "talk about machine"

    async def _flow():
        canonical = await commands.maybe_handle(env.service, env.run)
        return canonical, question_promotion.pending_question_proposal.get()

    canonical, proposal = asyncio.run(_flow())
    assert canonical is not None
    assert canonical["workflow_used"] == "focus_clarify"
    assert "several matches" in canonical["message"]
    assert proposal is not None
    assert env.repository.get_focus(env.thread.pk)["focus_version"] == 0


def test_candidates_are_reauthorized_before_their_labels_are_offered(env, monkeypatch) -> None:
    """A search hint is not a grant: unauthorized hits never surface labels."""
    import asyncio

    monkeypatch.setattr(
        focus_resolution,
        "search_machines",
        lambda _actor, _query, _limit: [3, 9],
    )

    def read_machine(actor, ref):
        return None if ref == 9 else SimpleNamespace(pk=ref, name=f"Machine {ref}")

    monkeypatch.setattr(focus_resolution, "read_machine", read_machine)
    env.run.content = "talk about machine"

    async def _flow():
        canonical = await commands.maybe_handle(env.service, env.run)
        return canonical, question_promotion.pending_question_proposal.get()

    canonical, proposal = asyncio.run(_flow())
    assert canonical is not None
    assert canonical["workflow_used"] == "focus_switch"
    assert canonical["message"] == "Now talking about Machine 3."
    assert proposal is None
    # The revoked candidate never appeared anywhere and never became focus.
    state = env.repository.get_focus(env.thread.pk)
    assert state["focus"]["id"] == 3


def test_revoked_focus_anaphor_gets_unavailable_clarification(env, monkeypatch) -> None:
    """A removed/revoked focus clarifies — it never routes unscoped or guesses."""
    env.repository.set_focus(env.thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    env.run.focus_snapshot = {
        "focus": {
            "schema_version": 1,
            "kind": "machine",
            "id": 3,
            "label": "Machine 3",
            "machine_id": 3,
            "work_order_id": None,
        },
        "version": 1,
    }
    monkeypatch.setattr(focus_resolution, "read_machine", lambda _actor, _ref: None)
    env.run.content = "what is its status?"
    canonical = _maybe_handle(env)
    assert canonical is not None
    assert canonical["workflow_used"] == "focus_clarify"
    assert "no longer available" in canonical["message"]
    assert "Machine 3" not in canonical["message"]


def test_switch_fails_closed_when_the_decision_cannot_be_set_aside(env, monkeypatch) -> None:
    class _Coordinator:
        def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
            raise RuntimeError("store contention")

    monkeypatch.setattr("ai.core.decisions.coordinator.get_coordinator", lambda: _Coordinator())
    env.run.content = "switch to machine 4"
    canonical = _maybe_handle(env)
    assert canonical is not None
    assert "unchanged" in canonical["message"]
    assert canonical["focus_changed"] is False
    assert env.repository.get_focus(env.thread.pk)["focus_version"] == 0
    assert getattr(env.run, "focus_changed", False) is False


def test_successful_switch_marks_focus_changed_for_refresh(env) -> None:
    env.run.content = "switch to machine 4"
    canonical = _maybe_handle(env)
    assert canonical["message"] == "Now talking about Machine 4."
    assert canonical["focus_changed"] is True
    assert getattr(env.run, "focus_changed", False) is True


def test_confirmed_focus_selection_updates_focus(env) -> None:
    env.repository.set_focus(env.thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    record = {
        "source": commands.FOCUS_QUESTION_SOURCE,
        "interrupt_id": "q1",
        "options": [
            {"id": "focus:machine:3", "label": "Machine 3", "ref": {"kind": "machine", "id": 3}},
            {"id": "focus:machine:4", "label": "Machine 4", "ref": {"kind": "machine", "id": 4}},
        ],
    }
    env.run.question_resolution = QuestionResolution(
        record=record,
        interpretation=AnswerInterpretation(
            outcome="selected",
            option_index=1,
            option_id="focus:machine:4",
            matched_by="ordinal",
            policy_version=QUESTION_ANSWER_POLICY_VERSION,
        ),
    )
    canonical = _apply_answer(env)
    assert canonical is not None
    assert canonical["message"] == "Now talking about Machine 4."
    state = env.repository.get_focus(env.thread.pk)
    assert state["focus"]["id"] == 4
    assert state["focus_version"] == 2


def test_unmatched_or_declined_answers_leave_focus_unchanged(env) -> None:
    env.repository.set_focus(env.thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    record = {
        "source": commands.FOCUS_QUESTION_SOURCE,
        "interrupt_id": "q2",
        "options": [
            {"id": "focus:machine:3", "label": "Machine 3", "ref": {"kind": "machine", "id": 3}},
        ],
    }
    for outcome, index in (("unmatched", None), ("declined", None)):
        env.run.question_resolution = QuestionResolution(
            record=record,
            interpretation=AnswerInterpretation(
                outcome=outcome,
                option_index=index,
                option_id=None,
                matched_by=None,
                policy_version=QUESTION_ANSWER_POLICY_VERSION,
            ),
        )
        assert _apply_answer(env) is None
    state = env.repository.get_focus(env.thread.pk)
    assert state["focus"]["id"] == 3
    assert state["focus_version"] == 1


def test_revoked_selection_target_cannot_update_focus(env) -> None:
    """A confirmed ref is re-authorized; revoked access stays unchanged."""
    monkey_calls = []

    def deny(actor, ref):
        monkey_calls.append(ref)
        return None

    import ai.core.focus.resolution as res

    original = res.read_machine
    res.read_machine = deny
    try:
        record = {
            "source": commands.FOCUS_QUESTION_SOURCE,
            "interrupt_id": "q3",
            "options": [
                {
                    "id": "focus:machine:9",
                    "label": "Machine 9",
                    "ref": {"kind": "machine", "id": 9},
                },
            ],
        }
        env.run.question_resolution = QuestionResolution(
            record=record,
            interpretation=AnswerInterpretation(
                outcome="selected",
                option_index=0,
                option_id="focus:machine:9",
                matched_by="ordinal",
                policy_version=QUESTION_ANSWER_POLICY_VERSION,
            ),
        )
        canonical = _apply_answer(env)
    finally:
        res.read_machine = original
    assert canonical is not None
    assert "unchanged" in canonical["message"]
    assert monkey_calls == [9]
    assert env.repository.get_focus(env.thread.pk) == {
        "focus": None,
        "focus_version": 0,
        "focus_unavailable": False,
    }


def test_no_focus_anaphor_asks_which_target(env) -> None:
    env.run.content = "what is its status?"
    canonical = _maybe_handle(env)
    assert canonical is not None
    assert canonical["workflow_used"] == "focus_clarify"
    assert "Which machine, task or work order" in canonical["message"]
    assert env.repository.get_focus(env.thread.pk)["focus_version"] == 0


def test_anaphor_with_confirmed_focus_routes_normally(env) -> None:
    env.repository.set_focus(env.thread.pk, {"kind": "machine", "id": 3}, expected_focus_version=0)
    env.run.focus_snapshot = {
        "focus": {
            "schema_version": 1,
            "kind": "machine",
            "id": 3,
            "label": "Machine 3",
            "machine_id": 3,
            "work_order_id": None,
        },
        "version": 1,
    }
    env.run.content = "what is its status?"
    assert _maybe_handle(env) is None
