# Chat visualization repairs — 2026-10-25

Baseline: EQUA `657e70b0ed031e8589fd27bbfbe8d5c1117c7757`. Implements the repair plan following the two retest JSON reports. Source changes stay on `equa/customizations`; no new runtime dependency or database migration is required.

## Resulting behavior

- Parts lookups have their own read intent and retain parts tools. Presentation labels and chart creation do not become business writes; separate positive effects still retain the existing write boundary.
- Explicit machine IDs (including a name followed by `(ID 24)`) and acknowledged selected-machine references resolve under current authorization. The effective per-turn predicate narrows analytics and work-order retrieval without changing persistent focus or analysis scope. Conflicting/out-of-scope/unavailable targets never trigger a broader search. Identity cards use the native authorized identity projection.
- Recorded monthly work-order trends use the existing validated snapshot executor, with 1–36 calendar months, source/timezone/window/coverage and a supporting table.
- A dedicated `status_comparison` analysis intent counts the complete filtered population, independently of display-page size. Open includes draft/planned/ready/in_progress/on_hold/verifying; completed and canceled are separate and reconcile to the population. Unknown stored statuses cannot silently become Open.
- Supplied synthetic `label=value` series render line/bar/pie charts without model-generated code or operational-data queries. Values and category order are preserved; zeros remain zero; pie totals must be positive and nonnegative. Invalid/duplicate/nonfinite/unbounded values and unsupported requests produce explicit limitations.
- Version-two chart snapshots carry recorded versus supplied provenance and server references. Version-one component meanings and historical rendering remain supported. `MetricComparison` continues to mean retrieval coverage. Shared readers retain the existing conservative projection.
- A targeted answer no longer attaches unrelated machines from a broad returned page. Streaming/replay/history use the same terminal snapshot and never fetch current values to redraw history. Missing references and renderer faults preserve readable canonical text.

## Contracts and implementation

The backend owns the bounded `PresentationChartV2`/point schema and generates TypeScript through `generate_wire_contract`. Recorded chart candidates attach only after an evidence-gate pass. Supplied snapshots cannot claim recorded coverage. The frontend validates the complete snapshot before rendering, retains its table/provenance, and resolves `ChartSnapshot` only in `aimms-chat@2`. Both versions retain flat literal-reference programs and existing byte/node/reference budgets. Packaged OpenUI artifacts are regenerated through `presentation:generate`; dependency versions and the lockfile are unchanged.

Key paths: `ai/core/presentation/{requests,provided,datasets,wire,builder}.py`, `ai/core/turn/{targets,routing,execution,finalize}.py`, `ai/core/analysis/{intent,scope_context,executor,synthesis}.py`, `ai/core/tools/capabilities.py`, authorized work-order reader/tool, and frontend `aichat/genui`.

## Qualification and reproduction

The committed `ai/core/evals/chat_visualization_retest.json` retains all 19 exact reported prompt shapes and expected outcomes. The deterministic replay checks read/effect intent and parts-tool exposure; it is not a live-provider correctness claim for all 19 questions. `test_chat_visualization_retest.py` additionally exercises real scoped ORM/turn execution, >25-row full counts, target identity, six-month data, 37-month limitation, independent target filtering, explicit-scope conflict, revocation, supplied data and immutable replay. Only the optional model organizer is replaced in those deterministic tests.

Observed checks:

- Full relevant backend battery: **2,334 pass; 15 pre-existing manual-source structural-grammar failures**, with identical failure IDs to the published baseline. No new functional failure remains. An earlier timing check under concurrent load exceeded 100 ms; its isolated replay and final full run pass. The final rerun also verifies optional turn-metadata compatibility after the last routing hardening.
- **234 frontend unit checks pass**, including strict version/provenance/coverage/value boundaries; TypeScript passes.
- **18 new optimized browser checks pass**: actual line/bar/pie graphics, zero/percentage values, light/dark at 390/1440 px, accessible tables, history, both streaming transports, and actual backend-produced status/trend/supplied DTOs. Screenshots inspected.
- **24 existing optimized presentation/workspace/overlay checks pass**. Both source-module fault injection checks pass on Vite development; these deliberately target development source URLs and are not optimized-build tests. Initial local dependency/HMR and untranslated-ID failures are retained in evidence, not counted as successes. Frozen dependencies and the correct `lingui compile --typescript` command resolved the setup.

Backend qualification uses the release Python 3.14.7 / actual installed legacy SDK, a disposable SQLite database, pytest-socket, and loopback-only HTTP allowances. Run from `src/backend/InvenTree` with `DJANGO_SETTINGS_MODULE=ai.core.tests.settings`, that directory and `src/backend` in `PYTHONPATH`; pytest `-p no:django --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1`. These pytest islands bootstrap disposable tables; native Django test runs continue to require `--keepdb`.

Frontend: frozen Yarn install; `presentation:generate` / `presentation:check`; `tsc --noEmit`; the chat Vitest modules; optimized drawer build and `playwright.chat.config.ts`. Set `AIMMS_TEST_PRESENTATION_ARTIFACT_DIR` during ORM tests and browser checks to bridge actual generated DTOs. Use Vite development for the two fault injection tests. New tests and snapshots are fixture evidence, not operational plant records or physical-phone/screen-reader acceptance.

Evidence directory: `/home/lokesh/.hermes/workspaces/ai-viz-implementation-20261025-pcw8wdd1`. Private provider/configuration logs must not be published.

## Real-provider localhost probe

A separate disposable localhost native Part API and ordinary read-role account were exercised through the actual installed SDK/model and T1 workflow. Native API preflight passed and the exact part-954 question returned the required recorded name/IPN on its first actual model attempt. The part-search attempt and an explicitly recorded follow-up stopped before any tool/native API dispatch because the model connection timed out (`APITimeoutError` -> `ConnectTimeout`). This is a provider-connectivity limitation of the local probe; neither attempt is counted as a semantic pass. No operational records or Azure app UI were queried. Credentials were mounted read-only into the temporary process, never printed or copied into source. Initial fixture role-permission setup failures occurred before provider traffic and remain in private evidence.

## Scoped release configuration

OpenUI display alone does not activate recorded charts. The qualified web profile is:

```dotenv
FEATURE_AI_ANALYSIS_ROUTER_ENFORCE=true
AIMMS_EVIDENCE_GATE_MODE=enforce
AIMMS_ANALYSIS_INTENT_HOLDBACK=record_retrieval,manual_fact,source_inventory,fleet_aggregate,manual_wo_comparison
```

This enables only qualified trend and status-comparison executors; unrelated analysis families stay on their established rails. Supplied charts use the presentation switch and validated bounded input. Keep independent presentation/router/evidence rollback switches. Do not flip global source defaults or enable every analysis family.

Release the exact clean commit through the existing ACR `aimms` / repository `aimms-dev`, with the same immutable image for web and worker. Update only the web's three analysis settings, image/provenance and revision suffix. Preserve all other configuration, secrets, registry access, identities, commands, scale and probes. Verify candidate and ordinary host identity/readiness, effective runtime settings, packaged artifacts, worker heartbeat and before/after configuration before retiring the previous web revision. No migration or operational data loader is needed. Azure verification uses health/configuration reads; functional testing remains on localhost. Retain the old image and exact old analysis settings for rollback.
