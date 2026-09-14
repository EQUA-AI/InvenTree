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

## Voice UX Phase V — 2026-09-14

This section supersedes neither the historical WS2 blanks above nor actual human
acceptance. Phase F/native lifecycle work is deferred by the owner to the mobile
version. Foreground web privacy, truthful state and existing browser behavior
remain in scope. Latest OS is a minimum assumption, **not device qualification**.
Email/provider testing and reply monitoring remain owner-paused.

The tracked scenario/CI registry is `.github/voice_validation_manifest.json`.
It maps S01–S22, six workflow families, exact test definitions and explicit gaps.
Its collection guard checks registration, not semantic completeness or sign-off.
Timing definitions and privacy boundaries: [Voice timing](voice-timing.md).
The pure scorer is `ai/core/voice/validation.py`; the ignored LocalTesting entry
point is `AIMMSVoiceHarness/scripts/phase_v_summarize.py`. It makes no network or
database calls. Input carries the complete planned attempt ledger, content-free
source/flags/artifact references, failures, exclusions and linked reruns.

### Result record (one per measured cell)

| Field | Required content |
| --- | --- |
| Date, source SHA, deployed revision/image | Actual tested source and UTC run time; no inherited deployment assumption |
| Scenario, workflow variant, run/attempt ID | Exact S01–S22 and canonical receipt/artifact references |
| Method | Unit / integration / emulated / synthetic real-provider / human; never interchange |
| Outcome | PASS / FAIL / NOT RUN / BLOCKED / DEFERRED / justified N/A |
| Measurement | Attempted/planned denominator, missingness, p50/p95 nearest-rank, proxy/acoustic provenance |
| Limitation and next step | Include failed attempts and explicit reruns; never silently replace evidence |
| Tester and reviewer | Consented pseudonymous tester; actual named reviewer, decision and date |

Do not paste operational transcripts, spoken previews, customer/supplier labels,
audio, credentials, configuration dumps, SDP or network addresses into records.
An artifact hash is a reference, not independent proof that its content was
audited. Reviewers must inspect the scoped source evidence before acceptance.

### VX-T1 — accent and decision phrases

| Cell | Required evidence | Status / limitation |
| --- | --- | --- |
| US / CA / GB human accents | Decision phrases, unusual identifiers, fifteen/fifty, negation, correction, unsupported language | BLOCKED — participants and reviewer not supplied |
| US Jenny/Andrew, CA Clara/Liam, GB Sonia/Ryan | Verify selected resource availability; ≥30 real-provider base-rate turns per accent; +15% variants separately | NOT RUN — fresh scoped campaign required; virtual-mic evidence is L4, not human qualification |
| Output and vocabulary | en-US / Ava, AIMMS-owned app-scoped vocabulary fixed across comparisons | Owner choices resolved; not a measurement result |

### VX-T2 — user-owned audio routes and modes

| Cell | Required evidence | Status / limitation |
| --- | --- | --- |
| Headset / earbuds / device speaker × continuous / PTT | Actual make/model, OS/browser versions, route, quiet/noise condition, echo, interruption and accidental input | BLOCKED — no physical devices/participants supplied |
| Route/noise minimums and failing-setup degradation | Owner-selected minimum matrix and safe fallback criteria | BLOCKED — owner decision needed |
| Local screen/keyboard stop | Same-browser-clock pause latency, p95 ≤250 ms from first measured run; acoustic tail separately | NOT RUN on physical hardware; local-control proxy does not certify acoustic stop |

### VX-T3 — foreground browser and network behavior

| Cell | Required evidence | Status / limitation |
| --- | --- | --- |
| Foreground navigation/rotation, visibility and mic permission | Emulated regression plus actual browser/device result; truthful mic/audio/global state | Automated registration available; physical foreground mobile-web NOT RUN |
| Direct-route corporate / home / mobile network | Actual target network, candidate type only, success/failure and honest degradation | BLOCKED — target devices/networks unavailable; TURN remains off |
| Lost response before/after confirmation | Original-operation reconciliation and exact one-effect canonical audit; no blind resubmission | NOT RUN in a fresh Phase V campaign; historical post-receipt reconnect is narrower evidence |
| Lock-screen, native app switch/call/headset controls | Future mobile-version lifecycle work | DEFERRED — Phase F owner decision; no support claim |

### VX-T4 — human-observed safety checklist

For every in-scope workflow variant, verify current review revision/hash,
mandatory pages, correct target/permission/scope, strict confirmation where
required, idempotency receipt, exact effect count and truthful UI/speech outcome.
Exercise correction, cancel/stop, stale focus, interrupted review, permission
revocation and disconnect. A safe refusal is not successful task completion.

| Gate | Status | Required owner / evidence |
| --- | --- | --- |
| S01–S22 human-observed checklist and canonical receipt/privacy audit | BLOCKED | Actual tester and reviewer; exact scoped run |
| Notification-positive fixture | BLOCKED | Admin grant of publication permission; no agent role grant |
| Fresh experimental campaign source/flags/fixtures | NOT RUN | Separate rollout/test authorization and fresh preflight; prior campaign is closed |
| Latency / hands-free enforcement | BLOCKED | ≥30 base-rate real-provider turns per accent, six-family denominator, owner re-baselining and first enforcing phase |
| Final pilot acceptance | NOT SIGNED | Named human reviewer, decision and date |

Wrong/duplicate/stale/unauthorized effects, review integrity and truthful outcomes
remain blocking. Acknowledgment ≤1.5 s (earcons prerequisite), useful audio ≤5 s
reads / ≤8 s previews and hands-free ≥90% remain tracking targets until the owner
records the enforcement decision. Missing acoustic evidence cannot pass an audible
target. A completed template or green automated suite is not human sign-off.

### Remediation execution methods (prepared, not run)

Before VX-T1–VX-T4, record consent, pseudonymous participant ID, actual OS/browser,
user-owned audio route and quiet/noise/network condition. Do not substitute
“latest OS” or an emulation preset. Agree methods and retention with the actual
tester and named reviewer before collection.

- VX-T1: freeze the identifier/negation/correction scenarios across consented
  US/CA/GB participants. Score critical fields, holds, corrections and missing
  turns against every planned attempt. Keep synthetic input voices and +15% rate
  strata separate from human-accent evidence.
- VX-T2: compare continuous/PTT on each agreed physical route in quiet and agreed
  noise. Check echo/self-interruption and unintended input. Local Stop uses the
  monotonic proxy. Acoustic onset/tail needs an approved external method, such as
  synchronized timing/level detection retaining event times only, with clock
  alignment and uncertainty documented. This worksheet authorizes no speech
  recordings. Without an approved method, acoustic cells stay BLOCKED.
- VX-T3: exercise visibility, navigation, foreground rotation, mic permission and
  network loss on actual agreed home/corporate/mobile networks. Capture candidate
  type and explicit recovery/degradation, not IPs or SDP. Never resubmit an
  uncertain operation just to finish a test. Native/lock-screen stays deferred.
- VX-T4: predeclare exact fixtures/intents/receipts and permissions for all six
  families before live effects. Separate safe refusals from hands-free completion;
  audit one-effect outcomes and failures. Email stays paused; grants are admin work.

Owner fields remain open: actual reviewer/name/date; OD-27 BYOD route/noise minimum
and safe degradation; OD-14 measured re-baseline and first enforcing phase;
accepted exclusions; dataset/source/image/artifact references. AIMMS vocabulary
ownership is not reviewer sign-off.
