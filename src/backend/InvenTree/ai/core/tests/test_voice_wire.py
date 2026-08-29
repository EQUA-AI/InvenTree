"""S43: the voice wire models + server error-code list stay honest."""

# ruff: noqa: E402

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.voice.wire import (
    SERVER_VOICE_ERROR_CODES,
    VoiceSessionPayload,
    VoiceTransportsAllowed,
    VoiceTurnResponse,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_every_realtime_and_signaling_code_is_listed() -> None:
    """Exception ``code`` attributes must all appear in the canonical list."""
    sources = [
        _BACKEND_ROOT / "voice" / "services" / "realtime.py",
        _BACKEND_ROOT / "ai" / "core" / "voice" / "signaling.py",
    ]
    declared = set()
    for path in sources:
        declared |= set(re.findall(r"code = ['\"]([A-Z_]+)['\"]", path.read_text()))
    missing = declared - set(SERVER_VOICE_ERROR_CODES)
    assert not missing, f"codes missing from SERVER_VOICE_ERROR_CODES: {missing}"


def test_every_routes_detail_literal_is_listed() -> None:
    """HTTP detail literals in routes.py must all appear in the list."""
    routes = (_BACKEND_ROOT / "ai" / "core" / "voice" / "routes.py").read_text()
    literals = set(re.findall(r"detail=\"(VOICE_[A-Z_]+)\"", routes))
    missing = literals - set(SERVER_VOICE_ERROR_CODES)
    assert not missing, f"routes detail codes missing: {missing}"


def test_session_payload_dump_matches_the_historic_dict_shape() -> None:
    payload = VoiceSessionPayload(
        id="sid",
        state="active",
        thread_id="t1",
        transport="webrtc",
        transports_allowed=VoiceTransportsAllowed(webrtc=True, relay=False),
        webrtc_preview=True,
        turn_count=2,
        policy_version="v1",
        terminal_reason=None,
    ).model_dump(mode="json")
    assert payload == {
        "id": "sid",
        "state": "active",
        "thread_id": "t1",
        "transport": "webrtc",
        "transports_allowed": {"webrtc": True, "relay": False},
        "webrtc_preview": True,
        "turn_count": 2,
        "policy_version": "v1",
        "terminal_reason": None,
        "analysis_scope_version": 0,
    }


def test_turn_response_coerces_dict_spoken_and_question() -> None:
    """routes.py passes plain dicts; the models coerce and keep extras."""
    response = VoiceTurnResponse(
        session_id="sid",
        thread_id="t1",
        turn_id="turn1",
        message="ok",
        workflow_used="wf8",
        response_state="complete",
        replayed=False,
        spoken={
            "utterance_id": "u1",
            "spoken_summary": "ok",
            "spoken_summary_hash": "h",
            "playback_state": "requested",
        },
        pending_question={
            "kind": "single_select",
            "interrupt_id": "q1",
            "question_text": "Which one?",
            "options": [{"id": "a", "label": "A", "extra_key": "kept"}],
            "surface": "voice",
        },
    ).model_dump(mode="json")
    assert response["spoken"]["playback_state"] == "requested"
    assert response["pending_question"]["options"][0]["extra_key"] == "kept"
    assert response["pending_question"]["surface"] == "voice"


def test_generated_artifact_is_current() -> None:
    """The committed TS artifact must match the live backend definitions.

    Mirrors CI's `generate_wire_contract --check`. The island settings do
    not install the repair app the generator imports, so this runs only
    under the full Django configuration (CI runs the command directly in
    the QC workflow either way).
    """
    from django.apps import apps

    if not apps.is_installed("repair"):
        import pytest

        pytest.skip("full app registry required for the generator")
    from django.core.management import call_command

    call_command("generate_wire_contract", "--check")
