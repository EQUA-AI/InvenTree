"""Decision transitions, conservative echo refusal and exact focus binding."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from ai.core.decisions.coordinator import (
    DecisionConflict,
    DecisionCoordinator,
    estimated_playback_seconds,
)
from ai.core.decisions.models import DecisionState
from ai.core.decisions.store import DecisionStoreUnavailable, InMemoryPendingDecisionStore
from ai.core.tests.test_decision_store import T0, _decision
from ai.core.voice.speech import spoken_summary_hash


@pytest.fixture
def harness():
    now = [T0]
    store = InMemoryPendingDecisionStore()
    source = SimpleNamespace(state="proposed", target_version=1, preview_hash="a" * 64)
    adapter = SimpleNamespace(read=lambda *_args: source, reject=lambda *_args: None)
    coordinator = DecisionCoordinator(store=store, adapter=adapter, now=lambda: now[0])
    decision = _decision(
        required_phrase=None,
        allowed_responses=("confirm hold", "yes", "change that", "cancel"),
        spoken_summary="Say confirm hold or yes, change that, or cancel.",
        utterance_id=None,
    )
    store.save("7", decision)
    actor = SimpleNamespace(user_pk="7")

    def run(text, **kwargs):
        current = store.read("7")
        return coordinator.resolve(
            text,
            actor=actor,
            session_id="session-1",
            thread_id="7",
            nonce="next",
            context=kwargs.pop("context", current.to_public_dict()),
            **kwargs,
        )

    return SimpleNamespace(c=coordinator, store=store, source=source, actor=actor, now=now, run=run)


def complete(h):
    d = h.store.read("7")
    d = h.c.bind_playback(
        d,
        utterance_id="u",
        spoken_text=d.spoken_summary,
        spoken_hash=spoken_summary_hash(d.spoken_summary),
    )
    d = h.c.playback(
        d,
        event="playback-started",
        sequence=d.sequence,
        utterance_id="u",
        spoken_hash=d.spoken_summary_hash,
    )
    h.now[0] += timedelta(seconds=10)
    return h.c.playback(
        d,
        event="playback-completed",
        sequence=d.sequence,
        utterance_id="u",
        spoken_hash=d.spoken_summary_hash,
    )


def test_clock_starts_at_completion_and_has_three_refreshes(harness):
    h = harness
    h.now[0] += timedelta(seconds=20)
    d = complete(h)
    assert d.expires_at == T0 + timedelta(seconds=150)
    for i in range(3):
        h.now[0] += timedelta(seconds=20)
        result = h.run("repeat")
        assert result.decision.review_turns == i + 1
    expiry = result.decision.expires_at
    assert h.run("read more").decision.expires_at == expiry
    h.now[0] += timedelta(seconds=20)
    assert h.run("repeat").decision.expires_at == expiry
    assert h.store.read("7").state == DecisionState.PRESENTED


@pytest.mark.parametrize("text", ["yes", "confirm hold", "cancel"])
def test_echo_keeps_decision_without_refresh_or_execution(harness, text):
    h = harness
    d = h.store.read("7")
    h.c.bind_playback(
        d,
        utterance_id="u",
        spoken_text=d.spoken_summary,
        spoken_hash=spoken_summary_hash(d.spoken_summary),
    )
    before = h.store.read("7")
    result = h.run(text, provider_active=True)
    assert result.event == "echo_refused"
    assert result.decision == before
    assert "confirm hold" not in result.spoken


def test_early_client_done_cannot_shorten_echo_window(harness):
    h = harness
    d = complete(h)
    # Conservatively estimated playback plus tail is independent of a callback.
    h.now[0] = T0 + timedelta(seconds=10.5)
    assert h.c.echo_window(d)
    assert h.run("yes").event == "echo_refused"


@pytest.mark.parametrize("text", ["no", "cancel that"])
def test_real_declines_are_not_blocked_by_provider_activity(harness, text):
    result = harness.run(text, provider_active=True)
    assert result.decision.state == DecisionState.DISARMED


def test_unrelated_disarms_and_routes(harness):
    result = harness.run("How many resistors do we have?")
    assert result.route_normally
    assert result.decision.state == DecisionState.DISARMED


def test_defer_preserves_clock_and_history_refreshes(harness):
    h = harness
    d = complete(h)
    assert h.run("not yet").decision.expires_at == d.expires_at
    h.now[0] += timedelta(seconds=20)
    assert h.run("what changed").decision.review_turns == 1


def test_hard_ceiling_never_pauses(harness):
    h = harness
    d = complete(h)
    h.now[0] = T0 + timedelta(seconds=300)
    assert h.run("repeat").decision.state == DecisionState.EXPIRED
    assert h.store.read("7").expires_at == d.expires_at


@pytest.mark.parametrize(
    "field,value",
    [("decision_id", "old"), ("sequence", 0), ("revision", 4), ("preview_hash", "old")],
)
def test_stale_focus_never_confirms(harness, field, value):
    h = harness
    complete(h)
    h.now[0] += timedelta(seconds=20)
    context = h.store.read("7").to_public_dict()
    context[field] = value
    with pytest.raises(DecisionConflict):
        h.run("confirm hold", context=context)


def test_source_touch_invalidates_voice(harness):
    h = harness
    h.source.state = "executed"
    with pytest.raises(DecisionConflict):
        h.run("yes")
    assert h.store.read("7").state == DecisionState.DISARMED


def test_playback_callbacks_bind_sequence_utterance_hash_and_order(harness):
    h = harness
    d = h.store.read("7")
    d = h.c.bind_playback(
        d,
        utterance_id="u",
        spoken_text=d.spoken_summary,
        spoken_hash=spoken_summary_hash(d.spoken_summary),
    )
    valid = {
        "event": "playback-started",
        "sequence": d.sequence,
        "utterance_id": "u",
        "spoken_hash": d.spoken_summary_hash,
    }
    for field, value in [
        ("sequence", 0),
        ("utterance_id", "other"),
        ("spoken_hash", "bad"),
        ("event", "playback-completed"),
    ]:
        with pytest.raises(DecisionConflict):
            h.c.playback(d, **{**valid, field: value})
    started = h.c.playback(d, **valid)
    with pytest.raises(DecisionConflict):
        h.c.playback(
            started,
            event="playback-completed",
            sequence=started.sequence,
            utterance_id="u",
            spoken_hash=started.spoken_summary_hash,
        )
    assert h.store.read("7").state == DecisionState.PRESENTED


def test_install_refuses_late_resolver_and_corruption_is_not_absence(harness):
    h = harness
    old = h.store.read("7")
    assert h.store.install(replace(old, decision_id="new"), old)
    assert not h.store.install(replace(old, decision_id="late"), old)
    h.store._records["7"] = {"invalid": True}
    with pytest.raises(DecisionStoreUnavailable):
        h.store.read("7")


def test_estimate_counts_identifiers_separately():
    assert estimated_playback_seconds("work order 104") > estimated_playback_seconds(
        "work order ABC"
    )
