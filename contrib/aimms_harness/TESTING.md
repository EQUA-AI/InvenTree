# Running the local AIMMS AI tests

This qualifies the **existing-SDK `ai/core/tests` island only**. It does not qualify the whole Django backend, frontend/browser behavior, PostgreSQL, production startup hooks, the modern-SDK migration or Azure staging.

## Reproduce the complete local run

From `/home/lokesh/Documents/mbpro/InvenTree-MAF-Harness`, with the already-prepared local image and task-owned volume:

```bash
python3 contrib/aimms_harness/run_tests.py \
  --repo /home/lokesh/Documents/mbpro/InvenTree-MAF-Harness \
  --image sha256:b6ce427887301a8d10058e59d55ee99f0e01f52ce4ef7e3e250687afa557fc07 \
  --volume aimms-maf-harness-setup-fe1e6a7f \
  --interpreter /scratch/baseline-venv/bin/python \
  --timeout 1200 \
  --output /home/lokesh/Documents/mbpro/aimms-maf-migration-evidence/testing-repair/your-new-run
```

`--output` must be a **new absolute directory outside this repository**, with an existing parent and no symlinked components. Existing output directories are refused. The command never pulls images or installs dependencies; a missing image/volume/interpreter is a prerequisite error, not an instruction to use the original checkout's environment.

- Add `--collect-only` to inspect collection/accounting without executing tests.
- Add repeatable `--test test_module.py` or `--test test_module.py::test_name` for a selected run. Selected and collection-only runs are never labelled complete execution.
- Full runs collect the entire declared island before execution. The runner returns a failed collection phase even if a later phase succeeds, and fails on missing reports, unexpected empty collection, source omissions or sandbox mismatches.
- Reports include exact container/exec commands, verified sandbox, configured interpreter and test UID, source accounting, node IDs, all phase exits, raw collection/execution output and JUnit. Keep node counts and JUnit subtest counts separate. The host Python version is not the container Python version.
- The reusable `/scratch` interpreter volume is read-only; mutable state lives on a separate `/runs` tmpfs. A root-only preparation phase copies the image's installed package bytes into an ephemeral `/deps` tmpfs because its private image path is unreadable to the test UID. No packages are installed, updated or written back to the image or reusable volume. Root-owned directories/files make this snapshot read-only to tests; the mount permits native extension mapping.
- The host launches collection and execution separately as UID/GID `65534:65534` through Docker exec. It records real phase exits and collection evidence before execution. Container-emitted collection/report control frames and writable outcome files are no longer authority. JUnit is the sole imported artifact: Docker cp, or a host-supplied read while the container is alive when the daemon cannot archive tmpfs. It remains test-produced evidence and cannot override a failed phase.
- Collection stdout and JUnit are **candidate-produced, unauthenticated observations**. Host storage protects captured bytes, not their origin. Full/selected execution structurally parses JUnit and requires exact collected-node/case identity consistency; collection-only does not establish execution. This is a local runner for reviewed test code, not a cryptographic guarantee against code coherently forging both observations. Parameter suffixes, including embedded `::`, are preserved literally; subtest rows are distinct from collected nodes.

The runner enforces no network, read-only container root/source, dropped capabilities, no-new-privileges, scrubbed inherited environment, disabled live integration/golden gates, and unique disposable SQLite/media/cache/config/home paths. It removes only its exact created container and its owned temporary data; copied evidence remains.

## Tooling and formatting

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s contrib/aimms_harness -p 'test_*.py' -v
ruff check --preview contrib/aimms_harness/run_tests.py contrib/aimms_harness/test_run_tests.py
ruff format --preview --check contrib/aimms_harness/run_tests.py contrib/aimms_harness/test_run_tests.py
```

These are tooling tests, not application or authenticated deployed acceptance. Docker transport doubles are labelled mocked; separate child-wrapper subprocess checks and real Docker executions provide their own evidence.

## Verified progress and explicit coverage boundaries

### Current result: local runner gates satisfied

The final parent-only correction has passed its **scoped independent review** and two fresh frozen-source full executions. Each records **3,548 passed / 16 skipped**, zero failed/error rows, the same 3,564 node IDs and skip reasons, exact node-to-JUnit identity matching, prepare/collect/execute exits zero, no source omission, and independent confirmation that the initially owned container is absent. Final checks also pass **102 tooling tests (73 runner tests)**, preview lint/format, 11 control scenarios, six accounting edges and a genuine real-pytest module skip. Source stayed unchanged throughout both runs. Evidence: `full-parent-literal-comparison-accepted.json`, `parent-literal-final-source-freeze.json`, and `parent-literal-review-acceptance.json` in the external evidence directory.

Review `deleg_0949c3b1` used 8 actual tools / 213.71 seconds within its 360-second limit; it executed the 73 runner unit tests and inspected the touched callers. It did not independently run Docker; parent real Docker controls remain separate evidence. This accepts only the documented existing-SDK offline local runner scope, with unauthenticated candidate observations and the reviewed-test-code assumption. The original dirty checkout remains unchanged. Nothing was staged, committed, pushed or deployed. Full Django/backend/frontend/browser/PostgreSQL, deployed startup, H0–H9 migration and Azure staging remain unqualified.

### Preserved rejected candidates

Earlier corrected-runner complete executions recorded **3,548 passed, 16 skipped, zero failed/error rows**, with identical 3,564 collected node IDs and skip reasons. Follow-up review nevertheless rejected remaining runner integrity and failure-handling gaps, so **acceptance was withheld for those candidate bytes**. See `full-reviewed-comparison-not-accepted.json`. Later acceptance does not retroactively accept rejected candidates or their worker timebox overruns.

The host-controlled rewrite passes **85 tooling tests**, preview lint/format and four real Docker boundary controls (unprivileged test UID, read-only dependency carrier, denial of keeper-stdout access, protected supervisor metadata). Both frozen full repeats record **3,548 passed / 16 skipped**, with prepare/collect/execute exits all zero, identical 3,564 node IDs and exact container removal verified. **Independent review rejected the rewrite**; none of those ordinary passes clears the rejection (`full-host-controlled-comparison-not-accepted.json`). Its implementation worker used 24 actual tool calls and 1,966.01 seconds, exceeding the 900-second timebox; that overrun is disclosed.

The rejected rewrite had five independently reproduced blockers: node-shaped candidate stdout could forge collection accounting; empty JUnit was accepted; zero execution rows were accepted despite collected nodes; cleanup did not pin the initially owned container ID; and a raced-in output directory could be overwritten by the failure-report path. The stdout finding also has a **real Docker reproduction**: one real test executed, but borrowing the same-UID child-wrapper stdout added a nonexistent node and the runner returned zero with two claimed nodes. See `host-controlled-review-rejection.json`, `host-controlled-followup-parent-reproductions.json` and `host-controlled-node-shaped-forgery-command.json`.

The explicitly approved focused correction passed **96 tooling tests**, preview lint/format and **11 control scenarios**: nine mocked-transport validation/identity/output controls, the real Docker node-shaped forgery (now rejected with exit 1), and the four-test real Docker isolation fixture. Both frozen full runs recorded 3,548 passed / 16 skipped, but review `deleg_99d474ac` rejected two independently reproduced accounting defects: unrelated skipped rows escaped reconciliation, and normal skipped cases were reclassified as collection rows in summaries. The review used 8 actual tools / 245.86 seconds and hit its iteration cap; it is not an unrestricted whole-runner audit. See `focused-correction-review-rejection.json` and `full-focused-correction-comparison-not-accepted.json`. The worker used 13 actual tools and 1,386.78 seconds, exceeding its 1,200-second timebox despite claiming otherwise.

The user then explicitly approved a **parent-only two-file correction**, without another implementation worker or rewrite. Both accounting bugs have behavioral RED→GREEN evidence. That candidate passed **99 tooling tests**, exact preview lint/format, the previous 11 control scenarios, six further accounting edge controls and a real pytest module-level skip. Its two frozen full repeats also recorded 3,548 passed / 16 skipped, but scoped review `deleg_349da845` rejected two malformed-diagnostic variants: absent classname was treated as explicitly empty, and a longer path containing the expected source path was accepted. The review used 8 actual tools / 347.58 seconds; both findings were inspection-only in the review and subsequently reproduced by the parent. See `parent-accounting-review-literal-rejection.json` and `full-parent-accounting-comparison-not-accepted.json`.

The final parent-only correction requires **present classname attributes, exact module identities and bounded literal source-path tokens**. Source accounting applies that identity/path rule too: text in a normal skipped case cannot hide an omitted source file. Three vertical RED→GREEN slices cover missing classname, ten malformed path variants and normal-skip attribution. Current checks pass **102 tooling tests (73 runner tests)**, exact preview lint/format, all previous 11 control scenarios and six accounting edges. A fresh real pytest module-level skip still succeeds, with one normal case and one separately disclosed collection skip, and its exact container is verified removed. No existing assertion was weakened; an old malformed collection-error mock now has pytest's explicit module identity. The fresh full repeats and scoped review are complete, as recorded above.

Collection accounts for **199 source files**: **198 collected files / 3,564 nodes**, plus `test_workflows.py`, a pre-existing manual `WorkflowTester` CLI with no pytest-collectable callables. It is not a newly excluded or skipped failing module. No source omission remains.

The 16 existing skipped nodes are disclosed individually in JUnit and `full-final-1-verified.json`:

| Boundary | Nodes | Explanation |
|---|---:|---|
| Azure identity/agent/HTTP/websocket host probes | 7 | Intentionally disabled in the credential-free offline lane. |
| Live golden deployment gate | 1 | Requires separately approved live deployment and authentication. |
| Real Redis atomicity | 1 | Requires an explicit isolated Redis URL. |
| Modern GA SDK memory providers | 2 | Not supported by the pinned existing-SDK lane. |
| Installed o200k tokenizer data | 1 | Encoder data cannot be downloaded offline; fallback tests still run. |
| Optional topology copy | 1 | The optional app is absent from the pinned source. |
| LocalDocs deployment script | 1 | The script is outside this checkout. |
| Tasks fence-copy parity | 2 | The pre-existing path loader gives the module no parent package, so its relative imports fail; these checks are **not** app-path parity evidence. |

No warnings were reported by the first final execution. The existing pytest configuration's deprecation filter is unchanged; this is not a claim that every installed dependency is warning-free. The test profile intentionally bypasses core/plugin production `ready()` effects, so local migration success is not startup, SSO, plugin or scheduler qualification.

The [repair plan](TESTING_REPAIR_PLAN.md) retains the baseline, approved scope and acceptance criteria. Evidence is external at `/home/lokesh/Documents/mbpro/aimms-maf-migration-evidence/testing-repair/`.
