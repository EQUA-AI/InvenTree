# AI runtime readiness and startup recovery

AI initialization is supervised independently of base InvenTree liveness. Model
and embedding probes still run before installing the voice gateway. No business
request is replayed by startup recovery; no provider call runs on a probe GET.

| Endpoint | Access / result |
| --- | --- |
| `/health/live` | Minimal public base-process liveness, 200; no DB/provider work |
| `/health/ai-ready` | Minimal public cached `ready` / `not_ready`, 200 / 503 |
| `/api/ai/health` | Existing authenticated boundary; cached 200 / 503 and sanitized runtime detail |
| `/api/ai/voice/capability` | Existing authenticated boundary; configured flags preserved alongside typed `runtime` availability |

The process starts in `starting`. It publishes `ready` only when required probes
and runtime initialization finish. Temporary provider failures publish
`transiently_unavailable`; authentication, invalid configuration, model/dimension
drift and unknown initialization faults publish `permanently_failed`. Shutdown
publishes `stopping` before cleanup. AI business routes return 503 before endpoint
writes while unavailable. Health/capability remain authenticated diagnostics.
Ordinary Django remains independently usable, including bare or voice-disabled
installations. Public probes disclose no configuration; logs use fixed reasons
and normalized status, not error bodies.
If configuration fails before lifespan entry (including auth-policy creation),
no AI routes are mounted at all: a minimal 503-only responder keeps base Django
available. It supplies neither a substitute auth policy nor detailed AI data.

One supervisor permits at most three attempts per 60-second cycle, with bounded
exponential backoff/jitter and numeric Retry-After capped at 30 seconds. Exhausted
temporary cycles wait 60, 120, 240, then at most 300 seconds between cycles.
Permanent failures wait for correction/restart. An attempt owns cleanup of its
clients, gateway hooks and optional DevUI, including partial initialization.

Probe-only transports use at most ten seconds per I/O operation, reduced by the
remaining cycle budget. OpenAI SDK retries are disabled for probes using
`max_retries=0` and an explicit timeout, following the
[official Python SDK controls](https://developers.openai.com/api/reference/python).
Gemini probes use one SDK attempt and millisecond timeouts; Cohere's ordinary
throttle retry loop is bypassed during boot probes. Identity transports are
bounded too. Normal application embedding retry policy is unchanged.
An over-budget attempt cannot publish readiness. DNS/credential libraries and
transport phase timeouts are not hard thread-kill deadlines: shutdown/retries
wait for the sole probe thread to drain. Configure deployment termination grace
accordingly; never cancel its await and start an overlapping initializer.

The UI preserves configured voice capability but disables Start and explains
startup/recovery or administrator intervention. While unavailable it refreshes
capability every five seconds in a visible page; background polling stays off.
Older servers without the additive runtime field remain wire-compatible.

## Rollout checklist (operator action, not automatic configuration)

No new environment flags or migrations are required. Reuse
`FEATURE_VOICE_VALIDATION_METRICS` for optional timing evidence.

On an authorized rollout, inspect the actual image, process count and probe
configuration. Use `/health/ai-ready` readiness for the AI-enabled experimental
web revision, with independent `/health/live` liveness and the previous healthy
revision available. Do not give the voice-disabled worker a web AI-readiness
requirement. Use local fault/recovery fixtures, not injected faults against shared
provider traffic; verify the candidate before promotion. Readiness is per process
and is not continuous provider-outage monitoring.

Compare permissions before/after a controlled restart with
`python manage.py audit_role_permissions` (optionally repeated `--group <id>`).
The command is read-only, including startup hooks. It reports managed
adds/removals and missing definitions; it never restores historical grants.
Review unexplained deltas instead of applying blanket grants. SQLite tests cover
deterministic projection; row-lock concurrency needs private PostgreSQL before
claiming deployment-specific concurrency qualification.
