"""RV06 real builder/gateway rate round trips; transport only is simulated."""

from __future__ import annotations

import asyncio
import copy
import uuid

import pytest
from ai.core.voice.gateway import VoiceLiveChannel
from ai.core.voice.speech import (
    ExactSpeechViolation,
    build_exact_tts_payload,
    spoken_summary_hash,
)


class RecordingSocket:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(copy.deepcopy(payload))

    async def close(self):
        self.closed = True


def persisted_input():
    text = "Inspect the inlet filter before continuing.\nKeep this exact wording."
    return {
        "persisted_text": text,
        "persisted_hash": spoken_summary_hash(text),
        "utterance_id": str(uuid.uuid4()),
        "speech_kind": "status",
        "dispatch_generation": "7",
    }


def channel_and_socket():
    # Native allow-listed actor preference avoids any default-provider lookup.
    channel = VoiceLiveChannel(
        "rv06-offline-roundtrip",
        voice_preference=VoiceLiveChannel.USER_VOICE_MAP["en"],
    )
    socket = RecordingSocket()
    channel._ws = socket
    return channel, socket


def private_rate_paths(value, path=()):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "_aimms_rate":
                found.append((*path, key))
            found.extend(private_rate_paths(item, (*path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(private_rate_paths(item, (*path, str(index))))
    return found


def verify_exact_create(create, persisted):
    response = create["response"]
    message = response["pre_generated_assistant_message"]
    assert message["content"] == [{"type": "text", "text": persisted["persisted_text"]}]
    assert spoken_summary_hash(message["content"][0]["text"]) == persisted["persisted_hash"]
    assert response["metadata"]["aimms_spoken_hash"] == persisted["persisted_hash"]
    assert response["metadata"]["aimms_utterance_id"] == persisted["utterance_id"]
    assert response["metadata"]["aimms_dispatch_generation"] == "7"
    assert private_rate_paths(create) == []


def test_rv06_implicit_normal_positive_preserves_exact_built_payload():
    async def run():
        persisted = persisted_input()
        before = copy.deepcopy(persisted)
        payload = build_exact_tts_payload(**persisted)
        original = copy.deepcopy(payload)
        channel, socket = channel_and_socket()
        try:
            await channel.send_control(payload)
            assert [item["type"] for item in socket.sent] == ["response.create"]
            verify_exact_create(socket.sent[0], persisted)
            assert payload == original and persisted == before
        finally:
            await channel.close()

    asyncio.run(run())


@pytest.mark.parametrize("rate", [0.8, 1.0])
def test_rv06_actual_built_supported_rate_never_leaks_private_metadata(rate):
    async def run():
        persisted = persisted_input()
        before = copy.deepcopy(persisted)
        payload = build_exact_tts_payload(**persisted, rate=rate)
        original = copy.deepcopy(payload)
        channel, socket = channel_and_socket()
        try:
            await channel.send_control(payload)
            updates = [item for item in socket.sent if item["type"] == "session.update"]
            expected = (
                []
                if rate == 1.0  # noqa: RUF069 - exact finite allow-listed wire value.
                else [
                    {
                        "type": "session.update",
                        "session": {
                            "voice": {
                                "type": "azure-standard",
                                "name": VoiceLiveChannel.USER_VOICE_MAP["en"][0],
                                "rate": "0.8",
                            }
                        },
                    }
                ]
            )
            assert updates == expected
            creates = [item for item in socket.sent if item["type"] == "response.create"]
            assert len(creates) == 1 and socket.sent[-1] == creates[0]
            verify_exact_create(creates[0], persisted)
            assert channel._output_rate == rate
            assert payload == original and persisted == before
        finally:
            await channel.close()

    asyncio.run(run())


def test_rv06_slow_repeat_then_normal_updates_rate_only_at_real_transitions():
    async def run():
        channel, socket = channel_and_socket()
        persisted = persisted_input()
        before = copy.deepcopy(persisted)
        try:
            for index, rate in enumerate([0.8, 0.8, 1.0]):
                payload = build_exact_tts_payload(**persisted, rate=rate)
                original = copy.deepcopy(payload)
                await channel.send_control(payload)
                create = socket.sent[-1]
                response_id = f"rv06-response-{index}"
                assert (
                    channel._gate.classify({
                        "type": "response.created",
                        "response": {"id": response_id},
                    })
                    == "speech_lifecycle"
                )
                assert (
                    channel._gate.classify({
                        "type": "response.done",
                        "response": {"id": response_id, "status": "completed"},
                    })
                    == "speech_lifecycle"
                )
                assert channel._gate.pending_app_responses == 0
                assert not channel.has_active_app_response()
                assert payload == original
                verify_exact_create(create, persisted)
            assert [
                item["session"]["voice"]["rate"]
                for item in socket.sent
                if item["type"] == "session.update"
            ] == ["0.8", "1.0"]
            assert [item["type"] for item in socket.sent] == [
                "session.update",
                "response.create",
                "response.create",
                "session.update",
                "response.create",
            ]
            assert channel._output_rate == 1.0 and persisted == before  # noqa: RUF069 - exact finite wire state.
        finally:
            await channel.close()

    asyncio.run(run())


def test_rv06_legacy_top_level_rate_remains_supported_without_source_mutation():
    async def run():
        persisted = persisted_input()
        payload = build_exact_tts_payload(**persisted)
        payload["_aimms_rate"] = 0.8
        original = copy.deepcopy(payload)
        channel, socket = channel_and_socket()
        try:
            await channel.send_control(payload)
            assert socket.sent[0]["type"] == "session.update"
            assert socket.sent[0]["session"]["voice"]["rate"] == "0.8"
            verify_exact_create(socket.sent[-1], persisted)
            assert payload == original
        finally:
            await channel.close()

    asyncio.run(run())


def test_rv06_hash_mismatch_still_refuses_before_any_transport():
    persisted = persisted_input()
    persisted["persisted_hash"] = spoken_summary_hash("Different persisted wording.")
    with pytest.raises(ExactSpeechViolation, match="persisted hash"):
        build_exact_tts_payload(**persisted, rate=0.8)


def test_rv06_unsupported_rate_still_refuses_before_any_transport():
    with pytest.raises(ValueError, match="unsupported presentation rate"):
        build_exact_tts_payload(**persisted_input(), rate=1.5)
