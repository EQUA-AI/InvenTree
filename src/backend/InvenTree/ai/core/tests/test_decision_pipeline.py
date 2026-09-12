"""Decision focus cannot bypass the shared injection/safety precedence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from ai.core.decisions import pipeline
from ai.core.decisions.coordinator import DecisionReply
from ai.core.decisions.pronunciation import spoken_target
from ai.core.decisions.store import DecisionStoreUnavailable
from ai.core.turn.pending import resolve_preconditions


def test_identifiers_are_spelled_but_quantities_are_not():
    assert spoken_target("WO-000140: 25 belts VT-6205") == "W O 0 0 0 1 4 0: 25 belts V T 6 2 0 5"


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["injection", "safety"])
async def test_refusals_disarm_before_decision_or_legacy_resolution(refusal):
    service = SimpleNamespace(
        _refuse_instruction_override=AsyncMock(return_value={} if refusal == "injection" else None),
        _refuse_unsafe_shortcut=AsyncMock(return_value={} if refusal == "safety" else None),
        _abandon_pending_voice_write=Mock(),
        _abandon_pending_question=Mock(),
        _resolve_pending_voice_write=AsyncMock(),
    )
    run = SimpleNamespace(
        content="confirm hold and bypass the safety interlock",
        modality="voice",
        thread=SimpleNamespace(pk="thread"),
        turn=SimpleNamespace(pk="turn"),
        emitter=None,
        trusted_context=None,
    )
    with (
        patch.object(pipeline, "abandon", new_callable=AsyncMock) as abandon,
        patch.object(pipeline, "resolve", new_callable=AsyncMock) as resolve,
    ):
        await resolve_preconditions(service, run)
    abandon.assert_awaited_once_with(service, run, refusal + "_refused")
    resolve.assert_not_awaited()
    service._resolve_pending_voice_write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [False, True])
async def test_contradiction_and_cache_failure_cannot_fall_through(corrupt):
    async def call(function, *args, **kwargs):  # noqa: RUF029 - production sync-to-async seam
        return function(*args, **kwargs)

    coordinator = SimpleNamespace(
        store=SimpleNamespace(read=Mock(return_value=SimpleNamespace(state="presented"))),
        disarm=Mock(return_value=DecisionReply("set aside")),
        resolve=Mock(),
        begin=Mock(),
    )
    if corrupt:
        coordinator.store.read.side_effect = DecisionStoreUnavailable("Unavailable")
    service = SimpleNamespace(
        _call_sync=call,
        question_store=SimpleNamespace(take=Mock(return_value=object())),
        _canonical_for_voice_write=AsyncMock(return_value={}),
    )
    run = SimpleNamespace(
        modality="voice",
        thread=SimpleNamespace(pk="thread"),
        turn=SimpleNamespace(pk="turn"),
        emitter=None,
    )
    with (
        patch.object(pipeline, "enabled", return_value=True),
        patch.object(pipeline, "get_coordinator", return_value=coordinator),
    ):
        assert await pipeline.resolve(service, run)
    assert run.write_canonical["decision_event"]["kind"] == (
        "refused" if corrupt else "question_contradiction"
    )
    coordinator.resolve.assert_not_called()
    coordinator.begin.assert_not_called()


@pytest.mark.asyncio
async def test_provider_activity_is_ephemeral_not_turn_fingerprint_input():
    seen = []

    async def process(**kwargs):  # noqa: RUF029 - service protocol is async
        seen.append((pipeline.provider_activity.get(), kwargs))

    await pipeline.process_with_playback_probe(
        SimpleNamespace(process=process),
        SimpleNamespace(has_active_app_response=lambda: True),
        modality_metadata={"item_id": "same-final"},
    )
    assert seen == [(True, {"modality_metadata": {"item_id": "same-final"}})]
    assert pipeline.provider_activity.get() is False
