"""B3: route ownership and persisted playback bindings are independent of assent."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ai.core.decisions.coordinator import DecisionCoordinator
from ai.core.decisions.routes import DecisionActionRequest
from ai.core.decisions.store import InMemoryPendingDecisionStore
from ai.core.tests.test_decision_store import T0, _decision
from ai.core.tests.test_realtime_session_api import _principal, _run, _settings, _user
from ai.core.tests.test_voice_status_phrases import _session_for
from ai.core.voice import routes
from django.core.management import call_command
from fastapi import HTTPException
from voice.models import VoiceSession, VoiceUtterance
from voice.services import realtime


@pytest.fixture(scope="module", autouse=True)
def database():
    call_command("migrate", verbosity=0, interactive=False)


def endpoint(name):
    return next(route.endpoint for route in routes.router.routes if route.name == name)


@pytest.fixture
def route_setup():
    actor = _principal(_user())
    settings = _settings()
    payload = _session_for(actor, settings)
    session = VoiceSession.objects.get(pk=payload["id"])
    utterance = realtime.persist_utterance(
        session=session, utterance_type="prompt", spoken_summary="Confirm hold or cancel."
    )
    source = SimpleNamespace(state="proposed", target_version=1, preview_hash="a" * 64)
    coordinator = DecisionCoordinator(
        store=InMemoryPendingDecisionStore(),
        adapter=SimpleNamespace(read=lambda *_args: source),
        now=lambda: T0,
    )
    d = _decision(
        thread_id=session.thread_id,
        session_id=str(session.pk),
        actor_user_pk=str(actor.user_pk),
        utterance_id=None,
    )
    coordinator.store.save(session.thread_id, d)
    d = coordinator.bind_playback(
        d,
        utterance_id=utterance.pk,
        spoken_text=utterance.spoken_summary,
        spoken_hash=utterance.spoken_summary_hash,
    )
    request = DecisionActionRequest(
        **{
            key: d.to_public_dict()[key]
            for key in ("decision_id", "sequence", "revision", "preview_hash")
        },
        utterance_id=str(utterance.pk),
        spoken_summary_hash=utterance.spoken_summary_hash,
    )
    with patch("ai.core.decisions.routes.get_coordinator", return_value=coordinator):
        yield actor, settings, session, coordinator, request


def test_foreign_session_cannot_read_or_decide(route_setup):
    _, settings, session, _, request = route_setup
    foreign = _principal(_user())
    with pytest.raises(HTTPException) as exc:
        _run(foreign, lambda: endpoint("read_decision")(str(session.pk)), settings)
    assert exc.value.status_code in (403, 404)
    with pytest.raises(HTTPException):
        _run(
            foreign,
            lambda: endpoint("act_on_decision")(str(session.pk), "confirm", request),
            settings,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": 0},
        {"utterance_id": "00000000-0000-0000-0000-000000000000"},
        {"utterance_id": "not-a-uuid"},
        {"spoken_summary_hash": "wrong"},
    ],
)
def test_stale_or_mismatched_callback_is_refused(route_setup, changes):
    actor, settings, session, coordinator, request = route_setup
    with pytest.raises(HTTPException) as exc:
        _run(
            actor,
            lambda: endpoint("act_on_decision")(
                str(session.pk), "playback-started", request.model_copy(update=changes)
            ),
            settings,
        )
    assert exc.value.status_code == 409
    assert coordinator.store.read(session.thread_id).state == "presented"


def test_valid_start_marks_only_delivery_and_early_completion_refuses(route_setup):
    actor, settings, session, coordinator, request = route_setup
    result = _run(
        actor,
        lambda: endpoint("act_on_decision")(str(session.pk), "playback-started", request),
        settings,
    )
    assert result["pending_decision"]["delivery_state"] == "playing"
    assert VoiceUtterance.objects.get(pk=request.utterance_id).playback_state == "playing"
    assert not session.operations.exists()
    request = request.model_copy(update={"sequence": result["pending_decision"]["sequence"]})
    with pytest.raises(HTTPException):
        _run(
            actor,
            lambda: endpoint("act_on_decision")(str(session.pk), "playback-completed", request),
            settings,
        )
    assert coordinator.store.read(session.thread_id).playback_completed_at is None
