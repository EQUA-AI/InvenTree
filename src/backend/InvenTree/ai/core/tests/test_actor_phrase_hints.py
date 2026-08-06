"""S7 A2: actor-scoped ASR phrase hints — mint-path behaviour (island half).

The authorization-boundary test (actor A's hints never contain actor B's
machine names) lives in the Django world (``assets`` tests), where machines
and clients can be built. Here we pin the mint path itself: the merge with
operator-static hints, the async wrap, and the degrade-to-empty contract.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.voice.gateway import VoiceLiveChannel, _channels, channel_for_session


@pytest.fixture(autouse=True)
def _clean_channels():
    _channels.clear()
    yield
    _channels.clear()


def _settings_stub(hints):
    from ai.core.config import Settings

    return Settings(
        _env_file=None,
        AZURE_VOICELIVE_PHRASE_HINTS=hints,
        AZURE_VOICELIVE_TRANSCRIPTION_MODEL="gpt-4o-transcribe",
    )


def test_policy_payload_merges_static_and_actor_hints(monkeypatch):
    """Merged hints reach the OpenAI-family transcriber as a prompt string.

    The first live rollout sent ``phrase_list`` here — an Azure-family field —
    and gpt-4o-transcribe silently stopped transcribing entirely. Hints for
    OpenAI-family models must travel as ``prompt``.
    """
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings_stub(["Epcon", "AIMMS"]),
    )
    channel = VoiceLiveChannel("session-1", user_id=7)
    payload = channel._session_policy_payload(("Influent Pump 1", "AIMMS", "WO-000128"))
    transcription = payload["input_audio_transcription"]
    assert "phrase_list" not in transcription
    assert transcription["prompt"] == (
        "Expected terminology: Epcon, AIMMS, Influent Pump 1, WO-000128"
    )


def test_policy_payload_without_actor_hints_matches_static(monkeypatch):
    monkeypatch.setattr("ai.core.config.get_settings", lambda: _settings_stub(["Epcon"]))
    channel = VoiceLiveChannel("session-2", user_id=None)
    payload = channel._session_policy_payload(())
    assert payload["input_audio_transcription"]["prompt"] == "Expected terminology: Epcon"


def test_azure_family_transcriber_still_gets_phrase_list(monkeypatch):
    """azure-speech keeps the native phrase_list field."""
    from ai.core.config import Settings

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: Settings(
            _env_file=None,
            AZURE_VOICELIVE_PHRASE_HINTS=["Epcon"],
            AZURE_VOICELIVE_TRANSCRIPTION_MODEL="azure-speech",
        ),
    )
    channel = VoiceLiveChannel("session-az", user_id=None)
    transcription = channel._session_policy_payload(("Clarifier",))["input_audio_transcription"]
    assert transcription["phrase_list"] == ["Epcon", "Clarifier"]
    assert "prompt" not in transcription


def test_empty_hints_add_no_transcription_fields(monkeypatch):
    """No hints -> the payload matches the pre-S7 shape exactly."""
    monkeypatch.setattr("ai.core.config.get_settings", lambda: _settings_stub([]))
    channel = VoiceLiveChannel("session-none", user_id=None)
    transcription = channel._session_policy_payload(())["input_audio_transcription"]
    assert "phrase_list" not in transcription
    assert "prompt" not in transcription


@pytest.mark.asyncio
async def test_actor_hints_resolve_off_the_event_loop(monkeypatch):
    """The sync lexicon call runs via sync_to_async — no SynchronousOnlyOperation."""
    calls = []

    def _fake_hints(user_id):
        # Raise loudly if we are ever on the event-loop thread: this is
        # exactly the failure that reverted the first build.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:  # pragma: no cover - the regression this test exists to catch
            raise AssertionError("actor_phrase_hints ran on the event loop")
        calls.append(user_id)
        return ["Clarifier Drive 2"]

    monkeypatch.setattr("ai.core.tools.capabilities.actor_phrase_hints", _fake_hints)
    channel = VoiceLiveChannel("session-3", user_id=42)
    hints = await channel._actor_phrase_hints()
    assert hints == ("Clarifier Drive 2",)
    assert calls == [42]


@pytest.mark.asyncio
async def test_actor_hints_degrade_to_empty_on_failure(monkeypatch):
    def _boom(user_id):
        raise RuntimeError("lexicon backend down")

    monkeypatch.setattr("ai.core.tools.capabilities.actor_phrase_hints", _boom)
    channel = VoiceLiveChannel("session-4", user_id=42)
    assert await channel._actor_phrase_hints() == ()


@pytest.mark.asyncio
async def test_anonymous_session_gets_no_actor_hints():
    channel = VoiceLiveChannel("session-5", user_id=None)
    assert await channel._actor_phrase_hints() == ()


def test_channel_factory_threads_the_owner():
    class _Session:
        id = "abc"
        owner_id = 99

    channel = channel_for_session(_Session())
    assert channel._user_id == 99
