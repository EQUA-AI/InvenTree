# AIMMS modern-MAF harness migration: execution plan

## 1. Status, scope and frozen baseline

**Status:** Planning and isolated setup completed in part; admin preservation-inventory and staging-test planning decisions are recorded, but full H0 acceptance is pending. No application runtime/dependency/schema/feature-flag/CI changes or Azure deployment changes have been made by this setup work. Implementation phases below are future work, not completed results.

The user authorized a full phased MAF migration, selected `mimo-coding-supervisor`, designated themselves admin/feature approver, and selected `aimms-dev` for staging. The current continuation is **planning and setting up**, not an instruction to deploy a partially qualified runtime.

| Item | Approved/verified value |
|---|---|
| Source lineage | `equa/customizations` |
| Frozen source commit | `05d47269bdd58febafea6f91542ad37b54ed7fdc` |
| Migration branch | `feat/aimms-maf-harness` |
| Isolated worktree | `/home/lokesh/Documents/mbpro/InvenTree-MAF-Harness` |
| Protected original checkout | `/home/lokesh/Documents/mbpro/InvenTree` |
| Durable local evidence | `/home/lokesh/Documents/mbpro/aimms-maf-migration-evidence` |
| Admin/product approver | Requesting administrator |
| Staging web/worker | `aimms-dev` / `aimms-dev-worker` |
| Azure scope | Subscription `5b75a75a-fff3-4d72-a3e9-5e16cb6a8687`, resource group `EpconChat`, environment `epcon-ai-env`, East US 2 |
| Supervisor / implementer | Current GPT chat / Xiaomi `mimo-v2.6-pro` worker |

The nonexistent singular `equa/customization` was not silently substituted. The admin explicitly approved the plural branch at the full SHA and excluded uncommitted work. Do not rebase/advance this base or import dirty lookalike files without another scope decision. `equa/production` remains outside this setup.

Repository instructions require human review before opening a PR and prohibit AI-generated issues. No commit, push, PR, issue, or automatic merge is authorized by this plan. Later Azure traffic/config/database/effectful changes require the reviewed phase and exact staging-operation scope.

The originating research document is `EQUA_AIMMS_AI_HARNESS_OPTIONS_AND_MIGRATION_PLAN.md`, supplied as an attachment. This execution plan turns its H0–H9 and T01–T50 gates into source-specific work packages; it does not certify the research's assumptions as deployed facts.

## 2. Verified setup and evidence boundaries

### Git and preservation

A clean worktree was created on the migration branch at the frozen commit. At the pre-work preservation capture, the original checkout had 59 modified and 80 untracked entries. A later check found unchanged original HEAD, branch, index, porcelain status and recorded non-secret WIP hashes. Do not treat those counts as a license to read credentials; no original `.env` or credential file is part of the migration input.

Evidence: `original-worktree-preservation.json` and `original-worktree-verification.json` in the durable evidence directory. A historical session-start snapshot with a different untracked count is not the preservation baseline for this setup.

### Runtime lanes

Task-owned Docker volume: `aimms-maf-harness-setup-fe1e6a7f`, context `default`.

Local immutable dependency-carrier image:

`sha256:b6ce427887301a8d10058e59d55ee99f0e01f52ce4ef7e3e250687afa557fc07`

This image is **not** the deployed `aimms-dev` image and **not** a build of the migration branch. It is a dependency carrier for read-only source tests and isolated SDK inspection. Its observed Python is `3.14.7`; pinned MAF core/DevUI are `1.0.0b251120`, Django `5.2.17`, OpenAI `3.18.0`. Its package check passed.

| Lane | Interpreter inside driver container | Isolation | Result |
|---|---|---|---|
| Baseline | `/scratch/baseline-venv/bin/python` | System packages from pinned carrier, pytest `9.1.1`, pytest-asyncio `1.3.0`; source mount read-only | `pip check` passed; focused tests executed |
| SDK candidate | `/scratch/candidate-venv/bin/python` | Fresh venv, no baseline MAF overlay | Joint install and `pip check` passed; actual API and no-call harness-construction probes executed |

Isolated candidate set, **provisional for H2**, not an application lock:

- `agent-framework-core==1.20.0`
- `agent-framework-openai==1.15.0`
- `agent-framework-foundry==1.14.0`
- `agent-framework-orchestrations==1.3.0`

Registry metadata and wheel hashes were retrieved from PyPI; the resolved candidate freeze is retained. The repository's September candidate (`1.17.0` / `1.14.2` / `1.12.0` / `1.1.1`) remains unchanged. Neither set is approved for production simply because a slim environment resolves.

Tests bypassed the image's normal entrypoint, had networking disabled, used task-volume temporary SQLite/test outputs, and imported only the read-only migration checkout. They did not start workers or perform application startup/migrations against Azure. The original application's `.venv` was not modified.

### Executed baseline tests

| Run | Actual result | Qualification |
|---|---|---|
| Focused factory/pins/RBAC/guard/trusted-context/memory/replay, seven modules | **72 passed, 2 skipped, 1 warning**, exit 0 | GA-only tests skipped on pinned MAF; no authenticated live acceptance |
| Expanded identity/permissions/scope/modality/write-governance/budgets/telemetry, 23 unique modules | **387 passed, 22 setup errors, 4 subtests passed, 1 warning**, exit 1 | Not a green suite; unresolved baseline bootstrap errors are retained |
| Pinned image + each isolated venv package check | No broken requirements, exits 0 | Package consistency only |
| Candidate API inspection | `Agent`, `HistoryProvider`, `create_harness_agent`, `Message`, `Content` present; `ChatAgent`, `ChatMessage`, `TextContent` absent | Real import/signature evidence, not application compatibility |
| Candidate inert harness construction | Returned an actual MAF `Agent`, zero model calls and zero tools | Does not verify model requests, guard interception, approvals or persistence |

The expanded run's 22 errors arise in `test_multiturn_isolation.py` and `test_pilot_latch.py` while their fixtures migrate under `ai.core.tests.settings`. The error names `aichat.0042_memory_consent`'s dependency on `assets.0015_alter_machineanomaly_work_order`. That assets migration **exists in the pinned source**, but the minimal settings' `INSTALLED_APPS` omits `assets`. This identifies a test-bootstrap mismatch, not evidence that the deployed/full-app migration graph is broken. Qualify these cases under a complete disposable test bootstrap; do not use `--fake`, remove assertions, rewrite applied migrations, or convert the failed run into a pass.

Both test runs emitted a Django-Q warning about retry/timeout ordering in the isolated test settings. Inspect and qualify the test configuration; it does not prove the same problem exists in deployed queue configuration.

Evidence: `baseline-focused-tests.json`, `baseline-security-tests.json`, `baseline-test-summary.json`, `local-runtime-probe.json`, `isolated-environment-*.json`, `candidate-api-probe.json`, `candidate-harness-construction.json`, `candidate-package-freeze.txt`.

## 3. Read-only `aimms-dev` baseline

ARM metadata captured at `2026-10-05T01:09:41.656670+00:00`:

| Property | Web | Worker |
|---|---|---|
| Ready revision | `aimms-dev--0000132` | `aimms-dev-worker--0000062` |
| Reported status | Running | Running |
| Mode | Multiple | Single |
| CPU / memory | 1 CPU / 2 GiB | 2 CPU / 4 GiB |
| Scale | min 1, max 2 | min 1, max 1 |
| Auto-update template | `True` | `False` |

Both app templates referenced:

`aimms-hjcxb6epgvhgbyge.azurecr.io/aimms-dev@sha256:401114b23a56ca50ae8f7814bb76d68e9ff52a9d12e9012fd99b75ac1f5bac94`

Web traffic was 100% on `aimms-dev--0000132`; another labelled revision had zero configured traffic. Template database engine: PostgreSQL. Allowlisted template commit identity: `3417cf5a233c026abd2f71da0eea3d97d9ec5e85`, different from the approved source.

Public GET probes: `/health/live` 200/alive; `/health/ai-ready` 200/ready; `/api/aichat/ui/capabilities/` 401 without authentication; `/static/web/build-info.json` 404. These results establish limited health/access behavior, not authenticated feature or immutable build identity.

**Deployment blockers to resolve before H3/H8 staging changes:**

1. Attest actual deployed backend/frontend commit, image runtime, effective flags and worker queues. ARM commit values and shared digest alone are insufficient.
2. Review the source-to-deployed-commit gap and additive migration histories before using this database. Do not assume `aimms-dev` is disposable or unused.
3. Choose one migration owner explicitly. Web auto-update true / worker false is a configuration difference requiring review, not permission to flip flags now.
4. Establish authenticated admin and least-privilege acceptance paths using approved secret handling. No token/credential extraction into chat, reports or worker prompts.
5. The admin selected an isolated test customer, reversible fixtures and mocked outbound email/orders as the planning boundary. Name and approve the actual fixtures/actors before live changes. This is not deployment, account/RBAC, real-customer-write or external-integration authorization. Real outbound email, customer orders, stock or maintenance records are not test fixtures by default.
6. Confirm shared DB/cache/queues/storage consumers and safe recovery/backups before any live schema/run-state changes.

No Azure settings, images, scale, revisions, traffic, credentials, roles or records were changed during discovery. Evidence contains names/references or explicit non-secret settings, not secret values.

## 4. Architecture and invariants

### Selected design

Modern MAF remains an embedded Python execution library under the existing authenticated `/api/ai` ASGI mount. Existing Django-Q workers remain the continuation/ingestion estate unless a measured need justifies a separately reviewed change. Existing Azure model deployments and API policy stay fixed during framework comparison. A Foundry client that invokes a service-managed agent is not equivalent to an in-container orchestration loop.

Retain `AIPrincipal`, trusted context, native rulesets/named permissions, row/customer scope, canonical capability IDs, proposal/approval lifecycle, evidence validation, thread services and UI wire contracts as application authority. The SDK proposes/plans; it never grants identity or permissions.

Proposed harness boundaries are start/continue/cancel, authorized dispatch, context/history bridge, event bridge, approval bridge and versioned state store. These are responsibilities, not existing importable APIs. Prefer extending existing seams over duplicating authorization implementations or inventing a new role system.

### Source-specific seams and required changes

| Boundary | Existing source | Required migration behavior |
|---|---|---|
| Auth and lifecycle | `InvenTree/asgi.py:20–84`, `ai/core/auth.py`, `trusted_context.py` | No public route/auth replacement; retain non-AI resilience, origin/CSRF and server-derived actor |
| Construction | `ai/core/agents/factory.py:31–77` (`AgentSpec`, `build_agent`) | Adapt actual modern `Agent(client=...)`/harness signature; no new constructor business tools outside reviewed capability selection |
| Provider | `ai/core/integrations/azure_openai_client.py:74,134` | Modern `OpenAIChatClient` shape differs; capture Azure deployment/API/auth/limits/request options rather than rename blindly |
| Per-run tools | `ai/core/workflows/rbac_run.py:97–126` | Select canonical objects before SDK wrappers, bind actor/workflow/modality and stable IDs outside model content |
| Invocation | `ai/core/tools/invocation_guard.py:170,291,306` | Reuse one authoritative policy at real dispatch; hard-deny new migrated business paths; verify actual modern middleware execution |
| Memory | `ai/core/memory/maf_adapter/_replay.py:7`, `_ga.py`, context builder | Replace old message types; single app history/replay/compaction owner, scoped untrusted history preserved as data |
| Approval | `aichat/services/proposals.py:48,92`, global approval app | Exact-action application decisions and one executor; SDK approval is only a signal |
| Voice | `ai/core/voice/write_gate.py`, `action_policy.py`, `config.py:478,521` | Preserve read-only lookup fence and enabled confirmed-write lane; no autonomous effects or privilege gain |
| Other model callers | Analysis, grounding, image captioning, memory extraction, model probes and `aichat/tasks.py:689` | Inventory and test direct calls too; changing the agent factory does not cover these paths |

Two actual SDK discoveries are important:

- Current replay uses `ChatMessage`/`TextContent`, absent in the inspected modern set. The existing “GA” seam is not proof of complete current-modern compatibility.
- Even with new capabilities disabled and `context_providers=[]`, the harness creates an `InMemoryHistoryProvider` by default with loading/storing enabled. Supply the application bridge explicitly and test ownership; do not assume an empty argument suppresses default history.

Do not install two incompatible versions of the `agent_framework` namespace into one Python environment. First make all retained workflows/API adapters compatible with one selected modern set, then enable harness behavior by workflow/run ownership. Package rollback requires a compatible image/state plan, not a flag selecting an uninstalled beta.

## 5. H0–H9 phase gates

All phases after the initial setup are **not started**. The admin accepted the inventory as written and selected the isolated/mocked staging-test plan. The corrected probe has passed scoped parent verification and is retained as a partial static inventory; its worker timebox was not met. H0 remains open pending complete dispatch/dependency qualification, baseline bootstrap qualification, authenticated live posture and named/approved test fixtures. The approval record and original accepted document hash are retained in `admin-planning-acceptance.json`.

| Phase | Work and deliverables | Exit gate / acceptance coverage | Approver |
|---|---|---|---|
| H0 Baseline | Accept feature inventory; enumerate routes, SDK/direct-model calls, canonical tool/effect paths, flags, voice and workers; qualify baseline errors; authenticate staging posture; establish scoped fixtures and measured latency/cost baseline | Approved enabled/disabled/retired/unresolved disposition; no missing entry point; named baseline failures and environments; controlled staging plan | Admin + GPT supervisor |
| H1 Authority | Framework-neutral policy seam with immutable IDs; middleware/service coverage; WF1/WF7 diagnostic path; positive/negative roles, fresh revocation, no-scope/cross-customer fixtures | T01–T18; legacy and direct/parallel paths retain or strengthen controls; no confidential/effectful shadow dispatch in promoted paths | Security/domain reviewer + supervisor |
| H2 Compatibility | Resolve full backend/container dependencies on actual production Python/architecture; regenerate normal hashed manifests; adapt agents/clients/messages/tools/middleware/orchestration/history/exceptions; update structural tests without weakening safety | Full retained-feature baseline and required install/import/build gates; Azure request/stream/schema probe in approved environment; T37–T42; one installed SDK set | Backend lead + supervisor |
| H3 Minimal harness | Restricted adapter with explicit app history ownership; default powerful tools off; WF8/general and WF9 first; AIMMS event translation and evidence buffer | Authorized success and crafted hidden-tool/cross-scope denial, citations and wire contracts; T11–T18, T29, T35–T40 | Admin + security reviewer |
| H4 State/approvals | Reuse/extend app records for run version, actor reference, scope reference, proposal, event sequence, deadline/cancel, checkpoint and execution fencing; bridge approvals; reconcile effects | T14–T28, T43–T45, T49; pause/revoke/restart/resume, double claims, changed payload/record and unknown outcome drills | Domain/security + operations |
| H5 Remaining workflows | WF2/WF3 analysis/research, normalized diagnostics/WF1, WF7 packet, WF6 documents and WF4 lifecycle; direct model calls covered | T30–T36 plus per-path authority/effect tests; full source feature inventory accounted for | Admin + backend/security |
| H6 Voice/workers | Existing provider and voice wire/confirmation behavior; compaction/history, ingestion and any actually enabled optional consumer; reconstruct actor on queued continuation | T25, T37–T38, T43–T47; authenticated UI and real voice/worker acceptance separately from mocks | Admin + operations |
| H7 Operations | Actual production-target image, shared cache/rate/token limits, multi-replica, failure/shutdown/provider outage, stop/cancel, telemetry, schema/retention and rollback rehearsal; quantitative comparison | T41–T50; accepted latency/cost/error/resource thresholds and no content leakage; image/queue/source identity verified | Operations + release owner |
| H8 Controlled promotion | Human-reviewed release evidence; selected cohorts/workflows on `aimms-dev`; exact runtime-owned runs and worker version routing; monitored rollback criteria | Full required positive/negative matrix, existing authenticated checker plus write/worker checks; no unresolved critical blocker | Admin/release owner |
| H9 Retirement | Drain/quarantine old-format runs, reconcile open/unknown actions, retain reviewed rollback evidence; remove only proven-obsolete adapters/dependencies/tests | No orphan run/schema/consumer; retention and rollback policy satisfied; no removed feature or security floor | Admin + backend/operations |

T01–T50 refer to the supplied research plan's acceptance matrix. Keep a per-test ledger with exact commit/image/config/runtime/database/fixtures/command/result, not a checkbox inferred from a demonstration. All required positive and negative tests must pass; pre-existing infrastructure failures remain named until resolved.

## 6. Work ownership and supervision

See `WORK_PACKAGES.md` for file allowlists and implementation sequence. Parent owns architectural decisions, this documentation, admin interaction, integration/review and acceptance. MiMo owns only the currently assigned code/test package. A child summary is not acceptance proof.

The initial MiMo package is stdlib-only static baseline inventory tooling, not an application migration. Its two files now exist and the supervisor executed the real CLI: nine workflow definitions, one alias and 351 scoped test-source paths were emitted with counts checked against the arrays. These are source findings, not executed application tests or proof of live authorization.

The first handoff is **not accepted**. Its 24 fixture tests and preview lint passed, but five additional supervisor regressions failed: intermediate-directory symlink escape, malformed dependency structure escaping sanitized errors, skipped scope falsely labelled complete, source overwrite outside the scan roots, and an external output hardlink overwriting a required source. Both owned files also failed the repository preview formatter. A separate bounded MiMo read-only review independently confirmed the input-scope, malformed-TOML and skipped-coverage defects.

The single corrective MiMo pass has returned. Parent re-read the actual code and changed tests, then independently verified **29 fixture tests**, **five previously failing supervisor regressions**, preview lint and preview formatting, all passing. Real-worktree output is byte-identical across repeated runs, relative-repository/different-CWD and stdout modes; an independent AST scan matches the emitted imports, call-site arrays and scoped test paths. Source counts and pins are unchanged from the first handoff.

**Limited disposition:** retain the verified code for static setup use, not as a complete H0 inventory or full work-package acceptance. The actual report explicitly marks `inventory_complete=false`: 12 dependency lines remain unparsed, four each in backend `requirements.in`, `requirements.txt` and `requirements-3.14.txt`; no unparsed text is echoed or interpreted. There are no skipped symlink paths in this checkout. `.run`/`.run_stream` remain lexical review candidates, and workflow dynamic values remain unresolved rather than guessed. Output inside the selected repository is refused, even for a new filename; use stdout or an external regular single-link report file. Atomic output/race hardening and exotic bind-mount aliases were not proved; this is trusted-local-checkout tooling, not a hostile-filesystem service.

**Bound exception:** the worker used about 28.71 minutes against the 20-minute cap (26 tool calls reported against a 32-call cap). The timebox contract was violated. No further correction/refinement worker was launched; remaining H0 work needs a fresh scoped/timeboxed decision. Preserve the failed first handoff separately from this narrower passing result. Final evidence: `probe-corrected-parent-verification.json`, `baseline-inventory.json`, `original-worktree-after-correction.json`, `isolated-environment-final-checks.json` and `supervision-routing-restored.json`. Earlier failed evidence remains in `probe-first-handoff-verification.json`, `baseline-inventory-first-handoff.json`, `probe-first-handoff-scope-audit.json`, `probe-independent-review.json` and `probe-correction-contract.json`.

Keep overlapping runtime/factory/guard/memory packages sequential; only parallelize non-overlapping owned paths with isolated test state. Require test-first RED/GREEN evidence, complete modified-file list, exact commands/exits, inspected dependencies and unresolved risks. No worker may commit/push/deploy, extract secrets, enable effects or decide product/security policy.

Recorded prior delegation settings: provider `openai-codex`, model `gpt-6-luna`, empty alternate delegation URL. Selected coding worker: provider `xiaomi`, model `mimo-v2.6-pro`. With no live subagents remaining, the supervisor restored and read back the prior provider/model pins. The current chat model was not changed. Later approved batches must explicitly repin and verify routing.

## 7. Verification commands and environment contracts

### Already executed

Exact driver commands are stored in the evidence JSON files. They use `docker --context default run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges`, the immutable carrier ID, task-volume scratch, read-only `/repo`, `DJANGO_SETTINGS_MODULE=ai.core.tests.settings`, `PYTHONPATH=/repo/src/backend/InvenTree`, disabled automatic pytest plugins and explicit pytest-asyncio.

The focused run used `python -m pytest -c /dev/null -p pytest_asyncio.plugin ... --junitxml=/scratch/baseline-focused.xml` over the seven named modules. The expanded run used the same lane and its own XML. JUnit counts include four successful pytest subtests in the expanded XML; do not add subtests to its 387 top-level passes or double-count reruns.

### Future commands, not executed in this setup

Run from the isolated checkout with task-owned configuration/database/cache/artifacts, never the ambient developer or shared Azure database. Read `tasks.py:1862` and the frontend runner configuration before using these commands:

```bash
# Full application suites; complete disposable DB/bootstrap required.
invoke dev.test --keepdb --runtest=aichat
invoke dev.test --keepdb --runtest=approvals
invoke dev.test --keepdb --runtest=repair
invoke dev.test --keepdb --runtest=assets
invoke dev.test --keepdb --runtest=voice
invoke dev.test --migrations

# Pure frontend unit modules, then type/build; from src/frontend.
npm run test:unit
npm run build

# Browser contracts: local isolated services and fixtures only.
# This configuration starts Vite, ASGI, OIDC mock and a worker.
npx playwright test tests/pages/pui_ai_chat.spec.ts --project=chromium

# Repository hooks on the owned, reviewed file allowlist.
pre-commit run --files <reviewed-owned-paths>
```

`<reviewed-owned-paths>` is an instruction to provide the actual allowlist, not a runnable literal. `.pre-commit-config.yaml` pins Ruff `0.15.12` with preview formatting/checking and uv `0.11.12` lock generation. Do not hand-edit generated hash locks or reformat unrelated files. Frontend Vitest is Node/pure-module-only; it is not React/browser/authenticated evidence. The Playwright config starts services even when a different base URL is supplied, so use a reviewed staging-specific acceptance harness rather than blindly pointing it at `aimms-dev`.

Require PostgreSQL parity for PostgreSQL-specific tests and real migration/queue recovery; SQLite unit success is insufficient. H2/H7 must build `contrib/container/Dockerfile`'s explicit production target, preserve static/translation/tokenizer/document/media dependencies and inspect the immutable built image. The exact build inputs/args and authenticated release invocation must be chosen from the existing release contracts when that phase is reached.

## 8. Release, rollback and definition of done

Assign backend/runtime/state version when a run begins. Keep turns, approval decisions and queue continuations on a compatible runtime. HTTP traffic splitting does not version-route workers. New-format checkpoints cannot be executed by an old image without tested compatibility.

Before promotion, record current image/config, schema and retention compatibility, web/worker ownership, pending proposals and active runs. On rollback: stop candidate admission/writes; capture/reconcile open/unknown effects; drain or quarantine incompatible runs; restore compatible images/config/worker routing; repeat authenticated read/write/worker checks. Never rerun a partly executed mutation through the legacy runtime to obtain a successful answer.

Immediate blockers: permission/scope bypass, cross-customer disclosure, duplicate irreversible effect, fabricated success, unsafe telemetry, corrupted history, unowned durable run, lost supported functionality or incompatible rollback state. Quantitative operational limits must be measured and accepted before H8; they are not invented here.

Done means the intended EQUA container owns orchestration; every accepted source feature/disabled posture is preserved; all dispatch paths enforce actual current-user policy; effects/approvals survive retry/revocation/restart/concurrency; one history/compaction owner remains; actual UI/voice/workers/images pass; aggregate controls and safe telemetry cover the run tree; retention/rollback are rehearsed; and the admin/human release record accepts the evidence.

## 9. Sources

- Supplied `EQUA_AIMMS_AI_HARNESS_OPTIONS_AND_MIGRATION_PLAN.md`, sections 9–19 and 22–23.
- `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `tasks.py`, `.pre-commit-config.yaml`, `pyproject.toml` at the frozen commit.
- Existing modules/line references in sections 4 and `FEATURE_INVENTORY.md`.
- `src/backend/InvenTree/ai/requirements-maf-migration.txt`, AI dependency inputs, `.github/workflows/ai_maf_matrix.yaml` (Python 3.12 and limited branch/path coverage, not complete production-image parity).
- `src/frontend/package.json`, `vitest.config.ts`, `playwright.config.ts`.
- Existing `contrib/container/Dockerfile` and `contrib/container/AIMMS-release-checks.md`.
- Actual isolated installed SDK signatures plus PyPI metadata in the durable evidence directory.
- Official MAF harness reference: https://learn.microsoft.com/en-us/agent-framework/concepts/harness (API claims additionally checked against the installed candidate, not copied into the beta environment).
- Sanitized Azure ARM/public HTTP captures in the durable evidence directory. These are point-in-time observations; refresh before any later live operation.
