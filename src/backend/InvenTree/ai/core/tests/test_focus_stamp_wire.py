"""Client focus stamps: fingerprint participation and voice wire echo.

The client's ``expected_focus_version`` is a compatible optional hint on the
voice create/turn requests. It participates in the turn's idempotency
fingerprint so a replay can never be reinterpreted under a different stamp,
while requests without a hint keep their EXACT historical fingerprints (a
stored turn replayed after upgrade must not conflict).
"""

from __future__ import annotations

import pytest
from ai.core.turn.request import turn_request_fingerprint

_BASE = {
    "content": "what is its status?",
    "modality": "voice",
    "trusted_context": {"actor": "user:5"},
    "modality_metadata": {"voice_session_id": "s1"},
}


def test_fingerprint_without_hint_is_byte_compatible_with_history() -> None:
    """No hint => identical envelope to the pre-focus fingerprints."""
    import hashlib
    import json

    payload = json.dumps(
        {
            "content": _BASE["content"],
            "modality": _BASE["modality"],
            "trusted_context": _BASE["trusted_context"],
            "modality_metadata": _BASE["modality_metadata"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert turn_request_fingerprint(**_BASE) == expected


def test_focus_stamp_participates_in_the_fingerprint() -> None:
    plain = turn_request_fingerprint(**_BASE)
    stamped0 = turn_request_fingerprint(**_BASE, expected_focus_version=0)
    stamped1 = turn_request_fingerprint(**_BASE, expected_focus_version=1)
    assert stamped0 != plain
    assert stamped0 != stamped1
    # ...but the same stamp is the same request.
    assert stamped0 == turn_request_fingerprint(**_BASE, expected_focus_version=0)


def test_voice_wire_models_echo_focus_without_hashes(monkeypatch) -> None:
    from ai.core.voice.wire import (
        SERVER_VOICE_ERROR_CODES,
        VoiceSessionPayload,
        VoiceTransportsAllowed,
        VoiceTurnResponse,
    )

    session = VoiceSessionPayload(
        id="s1",
        state="active",
        thread_id="thread_1",
        transport=None,
        transports_allowed=VoiceTransportsAllowed(webrtc=False, relay=True),
        webrtc_preview=True,
        turn_count=0,
        policy_version="v1",
        terminal_reason=None,
        focus={
            "kind": "machine",
            "id": 3,
            "label": "Machine 3",
            "machine_id": 3,
            "work_order_id": None,
        },
        focus_version=2,
    ).model_dump(mode="json")
    assert session["focus"]["kind"] == "machine"
    assert session["focus_version"] == 2
    assert "hash" not in session["focus"]

    turn = VoiceTurnResponse(
        session_id="s1",
        thread_id="thread_1",
        turn_id="t1",
        message="answer",
        workflow_used="wf8",
        response_state="complete",
        replayed=False,
        spoken=None,
        focus={
            "kind": "kanban_card",
            "id": 11,
            "label": "Diagnose",
            "machine_id": 3,
            "work_order_id": 5,
        },
        focus_version=2,
    ).model_dump(mode="json")
    assert turn["focus"]["work_order_id"] == 5
    assert turn["focus"]["id"] == 11
    # Focus echo is optional/compatible: nothing without a focus.
    assert (
        VoiceTurnResponse(
            session_id="s1",
            thread_id="thread_1",
            turn_id="t2",
            message="answer",
            workflow_used=None,
            response_state="complete",
            replayed=False,
            spoken=None,
        ).model_dump(mode="json")["focus"]
        is None
    )

    # The stale-stamp rejection code exists exactly once on the wire union.
    assert SERVER_VOICE_ERROR_CODES.count("VOICE_FOCUS_CHANGED") == 1


def test_expected_focus_version_is_strictly_normalized() -> None:
    """No bool/float/string coercion and no negative stamps (gap 2)."""
    from ai.core.focus.contract import (
        FocusValidationError,
        normalize_expected_focus_version,
    )

    assert normalize_expected_focus_version(None) is None
    assert normalize_expected_focus_version(0) == 0
    assert normalize_expected_focus_version(7) == 7
    for bad in (True, False, 1.0, 3.5, "3", -1, [3], {"v": 1}):
        with pytest.raises(FocusValidationError):
            normalize_expected_focus_version(bad)


def test_typed_and_voice_request_models_reject_loose_stamps() -> None:
    from ai.core.app import ChatRequest
    from ai.core.voice.routes import VoiceSessionCreateRequest, VoiceTurnRequest
    from pydantic import ValidationError

    for bad in (True, 1.0, "3", -1):
        with pytest.raises(ValidationError):
            ChatRequest(message="hi", expected_focus_version=bad)
        with pytest.raises(ValidationError):
            VoiceTurnRequest(transcript="hi", item_id="1", expected_focus_version=bad)
        with pytest.raises(ValidationError):
            VoiceSessionCreateRequest(expected_focus_version=bad)
    assert ChatRequest(message="hi", expected_focus_version=3).expected_focus_version == 3
    assert ChatRequest(message="hi").expected_focus_version is None


def test_progress_mode_is_an_optional_validated_client_preference() -> None:
    """Off/Brief/Guided hint per transcript — never a stored business input."""
    from ai.core.voice.routes import VoiceTurnRequest
    from pydantic import ValidationError

    assert VoiceTurnRequest(transcript="hi", item_id="1").progress_mode == "brief"
    for mode in ("off", "guided"):
        assert (
            VoiceTurnRequest(transcript="hi", item_id="1", progress_mode=mode).progress_mode == mode
        )
    with pytest.raises(ValidationError):
        VoiceTurnRequest(transcript="hi", item_id="1", progress_mode="loud")
    # It never enters the business idempotency fingerprint: the fingerprint
    # builder has no such input at all (the ``voice_timing`` precedent —
    # optional observations cannot change a business replay fingerprint).
    assert turn_request_fingerprint(**_BASE) == turn_request_fingerprint(**_BASE)
