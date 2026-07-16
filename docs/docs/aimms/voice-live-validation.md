---
title: Voice Live Validation Record (WS2)
---

# Azure Voice Live validation record

This is the WS2 evidence template from `LocalDocs/VoiceImplementationPlan.md`.
Automated checks live in `src/backend/InvenTree/ai/core/tests/integration/`;
the matrices below are the human-executed half. Fill every cell with a result
and date, or the corresponding milestone gate stays open. Store no keys,
tokens, SDP payloads, or recordings in this file.

Run the automated target-host suites from the approved hosting environment:

```bash
cd src/backend/InvenTree
AIMMS_AZURE_INTEGRATION=1 \
AIMMS_PROXY_PROBE_URL=https://<your-aimms-host> \
DJANGO_SETTINGS_MODULE=ai.core.tests.settings \
python -m pytest ai/core/tests/integration -v
```

## WS2-T2 — Voice Live playground matrix (candidate: `en-US-AvaNeural`, `azure_semantic_vad`)

| Check | Setting under test | Result | Date | Notes |
|---|---|---|---|---|
| Voice renders acceptably | `en-US-AvaNeural`, rate `1.0` | | | |
| Microphone + interim transcript | `azure-speech`, `en-US` | | | |
| VAD end-of-turn pause feel | `silence_duration_ms=550` | | | |
| VAD prefix capture | `prefix_padding_ms=420` | | | |
| Noise suppression on shop-floor sample | `azure_deep_noise_suppression` | | | |
| Echo cancellation with delayed playback | `server_echo_cancellation` | | | |
| Barge-in interrupts playback | `interrupt_response=true` | | | |
| Filler words preserved | `remove_filler_words=false` | | | |
| Industrial terms with `phrase_list` | `["AIMMS","InvenTree","LOTO", …]` | | | |
| Transcript latency subjectively usable | end-of-speech → final text | | | |

## WS2-T3 — session-model / transcriber pair scoring

Score each pair on the same recorded utterance set (identifiers, serials,
measurements+units, negations, shop noise). `gpt-realtime-mini + azure-speech`
is an invalid pairing and must not be scored.

| Metric | `gpt-4.1-mini` + `azure-speech` + `phrase_list` | `gpt-realtime-mini` + `gpt-4o-transcribe` |
|---|---|---|
| Identifier accuracy (IPN/serial) | | |
| Measurement + unit accuracy | | |
| Negation fidelity | | |
| Noisy-environment accuracy | | |
| Final-transcript latency | | |
| Relative cost per session-minute | | |
| **Selected pair** | | |

Decision recorded by / date: ______

## WS2-T8 — WebRTC pilot network matrix

Public preview — not recommended for production by Microsoft; pilot only,
behind `FEATURE_VOICE_LIVE_WEBRTC`, with text as terminal fallback.

| Probe | Pilot browser/device | Corporate network path | Result | Date |
|---|---|---|---|---|
| Microphone permission flow | | | | |
| SDP relay via AIMMS signaling | | | | |
| RTP media path (browser ↔ Azure) | | | | |
| `voice-live-events` data channel | | | | |
| Firewall/TURN behavior | | | | |
| Reconnect after network blip | | | | |
| Explicit text fallback on failure | | | | |

## Descoped

WS2-T9 (batch Speech validation) is descoped by the 2026-07-15 no-audio
decision: capture uses the realtime transcription path, so no batch job,
stored audio object, or provider result copy exists to validate.

## Sign-off

| Gate input | Owner | Accepted (date) |
|---|---|---|
| Playground matrix complete | | |
| Model pair selected | | |
| Managed-identity suite green on target host | | |
| Proxy WebSocket probe green | | |
| WebRTC matrix accepted or WebRTC deferred | | |
