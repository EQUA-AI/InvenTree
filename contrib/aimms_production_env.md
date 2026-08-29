# AIMMS production env rollout (pilot deployment)

Ordered, phased environment flips for the production posture (owner
decisions 2026-08-29: the pilot is the production test — full
functionality, quality enforcement on, caps raised not removed,
retention jobs dark). Apply each phase as **one revision** so rollback
is a single traffic-weight command. Values in `<...>` are
deployment-specific; nothing here is a secret.

Code defaults stay conservative — this manifest IS the posture.

## Phase A — product reads + quality enforcement

```
AIMMS_WORK_ORDERS_ENABLED=True
AIMMS_MACHINE_AI_READ_ENABLED=True
AIMMS_MAINTENANCE_AI_READ_ENABLED=True
AIMMS_MAINTENANCE_SCOPE_RESOLVER=tasks.scope.granted_client_scope_resolver
AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=repair.diagnostic_scope.single_site_diagnostic_capability_resolver
AIMMS_SINGLE_SITE_CLIENT_CODE=<pilot client code>
FEATURE_AI_ANALYSIS_ROUTER_ENFORCE=1
AIMMS_EVIDENCE_GATE_MODE=enforce
AIMMS_MANUAL_GROUNDING_MODE=enforce
MODEL_VERSION_BOOT_PROBE_ENABLED=1
FEATURE_TYPED_TURN_FAILURES=1
```

Notes: router enforce + gate enforce ship together, but the coupling is
now STRUCTURAL — a later rollback of the gate automatically returns
every analysis intent to the legacy rail (no refusals possible). Fleet /
trend / comparison questions stay on the legacy rail until the S7/S9
executors land.

## Phase B — scope enforcement (gated)

Gate: `manage.py audit_scope_serials --client <pilot>` exits 0
(blank machine serials would silently drop manual search for scoped
threads — fix the data first).

```
FEATURE_AI_THREAD_SCOPE_ENFORCE=1
```

After ~1 week of clean operation: `manage.py arm_rollback_floor --yes`
(one-way: scope enforcement, the unsafe-shortcut guard, and fixture
isolation become permanent).

## Phase C — user features

```
FEATURE_THREAD_SHARING=1
FEATURE_TOKEN_STREAMING=1
FEATURE_TOOL_EVENTS=1
FEATURE_QUESTION_CARDS=1
FEATURE_AGUI_ENDPOINT=1
FEATURE_THREAD_COMPACTION_SHADOW=1
# FEATURE_THREAD_COMPACTION=1        # after ~1 week of shadow review
```

Streaming applies to the legacy text rail only; validated evidence
answers stay buffered structurally.

## Phase D — document + media RAG (gated on infra checklist)

Checklist first: (1) ffmpeg/ffprobe in the image — `manage.py
rag_video_preflight`; (2) Document Intelligence client constructs on the
worker (boot log clean); (3) indexes exist — `manage.py
create_rag_search_indexes`; (4) `AZURE_OPENAI_ENDPOINT` present.

```
AIMMS_ATTACHMENT_RAG_ENABLED=True
FEATURE_ATTACHMENT_RAG_INGEST=1
FEATURE_ATTACHMENT_RAG_RETRIEVAL=1
COHERE_EMBED_ENDPOINT=<https endpoint>
COHERE_EMBED_MODEL=<model>
AZURE_SEARCH_ATTACHMENT_DOCS_INDEX=<index>
AIMMS_MEDIA_RAG_ENABLED=True
FEATURE_MEDIA_RAG_INGEST=1
FEATURE_MEDIA_RAG_RETRIEVAL=1
GCP_PROJECT_ID=<...>
GCP_LOCATION=<...>
GCP_CREDENTIALS_PATH=<WIF json path>
GEMINI_EMBED_MODEL=<...>
AZURE_SEARCH_MEDIA_INDEX=<index>
AZURE_DOC_INTELLIGENCE_ENDPOINT=<...>
AZURE_DOC_INTELLIGENCE_KEY=<secret ref>
```

## Phase E — ops envelope (gated on shared cache)

Gate: the default Django cache on BOTH planes is the shared Redis
(`GET /api/ai/quota/preflight` reports `store.shared: true`; LocMem
disqualifies — the armed kill switch fails closed on an unreadable
cache, by design).

```
FEATURE_AI_PILOT_STOP_LATCH=1
AIMMS_PILOT_STOP_OWNERS=engineering:<u>,product:<u>,operations:<u>,quality:<u>,customer:<u>
FEATURE_DISTRIBUTED_RATE_LIMIT_ENFORCE=1
FEATURE_AI_ADMISSION_CONTROL_ENFORCE=1
FEATURE_TOKEN_BUDGET_ENFORCE=1
AI_ADMISSION_MAX_ACTIVE_PER_USER=4
AI_ADMISSION_MAX_ACTIVE_GLOBAL=25
AI_USER_DAILY_TOKEN_BUDGET=5000000
AI_RATE_CHAT_PER_MINUTE=30
AI_RATE_CHAT_PER_HOUR=600
AI_RATE_USER_PER_MINUTE=60
AI_RATE_USER_PER_HOUR=1200
AI_RATE_GLOBAL_PER_MINUTE=300
```

## Explicitly dark (owner decisions)

- `FEATURE_AI_RETENTION_JOBS` — data is kept (2026-08-29); only the
  ungated 24h upload sweep + deletion outbox run regardless.
- `FEATURE_AI_QUOTA_PROFILES` — skipped (the v1 budget + admission
  envelope suffices).
- NLI groundedness, model-tiering enforce, wf8 fast tier, history
  enrichment, guided procedures — out of scope, dark.
- Voice family — left exactly as currently deployed (owner to decide
  separately for the pilot customer).

## Smoke checks per phase

- A: an individual-record analysis question returns a validated evidence
  answer; a fleet-count question returns a legacy-rail answer (never a
  refusal).
- B: asking about an out-of-scope machine yields the recoverable
  scope-miss offer, not silence.
- C: streaming visible on legacy answers; a share/revoke round-trip.
- D: upload a PDF → it becomes searchable; image query returns media.
- E: `pilot_stop` drill engages + `pilot_resume` ×5 clears; preflight
  shows `pilot_stopped` transitions.
