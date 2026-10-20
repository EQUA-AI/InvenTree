"""Package F: persisted playback states reconcile from correlated provider
events (exact-speech playback support only).

Real ORM rows (``VoiceSession`` / ``VoiceUtterance``) on the island's own
scratch SQLite file. Tables come from the shared test-only schema helper
(``ai.core.tests.focus_test_db``) because the island migration graph is
pre-existing broken (``aichat.0042`` -> missing ``assets.0015`` island node);
that defect is reported and held, never repaired here, and this helper does
not prove production migrations. Schema failures are NOT swallowed: a failed
bootstrap errors these tests.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("django")

import django

django.setup()

from ai.core.tests.focus_test_db import ensure_island_tables  # noqa: E402
from ai.core.voice import speech_dispatch  # noqa: E402
from ai.core.voice.speech import spoken_summary_hash  # noqa: E402
from asgiref.sync import sync_to_async  # noqa: E402
from voice.models import PlaybackState, VoiceUtterance  # noqa: E402
from voice.services import realtime  # noqa: E402

ANSWER_TEXT = "Two likely causes."
ANSWER_UID = "22222222-2222-2222-2222-222222222222"
STATUS_TEXT = "Let me check that."
STATUS_UID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(scope="module", autouse=True)
def _database():
    # Not call_command("migrate"): see module docstring. No try/except --
    # a schema failure must fail these tests loudly, never skip them.
    ensure_island_tables()
    yield


def _user():
    from django.contrib.auth import get_user_model

    # Unique per call: a failed persist must never make the next fixture
    # collide on a count-derived username.
    return get_user_model().objects.create_user(
        username=f"dispatch-{uuid.uuid4().hex[:12]}", password="unused"
    )


def _session(user):
    return realtime.create_session(
        owner=user,
        thread_id="playback-reconcile",
        scope_key="test",
        policy_version="v1",
        limits=realtime.SessionLimits(),
    )


class FakeChannel:
    """Dispatch-only double: no provider, but the real dispatcher runs."""

    def __init__(self):
        self.sent = []

    async def send_control(self, payload):
        self.sent.append(payload)


def _reconcile(session, utterance_id, state):
    return realtime.reconcile_playback(session=session, utterance_id=utterance_id, state=state)


def test_reconcile_advances_only_correlated_rows():
    user = _user()
    session = _session(user)
    # A genuinely different session's rows must never be reconciled through
    # this one. The other session belongs to a different owner: the real
    # ``max_active_per_user=1`` constraint forbids a second live session for
    # the same owner, and the fixture must honor it rather than weaken it.
    other = _session(_user())
    utterance = realtime.persist_utterance(
        session=session,
        utterance_type="completed_answer",
        spoken_summary=ANSWER_TEXT,
        response_id="turn-1",
        turn_id="turn-1",
    )
    # An id from another session must never be reconciled through this one.
    assert _reconcile(other, str(utterance.pk), "requested") is None
    row = _reconcile(session, str(utterance.pk), "requested")
    assert row.playback_state == PlaybackState.REQUESTED
    row = _reconcile(session, str(utterance.pk), "done")
    assert row.playback_state == PlaybackState.DONE


def test_reconcile_is_terminal_sticky_interrupted_is_not_completed():
    user = _user()
    session = _session(user)
    utterance = realtime.persist_utterance(
        session=session,
        utterance_type="interim_status",
        spoken_summary=STATUS_TEXT,
    )
    _reconcile(session, str(utterance.pk), "canceled")
    # A late completion after the cancel must not overwrite it.
    row = _reconcile(session, str(utterance.pk), "done")
    assert row.playback_state == PlaybackState.CANCELED
    row = _reconcile(session, str(utterance.pk), "failed")
    assert row.playback_state == PlaybackState.CANCELED


def test_reconcile_never_touches_exact_speech_text_or_bindings():
    user = _user()
    session = _session(user)
    utterance = realtime.persist_utterance(
        session=session,
        utterance_type="completed_answer",
        spoken_summary=ANSWER_TEXT,
        response_id="turn-2",
        turn_id="turn-2",
    )
    before = VoiceUtterance.objects.values().get(pk=utterance.pk)
    _reconcile(session, str(utterance.pk), "requested")
    _reconcile(session, str(utterance.pk), "done")
    after = VoiceUtterance.objects.values().get(pk=utterance.pk)
    changed = {key for key in after if after[key] != before[key]}
    assert changed <= {"playback_state", "updated_at"}
    assert after["spoken_summary"] == before["spoken_summary"]
    assert after["spoken_summary_hash"] == spoken_summary_hash(ANSWER_TEXT)
    assert after["response_id"] == "turn-2"


def test_reconcile_rejects_unknown_states_without_writing():
    user = _user()
    session = _session(user)
    utterance = realtime.persist_utterance(
        session=session,
        utterance_type="completed_answer",
        spoken_summary=ANSWER_TEXT,
    )
    with pytest.raises(realtime.VoiceSessionError):
        _reconcile(session, str(utterance.pk), "playing-forever")
    row = VoiceUtterance.objects.get(pk=utterance.pk)
    assert row.playback_state == PlaybackState.PENDING


def test_dispatcher_reconciles_playback_rows_from_correlated_events():
    """End-to-end: real dispatcher + real rows + fake provider events."""

    async def run():
        user = await sync_to_async(_user, thread_sensitive=True)()
        session = await sync_to_async(_session, thread_sensitive=True)(user)
        utterance = await sync_to_async(
            lambda: realtime.persist_utterance(
                session=session,
                utterance_type="completed_answer",
                spoken_summary=ANSWER_TEXT,
                response_id="turn-3",
                turn_id="turn-3",
            ),
            thread_sensitive=True,
        )()
        channel = FakeChannel()
        dispatcher = speech_dispatch.SessionSpeechDispatcher(
            "session-orm",
            channel,
            on_playback=sync_to_async(
                lambda utterance_id, state: _reconcile(session, utterance_id, state),
                thread_sensitive=True,
            ),
        )
        record = await dispatcher.speak(
            kind="answer",
            text=ANSWER_TEXT,
            spoken_hash=spoken_summary_hash(ANSWER_TEXT),
            utterance_id=str(utterance.pk),
        )
        row = await sync_to_async(
            lambda: VoiceUtterance.objects.get(pk=utterance.pk), thread_sensitive=True
        )()
        assert record.state == "requested"
        assert row.playback_state == PlaybackState.REQUESTED
        await dispatcher.on_provider_event({"type": "response.created", "response": {"id": "r1"}})
        await dispatcher.on_provider_event({
            "type": "response.done",
            "response": {"id": "r1", "status": "completed"},
        })
        row = await sync_to_async(
            lambda: VoiceUtterance.objects.get(pk=utterance.pk), thread_sensitive=True
        )()
        assert record.state == "completed"
        assert row.playback_state == PlaybackState.DONE

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Targeted race regression: read-then-write reconciliation is not atomic.      #
# --------------------------------------------------------------------------- #
def test_reconcile_transition_is_compare_and_swap_not_read_then_write(monkeypatch):
    """A concurrent terminal event between read and write must win.

    The stale write may never overwrite a terminal row: the transition has to
    be a conditional (compare-and-swap) update, not read-check-save.
    """
    from django.utils import timezone

    user = _user()
    session = _session(user)
    utterance = realtime.persist_utterance(
        session=session,
        utterance_type="completed_answer",
        spoken_summary=ANSWER_TEXT,
        response_id="turn-cas",
        turn_id="turn-cas",
    )
    real_model = realtime.VoiceUtterance
    real_objects = real_model.objects
    fired = {"done": False}

    class _ObjectsFacade:
        def get(self, *args, **kwargs):
            row = real_objects.get(*args, **kwargs)  # the stale read
            if not fired["done"]:
                fired["done"] = True
                # The concurrent provider callback lands its terminal state
                # right AFTER our read and BEFORE our write.
                real_objects.filter(pk=row.pk).update(
                    playback_state=PlaybackState.CANCELED,
                    updated_at=timezone.now(),
                )
            return row

        def __getattr__(self, name):
            return getattr(real_objects, name)

    class _ModelFacade:
        objects = _ObjectsFacade()

        def __getattr__(self, name):
            return getattr(real_model, name)

    monkeypatch.setattr(realtime, "VoiceUtterance", _ModelFacade())
    row = realtime.reconcile_playback(session=session, utterance_id=str(utterance.pk), state="done")
    monkeypatch.undo()
    assert fired["done"], "interleaving hook did not fire"
    fresh = VoiceUtterance.objects.get(pk=utterance.pk)
    assert fresh.playback_state == PlaybackState.CANCELED
    assert row.playback_state == PlaybackState.CANCELED


def test_mark_playback_never_overwrites_a_terminal_row():
    """A slow route write after a fast provider callback must not regress
    the terminal row back to requested/playing (exact-speech playback
    support only)."""
    user = _user()
    session = _session(user)
    utterance = realtime.persist_utterance(
        session=session,
        utterance_type="interim_status",
        spoken_summary=STATUS_TEXT,
    )
    _reconcile(session, str(utterance.pk), "done")
    row = realtime.mark_playback(utterance=utterance, state="requested")
    assert row.playback_state == PlaybackState.DONE
    row = realtime.mark_playback(utterance=utterance, state="failed")
    assert row.playback_state == PlaybackState.DONE
    fresh = VoiceUtterance.objects.get(pk=utterance.pk)
    assert fresh.playback_state == PlaybackState.DONE
