"""Bounded fixed output sample. No microphone, user text, session row or audio file."""

from __future__ import annotations

import asyncio
import base64
import io
import wave

SAMPLE_TEXT = {
    "en-US": "This is the AIMMS voice. Ask a question, or say stop speaking to interrupt.",
    "es-ES": "Esta es la voz de AIMMS. Haz una pregunta.",
    "de-DE": "Das ist die AIMMS Stimme. Stellen Sie eine Frage.",  # codespell:ignore sie
    "fr-FR": "Voici la voix AIMMS. Posez une question.",
}
MAX_AUDIO_BYTES = 24_000 * 2 * 15


def reserve_sample(actor_id) -> bool:
    """Shared-cache atomic limit: at most one 15-second sample per minute/actor."""
    from django.core.cache import cache

    return cache.add(f"voice-sample-v1:{actor_id}", True, timeout=60)


async def synthesize_sample(locale: str, voice: str) -> bytes:
    """Short-lived output-only provider socket; bounded RAM, never retained.

    Fixed application text is the sample allow-list authority. It is not a chat
    answer and cannot carry tools, input audio or a user-supplied slot.
    """
    import aiohttp
    from ai.core.config import get_settings
    from ai.core.voice.endpoints import build_control_url
    from ai.core.voice.experience import voice_pairs
    from ai.core.voice.gateway import _token_cache
    from ai.core.voice.provider import EventGate, SessionPolicy
    from ai.core.voice.speech import build_exact_tts_payload, spoken_summary_hash

    if (voice, locale) not in voice_pairs():
        raise ValueError("unmapped voice")
    settings = get_settings()
    async with asyncio.timeout(20):
        bearer = await _token_cache.bearer()
        async with (
            aiohttp.ClientSession() as http,
            http.ws_connect(
                build_control_url(
                    settings.azure_voicelive_endpoint,
                    settings.azure_voicelive_model,
                    api_version=settings.azure_voicelive_api_version,
                ),
                headers={"Authorization": f"Bearer {bearer}"},
                max_msg_size=1_000_000,
            ) as ws,
        ):
            policy = SessionPolicy(
                voice_name=voice,
                language=locale,
                transcription_model=settings.azure_voicelive_transcription_model,
                native_sts=settings.feature_voice_native_sts,
            ).session_update_payload()
            policy["session"]["output_audio_format"] = "pcm16"
            await ws.send_json(policy)
            sent = False
            gate = EventGate()
            audio = bytearray()
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise ValueError("sample transport closed")
                event = message.json()
                kind = gate.classify(event)
                if kind in ("forbidden", "error"):
                    raise ValueError("sample policy refused")
                if event.get("type") == "session.updated" and not sent:
                    sent = True
                    gate.expect_app_response("sample-v1")
                    text = SAMPLE_TEXT[locale]
                    speech = build_exact_tts_payload(
                        persisted_text=text, persisted_hash=spoken_summary_hash(text)
                    )
                    speech["event_id"] = "sample-v1"
                    await ws.send_json(speech)
                if event.get("type") == "response.audio.delta":
                    audio.extend(base64.b64decode(event["delta"], validate=True))
                    if len(audio) > MAX_AUDIO_BYTES:
                        raise ValueError("sample audio limit")
                if event.get("type") == "response.done":
                    if not audio or event.get("response", {}).get("status") != "completed":
                        raise ValueError("sample incomplete")
                    output = io.BytesIO()
                    with wave.open(output, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(24000)
                        wav.writeframes(audio)
                    return output.getvalue()
    raise ValueError("sample unavailable")
