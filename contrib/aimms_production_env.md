# AIMMS production env rollout (pilot deployment)

Ordered, phased environment flips for the production posture (owner
decisions 2026-08-29: the pilot is the production test — full
functionality, quality enforcement on, caps raised not removed,
retention jobs dark). Apply each phase as **one revision** so rollback
is a single traffic-weight command. Values in `<...>` are
deployment-specific; nothing here is a secret.

Code defaults stay conservative — this manifest IS the posture.

**Deploy path (CR-1, 2026-09):** `LocalDocs/scripts/aimms_deploy.sh` is the
only sanctioned deploy path for the four apps. It takes the image digest
from `az acr repository show --image`, updates web then worker, runs the
previous revision's `manage.py check` smoke against the migrated database
(CR-9), sets traffic to the new revision (the apps are explicit-traffic
multi-revision: without `ingress traffic set` a new revision serves
nothing), drains, and reclaims 0%-weight revisions so no app carries more
than three active revisions. `LocalDocs/AZURE_ACR_CONTAINERAPP_COMMANDS.md`
stays as the reference for the individual commands, never as a procedure.

## Phase A — product reads + quality enforcement

```
AIMMS_WORK_ORDERS_ENABLED=True
AIMMS_MACHINE_AI_READ_ENABLED=True
AIMMS_MAINTENANCE_AI_READ_ENABLED=True
AIMMS_MAINTENANCE_SCOPE_RESOLVER=tasks.scope.granted_client_scope_resolver
AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=repair.diagnostic_scope.single_site_diagnostic_capability_resolver
AIMMS_SINGLE_SITE_CLIENT_CODE=<pilot client code>
AIMMS_PLANT_TIMEZONE=<IANA name, e.g. Australia/Sydney>
FEATURE_AI_ANALYSIS_ROUTER_ENFORCE=1
AIMMS_EVIDENCE_GATE_MODE=enforce
AIMMS_MANUAL_GROUNDING_MODE=enforce
MODEL_VERSION_BOOT_PROBE_ENABLED=1
FEATURE_TYPED_TURN_FAILURES=1
# S7/S9 per-intent staging: intents listed here keep the legacy rail
# (shadow-soaked) despite their shipped executors. Clear one name from
# the csv to flip that intent to validated answers; re-add it to roll
# one intent back without a deploy. Empty = everything validated.
AIMMS_ANALYSIS_INTENT_HOLDBACK=fleet_aggregate,trend_analysis,manual_wo_comparison
```

Notes: router enforce + gate enforce ship together, but the coupling is
now STRUCTURAL — a later rollback of the gate automatically returns
every analysis intent to the legacy rail (no refusals possible).
`AIMMS_PLANT_TIMEZONE` is the analytics calendar (time buckets, date
windows); empty falls back to the server `TIME_ZONE` and every answer
names the zone it used.

**S7/S9 rollout (per intent, no deploys):** ship with the holdback csv
above; watch the legacy shadow scans + `analysis_router.divergence
reason=intent_holdback` lines for a few days; then clear
`fleet_aggregate`, later `trend_analysis`, later `manual_wo_comparison`
— one env edit each. Gate for the first flip: the 25k benchmark
(`manage.py test tasks.tests.test_analytics_load --keepdb` on the
production-shaped DB) passes its five criteria. S9's manual route also
wants verified applicability rows (Phase B½ below) before it can serve
manual comparisons; structured-procedure comparisons work without them.

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

### Phase B½ — verified applicability (S8b, human workflow)

No env flip — a staffing prerequisite plus data work. Grant
`aichat.verify_document_applicability` (maintenance management) and
`aichat.countersign_document_applicability` (engineering) to NAMED
humans; the proposer can never verify their own claim, and
model/configuration claims need both signatures. Then:

```
manage.py applicability_backfill --by <proposer> --json   # dry run
manage.py applicability_backfill --by <proposer> --yes    # proposed rows
manage.py applicability_report                            # the human queue
manage.py applicability_verify --claim <id> --by <verifier>
```

Verified rows flip document `applicable` states from unresolved to
verified, open the serial-less machines' verified-document route, and
are what S9's manual-route comparisons require. Without named holders
everything stays `proposed` and nothing changes — by design.

## Phase C — user features

```
FEATURE_THREAD_SHARING=1
FEATURE_TOKEN_STREAMING=1
FEATURE_TOOL_EVENTS=1
FEATURE_QUESTION_CARDS=1
FEATURE_AGUI_ENDPOINT=1
FEATURE_THREAD_COMPACTION_SHADOW=1
FEATURE_THREAD_COMPACTION=1   # live on aimms-experimental since 2026-08-13 (revision --0000040); accepted in writing 2026-09-02 (D-02) on gpt-4.1 pending the CR-2 routing override, due with the Pre-work core
```

Per-app compaction posture (verified 2026-09-01; the earlier "shadow
only" wording of this manifest was stale):

| App | `FEATURE_THREAD_COMPACTION` | `..._SHADOW` | `FEATURE_MODEL_TIERING_ENFORCE` | Role |
|---|---|---|---|---|
| aimms-experimental (web + AI mount) | true | true | true (proven no-op under the identity table, `test_model_policy`) | enqueues at backlog >= 16 and consumes the summary note |
| inventree-worker | unset | unset | unset | runs `compact_thread_summary` -> `_summarize`; the worker env decides the deployment |
| aimms-dev / aimms-dev-worker | unset | unset | unset | parity catch-up in M2 |

D-02 failure action (i): if the routing override below is not live on both
workers by 2026-09-19, `FEATURE_THREAD_COMPACTION` is paused on
aimms-experimental.

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

## Worker apps (compaction and memory extraction run here)

`compact_thread_summary` (and, from M3a, memory extraction) execute on
inventree-worker / aimms-dev-worker via `offload_task(force_async=True)`
(`aichat/services/threads.py`), so the WORKER env — not the web app's —
governs `select_deployment` for these calls. The tiering flags are
irrelevant to this path: both policy branches resolve SUMMARIZATION and
EXTRACTION to the override or the standard tier, never the fast tier
(D-10; `ai/core/model_policy.py`).

```
AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT=gpt-5.6-luna-dz   # interim gpt-5.6-luna until the DataZoneStandard deployment exists; empty = standard tier
AZURE_SUMMARIZATION_REASONING_EFFORT=low                 # sent only when the override is set
# Remainder window (CR-4/CR-6), not yet live:
# Q_CLUSTER_NAME=ai-memory
# MEM0_TELEMETRY=false
# MEM0_DIR=/tmp/mem0
# AIMMS_EGRESS_MODE=enforce
```

Pre-edit gate for the override: `manage.py compaction_model_probe
--deployment <override>` on the worker prints `schema_ok=true` and
`seed_leaked=false` (strict `json_schema` and `reasoning_effort` accepted
on the reasoning deployment; the compaction payload passes
`ai/core/redaction.py` before the call). Restart the managed-identity
callers after the env edit.

## Phase F — semantic memory (posture B, dark until M3a)

Declared here so the flag names are fixed before the code exists; the
Settings fields and their `aimms_flags.py` registry rows land with M3a
(a registry row without its field fails `test_flag_registry`). Dev first,
then experimental; every flip cites its gate-suite run id (GR-47).

```
# FEATURE_SEMANTIC_MEMORY_EXTRACT_SHADOW=0
# FEATURE_SEMANTIC_MEMORY_RECALL=0
# FEATURE_SEMANTIC_MEMORY_MEM0=0
```

## Phase G — M1 context builder (dark until the D7 exit gate)

Both knobs ship in the Part C image (commits `d1a7b4f30`..`823d61ac6`,
2026-09-05) with defaults that change nothing. The builder itself is
always on: every rail reads one typed `ContextBundle`, wf8 replays it
exactly as the pre-builder dict did, and the fenced compaction note plus
the routing classifier's typed inputs are live without a flag. Each flip
below cites its `followup_parity` run id (GR-47) and gets its own
revision so a rollback is one `ingress traffic set`.

```
FEATURE_MEMORY_RAIL_REPLAY=1           # replay the bundle on wf2/wf3/wf4/wf6 and wf1 step 1; wf8 ungated, wf9 history-free by design
AIMMS_PROMPT_CACHE_KEY_DEPLOYMENTS=<standard>,<fast>   # csv of deployments that receive prompt_cache_key=<client>:<thread>:<mode>; empty = no key sent
AIMMS_PROMPT_CACHE_RETENTION=24h       # "" (provider default 5-10 min) | in_memory | 24h — rides only where the key rides
FEATURE_PROMPT_CACHE_STABLE_TOOLS=1    # wf8 keeps the thread's earlier packs so consecutive turns share one tool prefix (GR-33)
```

Posture 2026-09-05: all four ON on the dev pair (`aimms-dev--0000091` /
`aimms-dev-worker--0000021`, key deployments `gpt-5.1,gpt-4.1`); the
experimental pair still runs the pre-Part-C image with the four dark —
its flip (key deployments `gpt-5.6-luna,gpt-4.1`; luna accepts key + 24h
on 2025-04-01-preview, 1,939 of 1,942 cached on the probe) rides the next
experimental deploy together with the granted scope resolver.

Cache facts measured on dev 2026-09-05: Azure accepts `prompt_cache_key` and
`prompt_cache_retention=24h` on api-version 2024-10-21 for gpt-5.1 and gpt-4.1
(1,920 of 1,943 tokens cached on the second call); the live wf8 request misses
because the broker changes the tool definitions between turns, which is what
`FEATURE_PROMPT_CACHE_STABLE_TOOLS` fixes. Enable the three together per
environment and cite the golden + `cache_capture_probe` run ids.

`AIMMS_PROMPT_CACHE_KEY_DEPLOYMENTS` stays empty until
`manage.py prompt_cache_probe --mode i` has passed on that environment
(dev gpt-5.1: cached 1024/1078 on 2026-09-05; experimental not yet run).
Neither knob is read on the worker path.

## Explicitly dark (owner decisions)

- `FEATURE_AI_RETENTION_JOBS` — data is kept (2026-08-29); only the
  ungated 24h upload sweep + deletion outbox run regardless.
- `FEATURE_AI_QUOTA_PROFILES` — was skipped (the v1 budget + admission
  envelope suffices for users); switched ON on both pairs 2026-09-05 for the
  M1 battery, whose preflight refuses any profile but `evaluation`. Users
  without an assignment keep the standard limits (identical to v1); only the
  battery principal carries a 14-day `evaluation` assignment. Two more
  battery prerequisites ride with it: `MODEL_VERSION_BOOT_PROBE_ENABLED=1`
  with the three `AZURE_OPENAI_EXPECTED_*` pins (needs image `771528213`
  or later — earlier probes sent a 1-token cap that gpt-5.x rejects and
  refused the boot), and a shared Django cache (`INVENTREE_CACHE_HOST`)
  because the preflight refuses a LocMem counter store — still open.
- NLI groundedness, wf8 fast tier, history enrichment, guided
  procedures — out of scope, dark.
- `FEATURE_MODEL_TIERING_ENFORCE` is set on aimms-experimental (P5) and
  is a proven no-op under the identity table; it does not govern the
  worker path (see Worker apps).
- Voice family — left exactly as currently deployed (owner to decide
  separately for the pilot customer).

## Smoke checks per phase

- A: an individual-record analysis question returns a validated evidence
  answer; a fleet-count question returns a legacy-rail answer while its
  intent is held back (never a refusal). After clearing an intent from
  the holdback csv: "how many work orders per machine" returns a
  validated breakdown table naming its date field and timezone; a
  "per technician" grouping returns the named grouping-unavailable
  message; "did WO-… follow the procedure" returns the six-status
  comparison with the compliance-disabled boundary.
- B: asking about an out-of-scope machine yields the recoverable
  scope-miss offer, not silence.
- C: streaming visible on legacy answers; a share/revoke round-trip.
- D: upload a PDF → it becomes searchable; image query returns media.
- E: `pilot_stop` drill engages + `pilot_resume` ×5 clears; preflight
  shows `pilot_stopped` transitions.
