"""Voice parity suite (§13.6): the six voice-specific pilot requirements.

ASR-confidence handling, scope-version binding (§13.3 pattern 10),
English-only safety refusal (Q30), evidence handoff (voice rail carries no
evidence surface), no-speech / partial-transcript discipline, and the §8.9
latency-class mapping. Route-level tests drive the real handlers under a
boundary principal with a fake normalized turn service — no provider
network access, same harness as test_realtime_session_api.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import ai.core.app as ai_app  # noqa: E402
import pytest  # noqa: E402
from ai.core.quota.slo import slo_class_for  # noqa: E402
from ai.core.turn.responses import _canonical_safety_refusal  # noqa: E402
from ai.core.voice import status_phrases  # noqa: E402
from ai.core.voice.routes import (  # noqa: E402
    VoiceSessionCreateRequest,
    VoiceTurnRequest,
    create_voice_session,
    get_voice_session,
    submit_voice_turn,
    voice_capability,
)
from ai.core.voice.transcription import (  # noqa: E402
    FINAL_EVENT_TYPE,
    PARTIAL_EVENT_TYPE,
    TranscriptEventError,
    is_partial_transcript,
    normalize_final_transcript,
)
from ai.core.voice.wire import VoiceTurnResponse  # noqa: E402
from django.core.management import call_command  # noqa: E402

from .test_realtime_session_api import (  # noqa: E402
    _expect_http,
    _FakeTurnService,
    _principal,
    _run,
    _settings,
    _user,
)


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


def _thread_for(user, thread_id: str, version: int):
    from aichat.models import ChatThread

    return ChatThread.objects.create(
        id=thread_id,
        owner=user,
        scope_key="site:pilot",
        scope_hash="0" * 64,
        analysis_scope_version=version,
    )


def _submit(principal, settings, session_id, request, fake):
    with (
        patch.object(ai_app, "get_turn_service", return_value=fake),
        patch(
            "ai.core.trusted_context.build_trusted_turn_context",
            return_value=SimpleNamespace(),
        ),
    ):
        return _run(
            principal,
            lambda: submit_voice_turn(session_id, request),
            settings,
        )


# --------------------------------------------------------------------------- #
# 1. ASR confidence                                                            #
# --------------------------------------------------------------------------- #
def test_absent_confidence_is_not_low_and_the_turn_proceeds():
    """A transcript with no confidence value is submitted, not held.

    Providers that omit the field would otherwise hold every utterance; the
    server routing default (absent -> 1.0) and the client hold-gate agree,
    and the config comment now states the same.
    """
    final = normalize_final_transcript({
        "type": FINAL_EVENT_TYPE,
        "transcript": "check the pump",
        "item_id": "i1",
    })
    assert final.confidence is None
    assert "transcription_confidence" not in final.modality_metadata()

    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    fake = _FakeTurnService()
    result = _submit(
        principal,
        settings,
        created["id"],
        VoiceTurnRequest(transcript="check the pump", item_id="i1", confidence=None),
        fake,
    )
    assert result["response_state"] == "complete"
    assert "transcription_confidence" not in fake.calls[0]["modality_metadata"]


@pytest.mark.parametrize("confidence", ["high", -0.1, 1.5])
def test_invalid_confidence_is_rejected_before_any_turn(confidence):
    with pytest.raises(TranscriptEventError):
        normalize_final_transcript({
            "type": FINAL_EVENT_TYPE,
            "transcript": "check the pump",
            "item_id": "i1",
            "confidence": confidence,
        })


def test_valid_confidence_rides_the_modality_metadata_verbatim():
    final = normalize_final_transcript({
        "type": FINAL_EVENT_TYPE,
        "transcript": "check the pump",
        "item_id": "i1",
        "confidence": 0.42,
    })
    assert final.modality_metadata()["transcription_confidence"] == pytest.approx(0.42)


def test_capability_probe_serves_the_one_confidence_floor():
    user = _user()
    settings = _settings([user.pk])
    probe = _run(_principal(user), lambda: voice_capability(), settings)
    assert probe["confidence_floor"] == settings.voice_confidence_floor


# --------------------------------------------------------------------------- #
# 2. Scope-version binding (§13.3 pattern 10)                                  #
# --------------------------------------------------------------------------- #
def test_session_binds_the_thread_scope_version_at_creation():
    user = _user()
    settings = _settings([user.pk], VOICE_LIVE_MAX_ACTIVE_SESSIONS_PER_USER=5)
    principal = _principal(user)
    _thread_for(user, "thread_parity_bind", version=3)

    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id="thread_parity_bind")),
        settings,
    )
    assert created["analysis_scope_version"] == 3

    unbound = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    assert unbound["analysis_scope_version"] == 0


def test_scope_change_refuses_the_turn_without_executing_it():
    """A material scope change after binding: typed 409, no turn, no count."""
    from aichat.models import ChatThread

    user = _user()
    settings = _settings([user.pk], VOICE_LIVE_MAX_ACTIVE_SESSIONS_PER_USER=5)
    principal = _principal(user)
    _thread_for(user, "thread_parity_stale", version=1)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id="thread_parity_stale")),
        settings,
    )

    ChatThread.objects.filter(id="thread_parity_stale").update(analysis_scope_version=2)

    fake = _FakeTurnService()
    with (
        patch.object(ai_app, "get_turn_service", return_value=fake),
        patch(
            "ai.core.trusted_context.build_trusted_turn_context",
            return_value=SimpleNamespace(),
        ),
    ):
        _expect_http(
            principal,
            lambda: submit_voice_turn(
                created["id"],
                VoiceTurnRequest(transcript="status of the inverter", item_id="i2"),
            ),
            settings,
            409,
            "VOICE_SCOPE_CHANGED",
        )
    assert fake.calls == []
    fetched = _run(principal, lambda: get_voice_session(created["id"]), settings)
    assert fetched["turn_count"] == 0

    # Acknowledgement = restart: a NEW session re-binds the current version
    # and turns proceed.
    fresh = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id="thread_parity_stale")),
        settings,
    )
    assert fresh["analysis_scope_version"] == 2
    result = _submit(
        principal,
        settings,
        fresh["id"],
        VoiceTurnRequest(transcript="status of the inverter", item_id="i3"),
        _FakeTurnService(),
    )
    assert result["response_state"] == "complete"


def test_matching_scope_version_submits_normally():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    _thread_for(user, "thread_parity_match", version=5)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id="thread_parity_match")),
        settings,
    )
    fake = _FakeTurnService()
    result = _submit(
        principal,
        settings,
        created["id"],
        VoiceTurnRequest(transcript="show the last work order", item_id="i4"),
        fake,
    )
    assert result["response_state"] == "complete"
    assert len(fake.calls) == 1


def test_scope_changed_phrase_is_allow_listed_in_every_locale():
    assert status_phrases.SCOPE_CHANGED in status_phrases.ALLOWED_STATUS_PHRASES
    for table in status_phrases.LOCALIZED_STATUS_PHRASES.values():
        localized = table.get(status_phrases.SCOPE_CHANGED)
        if localized is not None:
            assert localized in status_phrases.ALLOWED_STATUS_PHRASES


# --------------------------------------------------------------------------- #
# 3. English-only safety refusal on voice (Q30)                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("locale", ["es", "de", "fr", "xx"])
def test_voice_safety_refusal_is_english_only_in_every_locale(locale):
    """The SPOKEN safety response is entirely English — body and boundary."""
    english = _canonical_safety_refusal(voice=True, locale="en")
    localized = _canonical_safety_refusal(voice=True, locale=locale)

    assert localized.spoken_summary == english.spoken_summary
    assert localized.detailed_response == english.detailed_response
    assert localized.safety_boundary == english.safety_boundary
    assert localized.speak is True
    # The spoken summary entails the visible text exactly.
    assert localized.spoken_summary == localized.detailed_response


def test_text_safety_refusal_keeps_the_localized_boundary():
    """The deliberate asymmetry: the visible text rail may localize the
    boundary chip; only the VOICE composition is forced English."""
    from ai.core import i18n_templates as i18n

    text_es = _canonical_safety_refusal(voice=False, locale="es")
    assert text_es.safety_boundary == i18n.deterministic_template(i18n.SAFETY_BOUNDARY, "es")
    assert text_es.speak is False
    assert text_es.spoken_summary == ""


# --------------------------------------------------------------------------- #
# 4. Evidence handoff: the voice rail carries no evidence surface              #
# --------------------------------------------------------------------------- #
def test_voice_turn_wire_model_has_no_evidence_field():
    """v2 evidence reaches voice users via thread resync, never this payload."""
    for name in VoiceTurnResponse.model_fields:
        assert "evidence" not in name, name
        assert "citation" not in name, name


def test_voice_turn_payload_never_leaks_evidence_keys():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )

    class _EvidenceLadenService(_FakeTurnService):
        async def process(self, **kwargs):
            result = await super().process(**kwargs)
            # A canonical carrying analysis evidence must not widen the
            # voice payload.
            result.canonical_response["evidence_analysis"] = {"claims": ["poison"]}
            return result

    result = _submit(
        principal,
        settings,
        created["id"],
        VoiceTurnRequest(transcript="summarize the repairs", item_id="i5"),
        _EvidenceLadenService(),
    )
    assert set(result) == {
        "session_id",
        "thread_id",
        "turn_id",
        "message",
        "workflow_used",
        "response_state",
        "replayed",
        "spoken",
        "pending_question",
    }


# --------------------------------------------------------------------------- #
# 5. No-speech and partial transcripts                                         #
# --------------------------------------------------------------------------- #
#: A recorded provider event sequence: two display-only partial deltas, one
#: completed transcription, and a no-speech (empty) completion.
RECORDED_ASR_EVENTS = [
    {"type": PARTIAL_EVENT_TYPE, "transcript": "check", "item_id": "seq-1"},
    {"type": PARTIAL_EVENT_TYPE, "transcript": "check the", "item_id": "seq-1"},
    {
        "type": FINAL_EVENT_TYPE,
        "transcript": "check the pump",
        "item_id": "seq-1",
        "confidence": 0.91,
    },
    {"type": FINAL_EVENT_TYPE, "transcript": "   ", "item_id": "seq-2"},
]


def test_recorded_sequence_yields_exactly_one_turn():
    """Partials render live but never become turns; empty finals never do."""
    finals = []
    for event in RECORDED_ASR_EVENTS:
        if is_partial_transcript(event):
            with pytest.raises(TranscriptEventError):
                normalize_final_transcript(event)
            continue
        try:
            finals.append(normalize_final_transcript(event))
        except TranscriptEventError:
            continue
    assert [final.text for final in finals] == ["check the pump"]


def test_empty_transcript_is_a_typed_422_and_no_turn_runs():
    user = _user()
    settings = _settings([user.pk])
    principal = _principal(user)
    created = _run(
        principal,
        lambda: create_voice_session(VoiceSessionCreateRequest(thread_id=None)),
        settings,
    )
    fake = _FakeTurnService()
    with patch.object(ai_app, "get_turn_service", return_value=fake):
        _expect_http(
            principal,
            lambda: submit_voice_turn(
                created["id"], VoiceTurnRequest(transcript="   ", item_id="i6")
            ),
            settings,
            422,
            "VOICE_TRANSCRIPT_INCOMPLETE",
        )
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# 6. Latency class (§8.9)                                                      #
# --------------------------------------------------------------------------- #
def test_voice_turns_map_to_the_section_8_9_latency_classes():
    # A deterministic safety refusal is bounded at 1/2/5 s regardless of rail.
    assert slo_class_for("safety_refusal", None) == "deterministic"
    # An ordinary voice lookup rides the lookup class (10/30/45 s).
    assert slo_class_for("wf8", "record_retrieval") == "lookup"
    assert slo_class_for("wf1", None) == "lookup"
