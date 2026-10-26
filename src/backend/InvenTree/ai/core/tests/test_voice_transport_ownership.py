"""Live-regression guard: a voice HTTP request must reach its provider owner."""

import uuid
from unittest.mock import patch

import pytest
from ai.core.tests.focus_test_db import ensure_island_tables
from ai.core.tests.test_realtime_session_api import _principal, _run, _settings, _user
from ai.core.tests.test_voice_status_phrases import _session_for
from ai.core.voice import routes
from fastapi import HTTPException
from voice.models import VoiceSession
from voice.services.ownership import claim_transport


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()


def test_other_replica_cannot_claim_or_admit_and_owner_can_end_from_any_replica():
    user = _user()
    principal, settings = _principal(user), _settings()
    created = _session_for(principal, settings)
    session = VoiceSession.objects.get(pk=created["id"])
    first, other = uuid.uuid4().hex * 2, uuid.uuid4().hex * 2
    with patch("voice.services.ownership.process_owner", return_value=first):
        assert claim_transport(session) is True
        assert claim_transport(session) is True
    with patch("voice.services.ownership.process_owner", return_value=other):
        assert claim_transport(session) is False

        with patch.object(
            routes,
            "_provider_channel_factory",
            side_effect=AssertionError("No channel on another replica"),
        ):
            for callback in [
                lambda: routes.observe_voice_lifecycle(
                    str(session.pk),
                    routes.VoiceLifecycleObservation(
                        type="response.created", response_id="foreign"
                    ),
                ),
                lambda: routes.submit_voice_turn(
                    str(session.pk),
                    routes.VoiceTurnRequest(transcript="Hello", item_id="must-not-admit"),
                ),
            ]:
                with pytest.raises(HTTPException) as error:
                    _run(principal, callback, settings)
                assert error.value.status_code == 409
                assert error.value.detail == "VOICE_SESSION_OWNER_MISMATCH"
        session.refresh_from_db()
        assert session.turn_count == 0
        payload = _run(principal, lambda: routes.get_voice_session(str(session.pk)), settings)
        assert first not in str(payload) and "transport_owner" not in payload
        ended = _run(principal, lambda: routes.end_voice_session(str(session.pk)), settings)
        assert ended["state"] == "ended"
        assert claim_transport(session) is False


def test_ownership_migration_round_trip_and_previous_writer_default():
    """Exercise the actual AddField operation on disposable native tables."""
    import importlib

    from django.apps import apps
    from django.db import connection
    from django.db.migrations.state import ProjectState

    operation = importlib.import_module(
        "voice.migrations.0012_voicesession_transport_owner"
    ).Migration.operations[0]
    latest = ProjectState.from_apps(apps)
    previous = latest.clone()
    previous.remove_field("voice", "voicesession", "transport_owner")
    old_model = previous.apps.get_model("voice", "VoiceSession")
    user = _user()
    fields = {
        "owner_id": user.pk,
        "thread_id": "migration-test",
        "scope_key": "all",
        "scope_hash": "a" * 64,
        "policy_version": "test",
    }
    with connection.schema_editor() as editor:
        operation.database_backwards("voice", editor, latest, previous)
    try:
        before = old_model.objects.create(**fields)
    finally:
        with connection.schema_editor() as editor:
            operation.database_forwards("voice", editor, previous, latest)
    assert VoiceSession.objects.get(pk=before.pk).transport_owner == ""
    # Rollback application code does not know about the new column.
    after = old_model.objects.create(**fields)
    assert VoiceSession.objects.get(pk=after.pk).transport_owner == ""
