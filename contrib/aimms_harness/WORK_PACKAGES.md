# AIMMS harness migration: supervised work packages

**Status:** Work breakdown, not a completed implementation. Source/branch/staging identities and evidence limits are in `EXECUTION_PLAN.md`. Product/security acceptance belongs to the admin and GPT supervisor; MiMo implements only the assigned code/test package.

## Common worker contract

- Work only in `/home/lokesh/Documents/mbpro/InvenTree-MAF-Harness`, branch `feat/aimms-maf-harness`, frozen source base `05d47269bdd58febafea6f91542ad37b54ed7fdc`.
- Inspect `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, relevant files, definitions, usages and test-runner settings before changes.
- Preserve the original checkout and all excluded WIP. Do not copy its `.env`, configuration, database or uncommitted application files.
- New files through `write_file`; existing code through V4A `patch`. Touch only the package's allowlist. Request scope expansion rather than editing a neighbor silently.
- Strict vertical TDD: one observable behavior, fail for missing behavior, minimal implementation, pass, then relevant regression checks. Retain RED and GREEN commands/results. Never weaken authorization assertions to get a green result.
- No commit/stage/push, PR/issues, merge, Azure changes, package installs in the original environment, credential extraction or Hermes config edits. An approved dependency package may install only into its explicitly assigned disposable environment.
- No live model/database/mailbox/customer effects unless the supervisor provides the separately approved target and fixture/effect contract. Network-disabled unit probes are not live-provider acceptance.
- Return: exact modified files; decisions within assigned scope; commands/exits/counts/warnings/skips; per-test TDD evidence; unresolved risks. Provide verifiable artifact paths. The supervisor re-reads actual changes and independently exercises them.

Only parallelize packages with disjoint file ownership and disposable test state. Factory/client/message/guard/history adaptation overlaps; it is sequential. The user must manually review before publication or a PR; no worker closes release/production acceptance.

## WP-00: baseline inventory CLI — initial H0 MiMo package

**Phase:** H0 setup. **Current state:** corrected code independently verified and retained as a **partial static setup result**, not full work-package/H0 acceptance. Parent confirmed 29 fixture tests, five previously failing supervisor regressions, real-repository deterministic output/independent AST counts and preview lint/format, all passing. The initial failure evidence remains retained. No application/runtime/Azure mutation was authorized or performed.

**Owned new files only:**

- `contrib/aimms_harness/baseline_probe.py`
- `contrib/aimms_harness/test_baseline_probe.py`

The parent owns all Markdown documents and evidence aggregation. Do not modify application runtime, requirements, flags, migrations, CI, container definitions or Azure resources in this package.

**Purpose:** repeatable stdlib-only static source/Git inventory that works without application initialization, credentials, Django setup, provider calls or dependency installation. Installed-package/signature/runtime discovery is separate isolated evidence, not a capability claimed by this CLI. Its output is inventory evidence, not an assertion that tools are authorized/live or that migration is safe.

**Acceptance:**

1. Exercise the real CLI against a temporary controlled repository and the approved checkout; JSON parses and reports actual source/Git identity.
2. Enumerate workflow definitions/aliases and the selected construction/model/dispatch review candidates from source rather than an invented result list. The complete dispatch census remains WP-01; lexical/AST matches cannot establish supported live execution.
3. Enumerate source locations/SDK import or construction sites without leaking raw prompts, credentials, environment values or unrelated user data. Do not scan `.env`/credential/database/secret files.
4. Explicitly label this report static/source-only. Distinguish its findings from the separate installed-package/symbol probes; neither probe may trigger application runtime/provider startup.
5. Use deterministic ordering and deduplicated counts. Syntax/missing-path/provider-package conditions yield explicit bounded failures or unresolved results, never fabricated successful entries.
6. Keep input/read scope inside the selected repository; test path/symlink escape protections where the utility follows files.
7. Stdlib tests require no install. Parent runs the exact entrypoint/test suite and relevant repository lint/format checks after inspecting the returned interface.
8. No claim of full canonical invocation/authorization coverage from lexical/AST hits. The dispatch map must be reviewed in H1.

The corrective pass owned only the two files above and addressed ancestor symlinks/containment, sanitized dependency-shape failures, disclosed skipped sources and unparsed dependency completeness, output collisions including hardlinks, and repository preview formatting. The five parent reproductions now pass; the parent also inspected the added regressions and corrected symlink-disclosure expectation rather than accepting the worker summary alone.

The pass **exceeded its 20-minute cap**, taking about 28.71 minutes; 26 tool calls were reported against the 32-call limit. No additional refinement pass was launched. The real report remains partial with 12 unparsed dependency lines; source dispatch/authorization/live-feature coverage is not closed. Further H0 work requires a fresh scoped/timeboxed decision rather than silently extending this package. The output policy deliberately refuses all paths inside the selected repository, including new filenames. Do not extend this utility into a general migration tool, adversarial-filesystem service or deployment helper.

## WP-01: H0 baseline qualification and dispatch map

**Prerequisite:** WP-00 reviewed; admin preservation inventory accepted; current live enablement/effect scope established before any authenticated effects.

**Proposed scope, assign concrete files after review:** test-bootstrap fixtures/settings plus new harness/dispatch contract tests. Existing source candidates are `ai/core/tests/settings.py`, `test_multiturn_isolation.py`, `test_pilot_latch.py`, `test_ai_boundary_auth.py`, `test_universal_enforcement.py` and `test_capability_invocation_guard.py`, all under `src/backend/InvenTree/`.

**Tasks:**

- Retain the failed 22-error baseline and reproduce the minimal-settings migration failure.
- Trace complete required apps/migration dependencies and choose a disposable complete bootstrap. The referenced assets migration exists; do not rewrite historical migrations or use fake migration state.
- Re-run the same error labels with the corrected bootstrap, same pinned SDK/runtime and separate test state. Distinguish baseline configuration correction from application migration.
- Extend source inventory to every request/stream/voice/diagnostic/parallel/nested/worker/direct SDK dispatch path. Map canonical ID, effect, actor source, modality, workflow, selection, execution authorizer, scope check, approval owner, retry/idempotency and evidence/event owner.
- Classify internal automation separately from user-delegated actions; never invent a human/service fallback actor.

**Gate:** no unexplained missing path; positive and denial tests for every effect/confidential-reader class; live disabled/retired features not activated. JUnit warning/config differences recorded separately.

## WP-02: H1 framework-neutral authority boundary

**Prerequisite:** accepted dispatch map; baseline qualified.

**Likely existing owned seams:** `ai/core/tools/invocation_guard.py`, `capabilities.py`, `rbac.py`, `workflows/rbac_run.py`, plus focused tests. A proposed new `ai/core/harness/` package is optional and must not duplicate policy.

**Tasks:** preserve canonical callable identity through wrappers; bind immutable canonical IDs in trusted code; freshly reconstruct actor/grants/scope immediately before operations; unknown IDs/context/authorizers denied. Cover WF1/WF7's diagnostic registry instead of pretending they are ordinary catalog rails. Inspect the actual empty `LEGACY_DIAGNOSTIC_TOOLS`, not just the retained constructor exception.

**Gate:** T01–T18 on original runtime; hidden/direct crafted invocation and revocation deny, allowed users still work, two scopes/parallel contexts remain isolated. No new customer-facing shadow-only path. No provider-version change in this package.

## WP-03: H2 dependency and API compatibility

**Prerequisite:** authority contracts green; single selected candidate set and complete application resolution approved.

**Assign sequentially:**

1. Disposable full dependency resolution/image probe; do not edit source until constraints and transitive compatibility are understood.
2. Provider/client and message/session compatibility, factory construction, middleware call shapes, orchestration/events and exceptions.
3. Memory bridge conversion and single-owner replay/compaction proof.
4. Dependency inputs and generated lockfiles through normal repository uv/pre-commit tooling; complete retained workflows compatible with the one installed set.

**Likely existing files:** AI and backend dependency inputs/locks; `ai/core/agents/factory.py`; `ai/core/integrations/azure_openai_client.py`; `ai/core/maf_compat.py`; `ai/core/memory/maf_adapter/`; applicable workflow/event tests; `.github/workflows/ai_maf_matrix.yaml` once the exact required check design is reviewed.

**Required discoveries to address:** old `ChatAgent(chat_client=...)` → actual modern `Agent(client=...)`; old Azure-specific client export vs inspected `OpenAIChatClient`; `ChatMessage`/`TextContent` → `Message`/`Content`; new default-history provider ownership; client function-invocation configuration and per-request options; current middleware runtime guarantees. The approved source's memory `_ga.py` already uses some modern types, but `_replay.py` still imports beta-only ones.

**Gate:** positive/negative contracts, full backend/worker/voice tests, exact production target build/import/package checks, approved Azure endpoint/schema/stream/request probe, no new hosted orchestration/data destinations. Preserve model family/deployment/auth policy. Old/new SDKs never share a namespace in one environment.

## WP-04: H3 restricted read-only harness

**Prerequisite:** all retained adapters work on one modern SDK set; complete hard-enforcement catalog; explicit app history bridge.

**Proposed owned new files:** small harness adapter/contracts and corresponding tests under a reviewed `ai/core/harness/` layout. Assign precise paths after approving interface responsibility, not based on the research document's illustrative names.

**Tasks:** enable only WF8/general then WF9 with existing authorized capabilities; disable new shell/files/code/auto-memory/web/delegation/standing approvals; preserve AIMMS event ordering and validate-before-display. Show exactly-once history append/replay and scoped retrieval/citations.

**Gate:** authorized representative cases and negative direct/cross-scope calls; no default powerful tools; no hidden second history loader/writer; current UI wire consumes outputs unchanged. Staging enabling waits for the operation/fixture approval.

## WP-05: H4 run state and approvals

**Prerequisite:** read-only parity, approved persistence/retention design and compatible schema upgrade/rollback contract.

**Existing owners:** `aichat/services/proposals.py`, `aichat/models.py`, approval app, thread/history repositories and worker services. New fields/models/migrations are proposed only if existing records cannot meet the contract; inspect definitions/consumers first.

**Tasks:** version-owned runs/checkpoints; authorized start/load/continue/cancel; current actor/scope/stop reconstruction; approval bound to exact normalized payload and record/proposal version; one execution owner/lease/fencing; stable business idempotency/reconciliation and explicit outcome-unknown.

**Gate:** T14–T28 and T43–T45/T49. Duplicate approvals/claims, permission revocation, restart, expired/cancelled proposals, changed payload/record, and crash-after-effect are tested in disposable controlled services. SDK interrupts alone are insufficient business approval.

## WP-06: H5 workflow and direct-caller parity

**Prerequisite:** state/approval/effect contracts qualified.

**Assign separately:** WF2/WF3, normalized diagnostics/WF1 and WF7, WF6 ingestion/review and WF4 procurement. Also account for direct model callers in analysis synthesis/intent, grounding, image captioning, memory extraction, probes/evaluations and `aichat/tasks.py` compaction.

**Gate:** T29–T36 plus the same authority/effect matrix. Existing deterministic business services remain deterministic; alias/retired workflow contracts preserved. An import-only migration does not satisfy business parity.

## WP-07: H6 voice, history and workers

**Prerequisite:** existing actual feature/provider posture confirmed, source workflow parity and durable ownership green.

**Owned packages:** voice protocol/confirmed-write adapter and tests; worker continuation/ingestion/compaction/memory consumers in non-overlapping sequential packages. Existing candidate roots are `ai/core/voice/`, `aichat/services/voice_bridge.py`, `aichat/services/memory_worker.py`, `aichat/tasks.py` and actual queue/job consumers discovered in WP-01.

**Gate:** unit guards plus authenticated browser, real voice transport/device and live controlled-worker acceptance as separate evidence. Ordinary voice lookup remains read-only; existing enabled confirmed-write capability retains exact confirmation/RBAC/proposal policy. Optional workers/features stay dark if not approved/live.

## WP-08: H7 artifact and operational qualification

**Prerequisite:** full feature matrix green, exact build commit/schema/config frozen and authenticated acceptance path usable.

**Tasks:** build production target; inspect immutable image source/package/static/translation identity; disposable PostgreSQL fresh/upgraded/rollback cases; web/worker image alignment and versioned queue claims; multi-replica restart/shutdown/cache outage/cancel/stop; aggregate budgets; content-safe traces; measured latency/cost/resources/backlog; rollback rehearsal.

**Gate:** T41–T50; clean exact-build evidence and admin/operations-accepted thresholds. No mystery skipped suite. Local carrier tests, ARM health and mocked voice do not close deployed-image/authenticated/device gates.

## WP-09: H8 staging promotion / H9 retirement

**Prerequisite:** manual release review, exact `aimms-dev` change authorization, tested recovery/backup, one migration owner and controlled fixture/effect scope. No implied production deployment.

**Tasks:** pin image and per-run runtime, deploy web/worker coherently, verify exact revisions/traffic/config after every external change, run existing authenticated checker plus controlled effects/worker tests, canary by owned run, monitor rollback triggers. Retire old adapters only after active old-format work and reviewed rollback period allow it.

**Gate:** human-reviewed release record and verified read-back of every changed external target; no orphan/unknown action or incompatible checkpoint, lost feature, widened authority or unapproved data destination. Do not open a PR until required human review; do not generate issues.

## Acceptance handoff format

Every package returns:

- Frozen base and candidate source identity, file allowlist and actual diff.
- For each requested behavior: test that failed, precise reason, implementation and passing rerun.
- Runtime/image/package set, database backend, fixtures, command/exits, counts/skips/warnings and retained log/artifact paths.
- Authority/effect/state/history/event invariant touched and sibling call paths inspected.
- Known failures and non-executed gates, kept distinct from later narrower passes.
- Supervisor-reviewed decision and admin/operations approvals still required.

A completed work package is not a completed phase or migration. The parent applies acceptance only after independently inspecting files and running the relevant tests.
