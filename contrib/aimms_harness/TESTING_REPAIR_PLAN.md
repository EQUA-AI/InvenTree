# Local AI test-suite repair plan

Status: user authorized runner hardening. Both prior full runs pass (3,548 passed / 16 disclosed skips each), but acceptance remains withheld until the four reproduced runner defects are fixed and the final candidate verified. Current reproduction: [TESTING.md](TESTING.md).

## Baseline and scope

- Checkout: `/home/lokesh/Documents/mbpro/InvenTree-MAF-Harness`, branch `feat/aimms-maf-harness`, HEAD `05d47269bdd58febafea6f91542ad37b54ed7fdc`.
- Existing untracked `contrib/aimms_harness/` planning/static-probe artifacts are protected. The original dirty `/home/lokesh/Documents/mbpro/InvenTree` checkout must remain untouched.
- Qualify the existing-SDK **local `ai/core/tests` suite**, not the entire Django backend, frontend, modern-MAF migration or Azure staging deployment.
- Use synthetic actors/customers/records, disposable file-backed SQLite, test-owned media/cache/home directories, recording or mocked outbound effects, an offline container and read-only source mounts. No credentials, external integrations, shared databases, account/RBAC changes, deployment, staging, commits, pushes, PRs or issues.
- Preserve every historical migration, application authorization boundary, feature default and safety assertion. No `--fake`, migration disabling, invented migration dependencies, exclusion of failing modules or blanket warning suppression.

## Established evidence

Supervisor runs, before source changes, are retained under `../aimms-maf-migration-evidence/testing-repair/` (an external durable directory, relative to the checkout's parent).

| Observation | Evidence | Diagnosis / target |
|---|---|---|
| Two originally blocked modules produce 22 setup errors | `repro22.json`, `initial-suite-summary.json` | `aichat.0042_memory_consent` requires real `assets` migrations; registration must include their real transitive dependencies. |
| Complete discovery finds 198 source files; collection yields 3,550 test nodes from 197 files | `starting-state.json`, `collect-initial.json` | Account separately for collection-time skips; do not confuse source-file counts, pytest nodes and JUnit subtest rows. |
| Full pre-change run has 166 setup errors, 20 failures, 3,345 passed JUnit rows and 19 skipped rows | `full-initial.json`, `initial-suite-summary.json` | JUnit includes subtests. Fix setup first, then reproduce and classify every remaining failure. |
| `tasks` is a real sibling app under `src/backend/tasks`, not `src/backend/InvenTree/tasks` | source `tasks/apps.py` and migration dependency inventory | Both backend import roots are required; do not substitute root Invoke `tasks.py`. |
| Registering the entire production app list into minimal settings is not sufficient | `complete-bootstrap-probe.json`, `complete-registry-probe.json` | Real model imports also require account/LDAP/configuration contracts. Choose a coherent test-only configuration, rather than deleting migration edges or repeatedly guessing settings. |
| Remaining failures include stale citation/catalog fixtures, fake-client compatibility, incomplete voice policy metadata, flag-registry coverage and possible shared-state/fixture leaks | `initial-suite-summary.json` | Source-ground each decision; preserve provenance, deny/cross-customer/revocation checks and feature defaults. Do not assume every failed assertion is stale. |

## Execution packages

### Approved scope extension after the post-bootstrap run

The complete post-bootstrap run eliminated collection/setup errors but retained
19 ordinary failures. The user approved minimal local production fixes preserving
feature defaults and fail-closed permissions; no migration, RBAC, deployment or
integration changes are authorized. The user also chose to preserve existing
authorized citation revision hashes and explicitly test provenance and private-field
isolation, rather than remove the hashes needed by media revision tracking.

Remaining work includes missing disabled flag metadata, explicitly screen-only
memory voice policies, conservative proposal-intent routing, a bounded status label,
generated contract synchronization, deterministic fixtures and truthful runner
accounting. Record each existing/new regression's RED/GREEN evidence. The prior
bootstrap/runner worker timebox overruns remain
process failures; they are not erased by technical passes. The AI test island's
startup-hook suppression is not production startup-lifecycle coverage.

### 1. Complete disposable Django bootstrap (MiMo implementation, supervisor acceptance)

Owned files initially: `src/backend/InvenTree/ai/core/tests/settings.py` and proposed `src/backend/InvenTree/ai/core/tests/test_test_bootstrap.py`. Additional files require supervisor approval.

1. Add a regression that verifies the real Django migration graph is consistent and the memory-consent tables can be migrated on a fresh disposable SQLite database. Watch the existing bootstrap fail first.
2. Implement a coherent test-only app/settings configuration with the actual source dependency closure and import roots. Do not import live configuration, read secret/environment files or enable integrations. Preserve `TestUser`, signed-cookie/async authentication semantics and all database migration histories.
3. Use safe test email/cache/storage configuration and a Django-Q retry greater than timeout; no live worker.
4. Re-run the two original failing modules and the graph regression; retain exact RED/GREEN commands, exits and results. Parent review is required before acceptance.

Worker limit: 15 minutes and 24 tool calls; stop with explicit partial findings at the limit. No unapproved refinement loop.

### 2. Reproducible local runner

Proposed files: `contrib/aimms_harness/run_tests.py`, `contrib/aimms_harness/test_run_tests.py`; supervisor-owned README/plan updates.

- Pin the supplied existing-SDK interpreter/image, not a floating tag. Keep candidate-SDK qualification separate.
- Explicitly select pytest configuration/plugins/import roots and asyncio mode, clear inherited credentials/configuration, pin live-test gates off, and run with `--network none`, read-only root/source, dropped capabilities and no-new-privileges.
- Create unique test-owned run directories. Refuse unsafe output locations and do not overwrite existing runs. Never mount the user's original source tree or mutate shared resources.
- Mount the reusable interpreter/dependency volume read-only. Keep mutable state on a separate, container-owned tmpfs; emit only allowlisted reports before the tmpfs disappears on container exit.
- Capture collection nodes/log/exit in the supervisor stdout stream before execution. Fail if post-execution collection files differ; do not let writable candidate reports redefine collection accounting.
- Record source discovery, collected node IDs, collection skips/errors, executed outcomes, JUnit, warnings, runtime identity, exact command and exit. Treat incomplete/missing reports as failure; preserve failed/killed attempts separately.
- Test selection/accounting, environment isolation, subprocess failure propagation and filesystem safety using negative fixtures before implementation.

### 3. Resolve all remaining local-suite failures

Sequential ownership where files overlap. For each failure:

1. Reproduce narrowly under the same pinned baseline environment.
2. Trace definition, usages, fixtures and accepted preservation contract.
3. Distinguish bootstrap/fixture/order defects from stale expected contracts and real product bugs.
4. Make the smallest approved change with a red-capable test. Do not relax privacy/authorization/write-fence assertions or add skips to hide failures.
5. Escalate production/security-policy or schema changes outside this test-infrastructure scope for a separate decision. Test-only expectation updates require explicit source justification; they cannot grant an action or enable a feature.

### 4. Verify and independently review

- Re-run the original 22-error selection and prior focused/security selections.
- Run complete local AI collection **and execution** from a fresh disposable environment, then repeat to expose order/state leaks. Reconcile discovery, skips, collected nodes and JUnit subtest accounting programmatically.
- Run existing static-probe fixtures and supervisor safety regressions to ensure protected tooling remains intact.
- Run the repository's exact preview Ruff lint/format checks on owned Python files; inspect all changed paths and migration hashes.
- Independent read-only reviewer examines the actual final diff and negative-control evidence. Parent verifies the returned findings and decides acceptance.
- Verify original-checkout preservation against the pre-existing durable baseline; report anything that cannot be proven.

## Definition of done

A working, documented command has actually executed the entire declared local AI scope with zero collection/setup errors and zero failed tests; all skips/warnings are explained without hidden omissions. Disposable DB/storage, mocked effects, unchanged migrations/feature defaults/authorization and original-worktree protection are verified. The final report gives actual pytest/JUnit totals, commands and remaining separately gated lanes. A clean local AI pass does not establish PostgreSQL, frontend/browser, Azure staging or modern-SDK parity.

## Supervisor verification progress

- Bootstrap: 25 original-selection/graph tests pass with real migrations; no historical migration was edited or disabled.
- Runner: 40 regressions pass, including real shim checks for installed dependency paths, configured-root node IDs and collection-failure propagation. Original complete collection accounts for 199 source files, 198 collected files and 3,564 nodes; `test_workflows.py` is a manual CLI with no pytest callables, not a collection skip.
- First final full execution: collection/execute exits 0; 3,548 passed, 16 skipped, no failure/error rows or reported warnings (`full-final-1/`, `full-final-1-verified.json`). Repeat and independent-review acceptance remain open.
- Exact preview Ruff 0.15.12 lint/format passes on 22 owned Python files; 66 tooling tests and five independent static-probe safety regressions pass.
- All 139 saved dirty-original file hashes, original branch/HEAD and exact NUL-delimited index/status hashes match the preservation baseline (`original-preservation-final-exact.json`).
- The original runtime tool ceiling remains 17. The stale benchmark cap of 12 was corrected against that existing contract and the exact reviewed 13-tool stock-ranking selection; latency, reduction, quality and live-comparison gates were not removed. Tool-count updates explicitly identify the already-existing `propose_memory_action` entry.
- The 16 skips are itemized in [TESTING.md](TESTING.md). In particular, two pre-existing tasks fence-copy path-loader skips remain a declared app-path parity gap, not proof of working app imports. Production startup and separately gated integration coverage remain open.

### Review corrections and acceptance reset

`deleg_443b56da` returned `passed: false`: the reusable baseline volume was writable and execution could forge collection files. The audit used eight actual tool calls (nine model API calls), within its eight-minute/eight-tool-call budget. Its production-policy/default/provenance review found no additional blocking changes; it did not validate runtime results.

The supervisor made narrowly scoped runner corrections. `/scratch` is read-only; all mutable state is now on `/runs` tmpfs. The host verifies collection files against a first stdout frame emitted before execution, and transfers allowlisted reports before container shutdown. The first tmpfs smoke exposed lost reports on stop; its failed evidence is retained in `runner-readonly-smoke-first/`, and the corrected stream transport passes an 11-test real smoke (`runner-readonly-smoke-second/`).

The real Docker adversarial fixture (`runner-real-collection-tamper-02/`) verifies read-only baseline writes fail with `EROFS`, then forges node IDs/logs and emits an imitation collection frame. Pytest passes both synthetic tests, but the runner correctly exits 1, retains the original two-node collection and reports tampering. The earlier fixture's bind permission failure is retained separately, not labelled as this negative control's success.

All 69 tooling tests and both runner preview lint/format checks pass. `reviewed-tested-source-manifest.json` differs from the previous freeze only in the two runner files; application/test candidate bytes are unchanged. `full-reviewed-1/` and `full-reviewed-2/` are fresh acceptance runs. The earlier complete runs are historical evidence, not accepted replacements for corrected-runner verification.

### Follow-up verdict and remaining blockers

Both corrected full runs completed with exit 0, identical 3,564 node IDs and skip reasons, 3,548 passed / 16 skipped, no failures/errors. Both owned run containers are verified removed. The 3,838 frozen source hashes match; all 139 saved original-checkout files plus its branch/HEAD/index/status remain unchanged.

`deleg_0f2c0872` returned `passed: false`. It used six actual tool calls (seven model API calls) but exceeded its six-minute timebox (374.9 seconds). No further implementation/refinement worker has been launched. Remaining findings were independently reproduced, not accepted merely from the review summary:

1. **Direct collection-frame forgery:** a synthetic collection import writes `/proc/1/fd/1` before the shim's frame; execution then replaces collection files to match. The real Docker runner incorrectly exits 0 and claims one collected node although pytest executes two (`runner-real-direct-stdout-tamper/`, companion command JSON). Ordinary captured-output imitation did not cover this path. Treat collection/runner control as a separate trust boundary; do not present current checks as adversarial proof.
2. **Exit mismatch:** a fake transport returns container exit 7 while the reports claim 0; the host returns 0. The actual container result must remain independently authoritative (`followup-review-logic-reproduction.json`).
3. **Report-source disagreement:** successful `docker cp` files are accepted even when the validated report stream disagrees. Use one authoritative allowlisted source or reject mismatches; mutable report files are not protected from test descendants.
4. **Malformed creation ID cleanup:** a successful create with an invalid returned identifier fails validation but leaves no tracked cleanup target. Preserve literal validation; resolve ownership through an explicitly assigned unique container name/label, never remove arbitrary output tokens.

Full-run evidence is retained as **not accepted** in `full-reviewed-comparison-not-accepted.json`. No production, dependency, migration, account/RBAC, external integration or deployment change was made while reproducing these findings. Additional runner-only hardening must have an explicit boundary, negative controls, fresh full verification and acceptance review; the technical application pass is not the final testing qualification.

### Authorized final runner-only package

The user instructed: “edit runner so that it is not blocked”. Scope is the two runner Python files and supervisor documentation; the application/test candidate and original checkout remain protected. The implementation package is bounded to 15 minutes / 24 actual tool calls; no automatic refinement after a cap or failed acceptance.

Chosen boundary: host-controlled collection/execution phases through Docker exec, with pytest running as UID/GID 65534 and a separate root-owned container keeper. The host—not writable container reports or a candidate-accessible stdout frame—owns phase exits, collection nodes/logs and outcome metadata. Only JUnit is transferred from the test filesystem. Use an explicit unique container name/run label to recover exact ownership when creation output is malformed.

Preflight evidence: the pinned image's private `/root/.local` dependencies are not readable to UID 65534, so switching users alone loses Django. An ephemeral, root-owned, read-only-to-test-user copy of those exact installed package bytes resolves imports without installs or shared-environment changes. `final-hardening-host-control-preflight.json` verifies Django/pytest/agent-framework/asgiref imports under UID 65534 and denial of access to the keeper stdout descriptor. Container-owned tmpfs carries the copy and all mutable test state; no credential/config/home tree is copied. Proc-overlay alternatives are unsupported by the OCI runtime and their failed probes are retained, not used as the solution.

Acceptance: regression-first checks for the actual exit mismatch, forged direct descriptor writes, report-source disagreement and malformed-ID cleanup; all prior meaningful runner contracts retained; real focused execution and adversarial controls; exact preview lint/format; fresh frozen full repeat; independent acceptance review. No feature/RBAC, migration, dependency pin, production startup, external integration, commit or deployment changes.

### Final package outcome: rejected, no automatic refinement

The two-file rewrite preserves the protected application candidate and original checkout. Parent checks reproduce 85 passing tooling tests, preview lint/format, four real Docker isolation controls and a first frozen full run with 3,548 passed / 16 skipped. The implementation worker used 24 actual tools and 1,966.01 seconds (900-second limit exceeded); its own shorter duration/call estimate is not the controlling evidence.

Independent review `deleg_738f6e92` returns `passed=false` within its 8-tool/480-second boundary (8 actual tools, 406.18 seconds). Parent independently reproduces all five findings: candidate-origin node-shaped collection stdout; empty JUnit acceptance; zero execution despite collected nodes; changed cleanup ID under matching name/label; and raced-in output-directory overwrite. The collection finding has a real Docker proof: same-UID wrapper stdout remains accessible even though the root keeper descriptor is protected. One executed node is falsely reported as two. Exact evidence is in `host-controlled-review-rejection.json`, `host-controlled-followup-parent-reproductions.json` and `host-controlled-node-shaped-forgery-command.json`.

The review's host pytest command failed because host pytest is not installed; its stdlib harness tests did run. This is not an application failure or permission to install packages. No further implementation worker or code refinement has been launched. Runner acceptance stays withheld, and the remaining scope must address report validation/execution reconciliation, pinned container identity, atomic output ownership and the collection-evidence trust contract.

### Explicitly approved focused correction

After the rejection the user selected **“Approve one focused runner-only correction package”**. The approved two-Python-file implementation is limited to 20 minutes / 18 actual tools, with no broad rewrite or automatic refinement. Both prior full repeats completed cleanly but remain unaccepted. Parent node/JUnit preflight establishes exact identity-counter equality for all 3,564 cases and catches the naive delimiter bug on an IPv6 parameter containing `::`; parameter values must be preserved literally.

Corrective gates: behavioral RED→GREEN for the five proofs; structural JUnit validation; exact normal-node identity reconciliation with separately accounted subtests/skips/errors; honest candidate-produced observation provenance (host capture is not authentication); pin initial owned ID for all subsequent operations/deletion; atomic output ownership before any failure-report writes. Preserve fail-closed source omissions, raw evidence and real phase exits. This local runner assumes reviewed test code and cannot be a cryptographic guarantee against arbitrary test code monkeypatching pytest and coherently forging both observations. No application/RBAC, migration, installed dependency, default feature, account, integration or deployment change is authorized.

### Focused correction handoff: verification pending

`deleg_5a2c3307` returned the two-file correction. Actual transcript accounting establishes 13 tools; notification duration is 1,386.78 seconds, exceeding the 1,200-second boundary. Its claimed ten-minute/within-budget duration is contradicted by the record. Scope is preserved, but the timebox is not retroactively accepted and no automatic refinement follows.

Parent independently verifies 96 tooling tests, exact preview lint/format, added-line static scan (no findings), original dirty-checkout HEAD/branch/index/status and 139 saved file hashes, and a source delta limited to the two runner Python files before these documentation updates. Prior candidate bytes were reconstructed from preserved snapshots/diffs and hash-matched before producing the focused review diffs. Nine independent mocked-transport controls reject empty/malformed/wrong-root/zero/missing/wrong-identity JUnit, initial/final ID swaps and raced output; the real same-UID wrapper stdout attack now exits 1 for its missing executed identity, and all four real isolation tests still pass. Both real control containers are independently verified removed. Evidence: `focused-correction-controls-parent-verification.json`, `focused-correction-tooling-parent-verification.json`, `focused-correction-lint-parent-verification.json`, `focused-correction-original-preservation.json` and `focused-correction-worker-cap-record.json`.

Both focused-correction full repeats completed before subsequent parent source edits: 3,548 passed / 16 skipped, exact cleanup, exit 0. They remain unaccepted. Review `deleg_99d474ac` rejected extra unmatched skipped rows and inconsistent summary counts; parent reproduces both. Review used 8 actual tools / 245.86 seconds and hit the iteration limit, so its coverage is scoped and incomplete. Evidence: `focused-correction-review-rejection.json`, `focused-correction-review-parent-reproductions.json` and `full-focused-correction-comparison-not-accepted.json`.

### Explicitly approved parent-only accounting correction

The user selected **“Approve parent-only two-file correction”**. No new implementation worker was launched. Parent first reproduced real pytest module-level skip identity/body in a disposable offline fixture, then completed two vertical behavioral RED→GREEN slices: reject unvalidated collection diagnostics while preserving real source-attributed module skips; and retain the exact node-aware parsed counts for summaries. Allowlisting requires empty classname, exact in-scope module identity, cited container source path, no collected cases for that module and no duplicate diagnostic identity. These are consistency rules for reviewed test code, not authentication of candidate observations.

That candidate passed 99 tooling tests, preview lint/format, the prior 11 independent control scenarios, six added accounting edge controls and the real genuine-module-skip fixture. Its two frozen full repeats completed with 3,548 passed / 16 skipped, unchanged source, identical nodes/skips, phase exits zero and exact container removal verified. Scoped review `deleg_349da845` nevertheless rejected absent-classname coercion and source-path substring attribution. It used 8 actual tools / 347.58 seconds; its two findings were inspection-only, then independently executed by the parent. Both technical fulls remain unaccepted (`full-parent-accounting-comparison-not-accepted.json`).

### Parent-only literal diagnostic validation: current candidate

This remains the approved two-file source-attribution correction, without an implementation worker or rewrite. After the prior full driver finished, three vertical RED→GREEN slices required explicit classname presence, exact bounded source-path tokens, and the same module identity/path rule in source accounting. The latter closes the sibling call path where a normal skipped case's text could conceal an uncollected source. One old malformed error mock was changed to real pytest's explicit module identity; its failure and source-accounting assertions remain intact.

Current verification passes 102 tooling tests (73 runner tests), exact preview lint/format, the prior 11 control scenarios and six accounting edges. Real Docker again rejects node-shaped stdout forgery, preserves all four isolation controls, and accepts a genuine module-level skip as a separate collection diagnostic; exact owned containers are verified absent. Evidence includes `parent-literal-{classname,source-token,source-accounting}-red.json`, `parent-literal-tooling-verification.json`, `parent-literal-controls-parent-verification.json`, `parent-literal-final-matrix.json`, `parent-literal-real-module-skip/`, the two parent-literal review diffs and `parent-literal-static-scan.json`.

No application/default/RBAC, migration, installed dependency, integration or live-system change was made in this final correction. Source changes remain confined to the two Python files plus this supervisor documentation. Candidate observations remain unauthenticated, under the reviewed-test-code assumption. No commit, push or deployment is authorized.

### Final local acceptance

Independent scoped review `deleg_0949c3b1` passed: 8 actual tools / 213.71 seconds within its 360-second limit, no truncation, 73 runner unit tests executed and touched parsing/reconciliation/accounting/summary/ownership paths inspected. The reviewer did not independently execute Docker; parent real controls are explicitly separate evidence (`parent-literal-review-acceptance.json`).

Two fresh full repeats on the frozen final source completed with **3,548 passed / 16 skipped** each, exactly matching all 3,564 collected identities, identical nodes/skip reasons, prepare/collect/execute exits zero, no source omission and exact initially owned containers verified absent. The complete 3,840-file freeze remained unchanged during both executions. `full-parent-literal-comparison-accepted.json` joins that evidence with the 102 tooling tests, exact lint/format, 11 control scenarios and six accounting edges. The earlier failed/rejected candidates and worker timebox overruns remain archived, not retroactively accepted.

The local existing-SDK `ai/core/tests` runner gates are satisfied. This does not close full H0, the Microsoft Agent Framework migration, deployed startup, authenticated application parity, full Django/backend/frontend/browser/PostgreSQL or Azure staging. The original dirty checkout's HEAD, branch, exact NUL Git index/status and 139 saved nonsensitive file hashes remain preserved. Only closing documentation is updated after the tested-source freeze; tested Python and application bytes are unchanged. No staging, commit, push, deployment or live write was performed.

## Sources

`CONTRIBUTING.md`; root and AI `pyproject.toml`; `ai/core/tests/settings.py`; `ai/core/tests/conftest.py`; `aichat/migrations/0042_memory_consent.py`; `assets/migrations/0015_alter_machineanomaly_work_order.py`; `src/backend/tasks/apps.py`; `InvenTree/settings.py`; external `testing-repair/starting-state.json` and `initial-suite-summary.json`; the accepted `FEATURE_INVENTORY.md` and existing H0–H9 plan.
