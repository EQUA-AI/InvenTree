"""Phase D contract and output-only safety tests; no external provider calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from ai.core.decisions.grammar import DecisionUtteranceKind, classify_decision_utterance
from ai.core.tests.test_realtime_session_api import _principal, _run, _settings, _user
from ai.core.voice import routes
from ai.core.voice.experience import (
    CONSENT_VERSION,
    capability,
    validate_preferences,
    voice_pairs,
    write_locale_reason,
    writes_eligible,
)
from ai.core.voice.presentation import chunks, short_text
from ai.core.voice.sample import reserve_sample
from django.core.management import call_command
from fastapi import HTTPException
from voice.models import VoicePresentation, VoiceSession, VoiceUtterance
from voice.services import presentation


@pytest.fixture(scope="module", autouse=True)
def database():
    call_command("migrate", verbosity=0, interactive=False)


def session_for(user, settings, **kwargs):
    body = routes.VoiceSessionCreateRequest(consent_version=CONSENT_VERSION, **kwargs)
    payload = _run(_principal(user), lambda: routes.create_voice_session(body), settings)
    return VoiceSession.objects.get(pk=payload["id"])


def test_capability_defaults_and_foreground_dependency():
    s = _settings(FEATURE_VOICE_FOREGROUND_SESSION=True)
    cap = capability(s)
    assert cap["default_mode"] == "continuous"
    assert cap["modes"] == ["continuous", "push_to_talk"]
    assert cap["idle_timeout_s"] == cap["decision_max_armed_s"] == 300
    assert cap["mic_silence_rms"] == pytest.approx(0.01)
    assert cap["mic_silence_window_s"] == pytest.approx(1.5)
    assert cap["tts_timeout_s"] == 8 and cap["route_loss_pause"]
    assert cap["consent_version"] == CONSENT_VERSION
    assert cap["vocabulary_owner"] == "AIMMS"
    assert cap["voices"] == [{"voice": voice, "locale": locale} for voice, locale in voice_pairs()]
    assert cap["foreground_session"] and cap["presentation"] and cap["help"]
    assert not capability(_settings())["foreground_session"]
    with pytest.raises(ValueError, match="requires FEATURE_VOICE_LIVE"):
        _settings(FEATURE_VOICE_LIVE=False, FEATURE_VOICE_FOREGROUND_SESSION=True)


@pytest.mark.parametrize("timeout", [30, 299, 301, 3600])
def test_consent_timeout_coupling(timeout):
    with pytest.raises(ValueError, match="consent-v2"):
        _settings(VOICE_LIVE_IDLE_TIMEOUT_S=timeout)


@pytest.mark.parametrize("version", ["", "consent-v1", "consent-v3"])
def test_stale_missing_consent_refuses_without_a_session(version):
    user = _user()
    before = VoiceSession.objects.count()
    with pytest.raises(HTTPException) as error:
        _run(
            _principal(user),
            lambda: routes.create_voice_session(
                routes.VoiceSessionCreateRequest(consent_version=version)
            ),
            _settings(),
        )
    assert error.value.detail == "VOICE_CONSENT_REQUIRED"
    assert VoiceSession.objects.count() == before


@pytest.mark.parametrize("voice,locale", voice_pairs())
def test_session_persists_exact_mapped_preferences(voice, locale):
    session = session_for(_user(), _settings(), voice=voice, locale=locale)
    assert (session.voice, session.locale, session.consent_version) == (
        voice,
        locale,
        CONSENT_VERSION,
    )


@pytest.mark.parametrize(
    "voice,locale", [("alloy", "en-US"), ("en-US-AvaNeural", "es-ES"), ("en-US-AvaNeural", "en-GB")]
)
def test_unmapped_pairs_are_refused(voice, locale):
    with pytest.raises(ValueError, match="VOICE_LOCALE_UNSUPPORTED"):
        validate_preferences(locale, voice, CONSENT_VERSION)


@pytest.mark.parametrize("voice,locale", voice_pairs())
def test_locale_grammar_and_reason_are_honest(voice, locale):
    assert bool(write_locale_reason(locale))
    expected = (
        DecisionUtteranceKind.AFFIRM if writes_eligible(locale) else DecisionUtteranceKind.UNRELATED
    )
    assert classify_decision_utterance("yes", allowed_responses=["yes"], locale=locale) == expected


def test_fixed_sample_rate_limit_and_no_session_or_transcript():
    user = _user()
    settings = _settings(FEATURE_VOICE_FOREGROUND_SESSION=True)
    counts = VoiceSession.objects.count(), VoiceUtterance.objects.count()
    request = routes.VoiceSampleRequest(locale="en-US", voice="en-US-AvaNeural")
    with patch(
        "ai.core.voice.sample.synthesize_sample",
        new_callable=AsyncMock,
        return_value=b"synthetic-output",
    ) as synthesize:
        response = _run(_principal(user), lambda: routes.voice_sample(request), settings)
        assert (
            response.body == b"synthetic-output" and response.headers["cache-control"] == "no-store"
        )
        synthesize.assert_awaited_once_with("en-US", "en-US-AvaNeural")
        with pytest.raises(HTTPException) as limited:
            _run(_principal(user), lambda: routes.voice_sample(request), settings)
        assert limited.value.status_code == 429
    assert counts == (VoiceSession.objects.count(), VoiceUtterance.objects.count())
    assert reserve_sample(f"different:{user.pk}")
    with pytest.raises(ValueError):
        routes.VoiceSampleRequest(locale="en-US", voice="en-US-AvaNeural", text="arbitrary")


def test_cancel_stops_provider_speech_and_keeps_input_records():
    user = _user()
    settings = _settings()
    session = session_for(user, settings)
    channel = SimpleNamespace(send_control=AsyncMock())
    with patch.object(routes, "_provider_channel_factory", return_value=channel):
        result = _run(
            _principal(user), lambda: routes.cancel_voice_playback(str(session.pk)), settings
        )
    assert result["id"] == str(session.pk)
    channel.send_control.assert_awaited_once_with({"type": "response.cancel"})
    session.refresh_from_db()
    assert session.state == "created"


def test_versioned_vocabulary_never_widens_scope_or_emits_stale_names():
    from ai.core.voice.vocabulary import hints_for_scoped_rows

    assert hints_for_scoped_rows([]) == []
    row = SimpleNamespace(
        pk=8,
        name="Boiler Feed Pump B",
        location="Plant A / Boiler House / Feedwater Skid / Position B",
    )
    assert hints_for_scoped_rows([row]) == ["Boiler Feed Pump B", row.location]
    row.location = "changed"
    assert hints_for_scoped_rows([row]) == ["Boiler Feed Pump B"]
    row.name = "different deployment"
    assert hints_for_scoped_rows([row]) == []


def test_pages_total_first_three_and_short_keep_warning():
    text = "Warning: never energize before inspection.\n1. Alpha\n2. Bravo\n3. Charlie\n4. Delta"
    pages = chunks(text)
    assert len(pages) == 2 and "4 items" in pages[0]
    assert pages[0].count("Warning: never energize before inspection.") == 1
    assert "Charlie" in pages[0] and "Delta" not in pages[0]
    assert all("never energize before inspection" in page for page in pages)
    assert "never energize before inspection" in short_text(pages[1])


def test_missing_inventory_fields_do_not_reorder_records_but_warnings_repeat():
    from ai.core.turn.responses import _plain_spoken_text

    layout = "Inventory page 1.\n" + "\n".join(
        f"{index}. Stock item {index}: 0.25000 metres; location A / B; serial not set."
        for index in range(1, 6)
    )
    pages = chunks(_plain_spoken_text(layout), layout=layout)
    assert len(pages) == 2
    assert pages[0].count("Inventory page 1.") == 1
    assert "5 items" in pages[0]
    assert "Stock item 4" not in pages[0] and "Stock item 5" not in pages[0]
    assert [pages[0].index(f"Stock item {index}:") for index in (1, 2, 3)] == sorted(
        pages[0].index(f"Stock item {index}:") for index in (1, 2, 3)
    )
    assert pages[0].count("0.25000 metres; location A / B; serial not set.") == 3
    assert pages[1].count("0.25000 metres; location A / B; serial not set.") == 2
    warning = " Never use this stock before inspection."
    warned = layout + warning
    assert all(
        warning.strip() in page for page in chunks(_plain_spoken_text(warned), layout=warned)
    )


def test_validated_long_answer_and_layout_are_paged_without_raw_text_bypass():
    from ai.core.turn.responses import _canonical_response_for_legacy

    layout = "Work orders:\n1. WO 101 alpha\n2. WO 102 bravo\n3. WO 103 charlie\n4. WO 104 delta"
    with patch(
        "ai.core.config.get_settings", return_value=_settings(FEATURE_VOICE_FOREGROUND_SESSION=True)
    ):
        response = _canonical_response_for_legacy(layout, speakable=True)
        assert response.speak
        pages = chunks(response.spoken_summary, layout=layout)
        assert len(pages) == 2 and "4 items" in pages[0]
        assert "delta" not in pages[0] and "WO 104 delta" in pages[1]
        long = "The answer is available. " * 50 + "Wear eye protection."
        canonical = _canonical_response_for_legacy(long, speakable=True)
        assert canonical.speak and canonical.spoken_summary.endswith("Wear eye protection.")
        assert "Wear eye protection." in short_text(canonical.spoken_summary)
        assert len(short_text(canonical.spoken_summary)) < len(canonical.spoken_summary)
    assert "injected" not in " ".join(
        chunks(response.spoken_summary, layout="1. injected\n2. unvalidated")
    )


def test_presentation_hash_binding_cursor_and_output_only_retries():
    from voice.services.realtime import ExactSpeechConflict

    user = _user()
    settings = _settings(FEATURE_VOICE_FOREGROUND_SESSION=True)
    session = session_for(user, settings)
    text = "1. Alpha\n2. Bravo\n3. Charlie\n4. Delta"
    item, first = presentation.create(session=session, turn_id="answer-1", text=text)
    same, replay = presentation.create(session=session, turn_id="answer-1", text=text)
    assert same.pk == item.pk and first.pk == replay.pk
    assert VoicePresentation.objects.filter(session=session).count() == 1
    assert session.utterances.count() == 2
    with pytest.raises(ExactSpeechConflict):
        presentation.create(session=session, turn_id="answer-1", text="different")
    request = routes.PresentationCommandRequest(
        **presentation.payload(item), presentation_command="next"
    )
    channel = SimpleNamespace(send_control=AsyncMock())
    with (
        patch.object(routes, "_provider_channel_factory", return_value=channel),
        patch("ai.core.app.get_turn_service") as business,
    ):
        result = _run(
            _principal(user),
            lambda: routes.presentation_command(str(session.pk), request),
            settings,
        )
        business.assert_not_called()
    assert result["presentation"]["index"] == 1
    assert "Delta" in result["spoken"]["spoken_summary"]
    session.refresh_from_db()
    assert session.turn_count == 0
    with pytest.raises(HTTPException) as stale:
        _run(
            _principal(user),
            lambda: routes.presentation_command(str(session.pk), request),
            settings,
        )
    assert stale.value.status_code == 409
    other = session_for(_user(), settings)
    with pytest.raises(HTTPException):
        _run(
            _principal(other.owner),
            lambda: routes.presentation_command(str(other.pk), request),
            settings,
        )
