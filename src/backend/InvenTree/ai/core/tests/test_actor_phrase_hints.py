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
    """Operator-static hints come first; actor hints append, deduplicated."""
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings_stub(["Epcon", "AIMMS"]),
    )
    channel = VoiceLiveChannel("session-1", user_id=7)
    payload = channel._session_policy_payload(("Influent Pump 1", "AIMMS", "WO-000128"))
    hints = payload["input_audio_transcription"]["phrase_list"]
    assert hints == ["Epcon", "AIMMS", "Influent Pump 1", "WO-000128"]


def test_policy_payload_without_actor_hints_matches_static(monkeypatch):
    monkeypatch.setattr("ai.core.config.get_settings", lambda: _settings_stub(["Epcon"]))
    channel = VoiceLiveChannel("session-2", user_id=None)
    payload = channel._session_policy_payload(())
    assert payload["input_audio_transcription"]["phrase_list"] == ["Epcon"]


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
