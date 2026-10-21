# P51-calibration — O20 native calibration traceability

Status: **partial — O20 review fixes implemented and targeted regressions pass; full users suite has one pre-existing failure covering 52 other models**. No deployment or R6 acceptance is claimed. This report supersedes only P51's missing reference-instrument/certificate provider disposition, within O20. O21 linked corrective Work belongs to the separate run.

Built in `/Users/Aniketkumar/IdeaProjects/InvenTree-r6/p51-calibration`, base `873a104e4`, branch `r6/p51-calibration`. All changes remain uncommitted. No Azure access, development-container startup/test, dependency installation/download, git write, issue, PR, or shared-ledger edit. Backend execution uses only the authorized offline runner and its isolated SQLite.

The screen is R6 screen 90, for a maintenance technician/engineer recording traceability for an existing calibration reading. Its design follows `masterdesignprompt.md`, `designprompt1.md` and the installed EQUA Mantine/InvenTree, visual craft and browser-testing skills. The design uses a restrained provenance section beside each reading, clear text for unknown/expired dates, an attributed revision history, and the existing Work attachment store. No live telemetry, geometry, animation or test acceptance is implied.

## Implementation and boundaries

- `CalibrationTraceability` is append-only, with a Work and reading, instrument ID, certificate number, issuing lab, nullable valid-until date, native certificate attachment and its recorded ID/name/SHA-256/byte-size identity, actor/time, revision, superseded revision and correction reason. Work/reading use CASCADE, recorder uses PROTECT, corrections use RESTRICT within the cascading chain, and native attachment removal uses SET_NULL with the original file identity retained.
- Reserved local migration `tasks.0037_calibration_traceability` depends on `tasks.0032_closeoutcapture_voice_unavailable_reason`. It is applied only inside the offline runner's disposable SQLite. Parallel migration integration and any eventual Azure application remain with the coordinator/deployment owner.
- `POST /api/kanban/work-orders/{work}/calibration-traceability/{reading}/` supports explicit `record` and `correct` intents through the native closeout command boundary. New revisions require current active-user `tasks.capture_closeout`, exact Work scope, calibration Work type, a reading on that same Work, a file uploaded on that same Work, optimistic Work and traceability versions, and a stable idempotency key. Corrections require a reason and append a new row. Both command and event are written to the native Work audit transactionally. An exact retry reauthorizes and returns its original receipt; a conflicting intent is refused.
- Additive `routine_work.v1` fields supply `calibration_traceability_can_record` and each reading's newest-first `calibration_traceability` history. Older/absent capabilities fail closed. Non-calibration Work gains no traceability writer. The native governed Work deletion retains the traceability history in its durable deletion snapshot and still removes the Work.
- Validity compares the certificate date with the native reading's `recorded_at` in the configured server timezone, explicitly shown in the UI. The same calendar day is within the recorded date; an earlier date is `expired_at_reading`; a missing date is `unknown`. This evaluates the recorded date only, not certificate authenticity, physical measurement conditions, calibration acceptance or operating permission.
- The native Mantine form selects an existing uploaded Work file. Upload remains the existing Work attachments action. Missing traceability says **Calibration traceability not recorded**. Viewers have no writer. Unknown save outcomes retain the exact intent/key and lock fields for retry; definite denials and stale versions remain unsaved. Old revisions remain inspectable after a correction. The form pins the Work version, traceability revision and source facts at opening, so a background refresh cannot authorize overwriting a newer revision with an older draft. Changed certificate identity is explicit and has no original-file link. An unavailable or unverified certificate is not preselected for correction. The owner must explicitly select an uploaded certificate.

Native attachment lifecycle is preserved: deleting a certificate file retains the traceability revision and recorded file identity, but does not preserve the file bytes. The UI labels the attachment unavailable. The projection checks native attachment ownership, stored filename, SHA-256 and actual byte size. Unreadable storage is unavailable; old snapshots without a fingerprint remain unknown rather than receiving a fabricated original hash. Native API rename/replace/move/link changes are refused for any referenced revision. Deletion retains metadata and becomes unavailable; trusted storage administrators/plugins are not frozen out by this slice. Byte identity does not authenticate certificate issuance or calibration acceptance. As-found/as-left designation/pairing, method/conditions, complete acceptance criteria, and qualified test/operating authority remain separate facts and are not invented.

## Files

New:

- `src/backend/tasks/calibration_models.py`
- `src/backend/tasks/calibration_serializers.py`
- `src/backend/tasks/calibration_api.py`
- `src/backend/tasks/services/calibration.py`
- `src/backend/tasks/migrations/0037_calibration_traceability.py`
- `src/backend/tasks/tests/test_calibration_traceability.py`
- `src/frontend/src/pages/tasks/workorders/execution/CalibrationTraceability.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/calibrationCommand.ts`
- `src/frontend/src/pages/tasks/workorders/execution/calibrationCommand.test.ts`
- `src/frontend/tests/s6/pui_calibration_traceability.spec.ts`

Shared existing files changed:

- `src/backend/InvenTree/common/api.py` (review: protect referenced certificate identity before native serializer validation mutates the instance)
- `src/backend/InvenTree/users/ruleset.py` (review: three governed-record exclusions)
- `src/backend/InvenTree/users/tests.py` (review: explicit governed-permission policy regression)
- `src/backend/tasks/models.py` (register/re-export model; review: persisted Work type retention)
- `src/backend/tasks/api.py` (import and one Work-scoped route)
- `src/backend/tasks/services/scheduling.py` (traceability in governed deletion snapshot)
- `src/backend/tasks/work_execution/fields.py` (pass authenticated reader)
- `src/backend/tasks/work_execution/routine.py` (additive per-reading facts/capability)
- `src/frontend/lib/types/RoutineWork.ts` (additive types)
- `src/frontend/src/pages/tasks/workorders/execution/RoutineWork.tsx` (inline section and precise remaining-gap copy)
- `src/frontend/tests/s6/pui_routine_work.spec.ts` (update the remaining-gap assertion)
- `src/frontend/tsconfig.s6.json` (include new spec)
- `contrib/ui-ux/r6/reports/P51.md` (disposition note)
- `contrib/ui-ux/r6/reports/P51-calibration.md` (this report)

## Edits reviewed after the pause

The coordinator identified edits made by another agent while this run was stopped. Both files are new/untracked, so I reviewed them with `git diff --no-index /dev/null <file>` and compared their SHA256 values with the retained pre-pause `source-final.json`. Only these two implementation files differed:

- Kept the `calibration_serializers.py` change: unsupported fields/non-object bodies raise a mapping under DRF's configured `NON_FIELD_ERRORS_KEY`. A bare top-level string caused DRF's `.errors` to raise `ValueError` instead of the required HTTP 400. Existing service/HTTP refusal tests exercise this boundary.
- Kept the `0037_calibration_traceability.py` dependency on `common.0053_merge_upstream_20260912`, replacing `common.0001_initial`. This matches the existing investigation-record migration and supplies the complete native attachment state when the schema is rendered. The reserved tasks number and tasks-0032 dependency remain unchanged. The model uses the native AutoField; no extra migration was introduced.

Both handoff edits were correct and in scope and were kept. No reversion was needed. Host hooks passed for both reviewed files before the resumed backend run. The final offline migration-state test is the tasks-scoped `makemigrations --check` equivalent; no migration command was run against Azure or a development database.

## Review findings and dispositions

O12-delegated coordinator review requested the following corrections. All three requested code changes are implemented; the named calibration regressions pass. The required full `users.tests` gate remains **not green** because `test_model_names` finds 52 additional uncovered models already absent from HEAD's ruleset definitions. No existing spec or permission assertion was weakened or skipped, and no blanket exclusions were added for those unrelated models. This is not acceptance of a partially passing backend command.

1. **Certificate substitution — fixed.** `services/calibration.py` streams SHA-256 and actual byte count into `certificate_snapshot` while holding the native Work and attachment transaction locks. It uses neither filename equality nor cached `file_size` as byte proof. Read projection verifies current bytes, caches only within one projection, and returns `unchanged`, `changed`, `unavailable`, or `identity_not_recorded`. Mismatch says **Certificate changed since recorded**, with no original-certificate link. Missing historical fingerprints are not backfilled from current bytes. The correction form uses the same verified-availability predicate as its download link and requires explicit selection for unavailable/changed/unverified files.

   `AttachmentDetail.update` also refuses rename, upload/clear, move or link changes while **any** traceability revision references that attachment, including earlier revisions after correction. It allows ordinary comments. The guard compares against identity fields copied from the locked row **before** `serializer.is_valid()`: native model validation mutates the serializer instance, so comparing afterward had allowed move/link changes. A separate upload plus authorized correction is the native change path. Native deletion still retains the original metadata/fingerprint and shows unavailable.

   Regressions exercise actual native storage rename + re-upload to the **identical original path with different equal-length bytes**, a Work-attachment editor with no `capture_closeout`, earlier-revision protection, a deliberately wrong cached size, unreadable storage with no writes, legacy unknown hashes, no original link/preselection, and reachable correction actions on 390px. The byte hash is technical identity evidence; it supplies no physical/calibration acceptance or certificate authenticity. This addresses record-integrity separation between attachment-edit and execution-record permissions. It follows the repository threat model's trusted-server/admin/plugin assumptions; it is not a public advisory or a claim against malicious trusted storage/DB administrators. No issue or external report was sent.

2. **Retyping hides traceability / denies an already-saved retry — fixed.** Chosen native option: the governed `update_work_order_plan` command refuses an actual Work type change while any traceability exists. The same invariant is enforced under a row lock in `WorkOrder.save`, covering canonical planning PATCH and other native serializer saves as well as the scheduling command; validation compares against persisted type, not an already-mutated serializer instance. Same-type/other planning edits remain available, and an unrecorded Work can still be retyped. This keeps recorded, including expired, traceability visible in its original calibration Work. Exact calibration replay now occurs before the calibration-only **new command** type check, while current active-user permission, Work scope, exact reading and request identity checks still precede replay. Tests cover inspection/corrective retype refusal without writes/version changes, retained expired projection, allowed planning edits and pre-record retyping, HTTP replay after a historical/direct type change returning its original receipt with no duplicate row/event, and canonical PATCH/direct-model-save refusal without hiding the record. New commands on non-calibration Work remain refused.

3. **Ruleset registration — three requested entries fixed; broader baseline gate open.** Added `tasks_calibrationtraceability`, `tasks_investigationrequest` and `tasks_investigationartifact` beside the other governed tasks records in `get_ruleset_ignore()`. The new users policy regression verifies these records use governed command permissions rather than a generic CRUD ruleset. All three are absent from the current uncovered-model failure. Full users tests pass **18/19**; the remaining `RuleSetModelTest.test_model_names` failure lists **52 other** aichat/voice/assets/repair models. Their exact names and proof that they are also absent from HEAD ruleset literals are in `review/pre-existing-ruleset-gaps.json`. Missing fact: the appropriate reviewed CRUD-versus-governed permission mapping for those models. The coordinator was asked for disposition; the scoped default is to disclose this baseline failure and leave those mappings untouched.

### Current review files

Twelve code/spec files and two reports changed during this follow-up:

- `src/backend/tasks/services/calibration.py`
- `src/backend/InvenTree/common/api.py`
- `src/backend/tasks/services/scheduling.py`
- `src/backend/InvenTree/users/ruleset.py`
- `src/backend/tasks/tests/test_calibration_traceability.py`
- `src/backend/InvenTree/users/tests.py`
- `src/frontend/lib/types/RoutineWork.ts`
- `src/frontend/src/pages/tasks/workorders/execution/CalibrationTraceability.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/calibrationCommand.ts`
- `src/frontend/src/pages/tasks/workorders/execution/calibrationCommand.test.ts`
- `src/frontend/tests/s6/pui_calibration_traceability.spec.ts`
- `src/backend/tasks/models.py`
- `contrib/ui-ux/r6/reports/P51.md` (current partial-check disposition)
- `contrib/ui-ux/r6/reports/P51-calibration.md`

No schema/migration edit, renumbering, shared-ledger edit, Azure operation, dependency/download, git write or PR. Migration `tasks.0037` is unchanged.

### Current review checks and evidence

From repository root, the following affected backend command was run twice, sequentially, through the offline runner only:

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/r6-s6/P51-calibration/backend tasks.tests.test_calibration_traceability tasks.tests.test_work_execution_routine users.tests common.test_api.AttachmentAPITests common.test_api.AttachmentThumbnailAPITests tasks.tests.test_scheduling_service tasks.tests.test_scheduling_api tasks.tests.test_closeout_reconcile tasks.tests.test_closeout_security
```

The corrected nine-label replay (before the added persistence regression) had **129 run; 128 pass / 1 fail / 0 skips, exit 1**. Calibration **26/26**, routine **7/7**, users **18/19**, attachments **2/2**, thumbnails **9/9**, scheduling service **19/19**, scheduling API **14/14**, closeout reconciliation **23/23**, closeout security **10/10**. Migration/model consistency, permissions, deletion, revision/concurrency/idempotency and all new calibration cases pass. Only the 52-model baseline ruleset failure remains. The first run had five failure assertions across three tests: move/link comparisons exposed native validation's instance mutation, the new path-reuse fixture assumed an unsuffixed stored filename, and the same users baseline failure. The guard now snapshots before validation and the storage test reuses the actual generated basename; the corrected replay passes those cases. The first and corrected intermediate logs are retained as `review/backend-initial.log` and `review/backend-pre-persistence.log`. The runner reuses only this slice's own retained isolated SQLite in a new network-disabled container. Host `ps aux | grep '[m]anage.py test'` was denied by the sandbox before each run; no owned Django runs overlap.

After adding the persistence-level guard and canonical PATCH/model-save regression, the final command added the canonical Work-order API suite:

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/r6-s6/P51-calibration/backend tasks.tests.test_calibration_traceability tasks.tests.test_work_execution_routine users.tests common.test_api.AttachmentAPITests common.test_api.AttachmentThumbnailAPITests tasks.tests.test_scheduling_service tasks.tests.test_scheduling_api tasks.tests.test_closeout_reconcile tasks.tests.test_closeout_security tasks.tests.test_workorder_api
```

**Final current-source result: 140 run; 139 pass / 1 fail / 0 skips, exit 1.** Calibration **27/27**, routine **7/7**, users **18/19**, attachments **2/2**, thumbnails **9/9**, scheduling service **19/19**, scheduling API **14/14**, closeout reconciliation **23/23**, closeout security **10/10**, canonical Work-order API **10/10**. The sole failure is still `users.tests.RuleSetModelTest.test_model_names` on the 52 unrelated baseline names; all O20 regressions, including canonical PATCH, migration state and governed deletion, pass. `review/backend-final.log`, `review/backend-final-manifest.json` and `review/backend-type-invariant.log` preserve this final result. The extra model guard's first host hook formatted one file (`review/prek-type-invariant.log`); the replay passed (`review/prek-type-invariant-final.log`). The final runner was observed as exactly one owned `manage.py test` process in its own offline container; it remained CPU-busy under host load and completed without intervention.

Frontend command wrapper (root): `python3 data/devscratch/r6-s6/P51-calibration/review_checks.py`. Exact nested commands run in `src/frontend`:

| Command | Result |
| --- | --- |
| `node node_modules/typescript/bin/tsc --noEmit` | 0 errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s6.json` | 0 errors; exit 0 |
| `node --input-type=module -e 'import {startVitest} from "vitest/node"; const context=await startVitest("test",[],{config:"vitest.config.ts",run:true,watch:false,maxWorkers:2},{envFile:false}); await context.close(); process.exit(context.state.getCountOfFailedTests() > 0 ? 1 : 0);'` | 642/642 units in 62 files, including 22 calibration helper cases; exit 0 |

After adding the footer visibility assertion, `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s6.json` was rerun in `src/frontend`: **0 errors, exit 0** (`review/types-s6-footer.log`). No implementation changed afterward.

Browser commands (root):

```sh
python3 data/devscratch/r6-s6/P51-calibration/run_browser.py browser-review-affected tests/s6/pui_calibration_traceability.spec.ts tests/s6/pui_routine_work.spec.ts
python3 data/devscratch/r6-s6/P51-calibration/run_browser.py browser-review-footer tests/s6/pui_calibration_traceability.spec.ts --grep 'changed certificate'
```

**76/76 passed** (34 calibration + 42 routine), desktop and 390px; **4/4** passed after adding the focused footer visibility/capture assertion. Existing specs were not weakened. `review/browser-commands.json` records exact nested clean-environment commands, harness **15220**, websocket **15230**, static native fixture root `fixture-site-review`, exits and native API/external guard restoration hashes. Rendering is **Linux Chromium in the owned `r6-calib-browser-15230` Docker container**, from the current native source compiled by the existing Vite toolchain. No real backend/storage, deployed or physical-device browser acceptance is claimed. No browser load timeout or retry occurred.

Fixture build (in `src/frontend`):

```sh
env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15220 NODE_ENV=development node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s6/P51-calibration/fixture-review.config.mts
```

Build passed with 9,770 modules; its test-bundle chunk warning is retained in `review/fixture-build.log`. The same specified Docker start command from the initial verification was used again, with `--pull never`, the readonly node_modules mount and only port 15230. After browser completion `docker stop r6-calib-browser-15230` stopped only this owned container; cleanup log is `review/browser-stop.log`.

The two browser runs saved **182 PNGs** under `browser/browser-review-affected/` and `browser/browser-review-footer/`; `review/captures.json` lists every path/dimension/hash. I inspected **12 captures**, covering changed identity/no link, empty correction selection and reachable disabled Save footer at desktop/390px in light/dark. `review/inspected-captures.json` records paths, hashes and findings. The phone form uses native vertical scrolling; the follow-up actions captures and visibility assertions verify the footer remains reachable. No horizontal clipping or unreadable changed-state label was observed.

Host formatting/lint used `/opt/homebrew/bin/prek run --files` over the twelve review code/spec paths above. `review/prek-initial.log` records automatic formatting of two files; `review/prek-source.log` is the passing replay. The corrected attachment guard/test passed the same host hook (`review/prek-attachment-fix.log`); the footer-only spec passed it too (`review/prek-footer-spec-final.log`). The final handoff hook passed over all **14 review files (12 code/specs + 2 reports)**, exit 0 with no files modified (`review/prek-handoff.log` / `.json`). Final whitespace, guard restoration, source hashes and container cleanup are recorded in `review/completion-manifest.json`. No container Ruff was used.

Current CI additions: retain the byte-fingerprint/path-reuse and native attachment-update permission regressions, old-revision protection, legacy/unreadable-file unknown states, Work-type retention and replay tests, governed-record ruleset policy, and the four changed-certificate/phone-footer browser cases. Run the exact commands above. The independent 52-model ruleset mapping must be reviewed and repaired before treating full users tests as green; skipping/filtering `test_model_names` or blanket-ignoring unrelated models is not a disposition here.

## Initial implementation verification — before O12 review

The commands/results and source hashes in this section are historical. Current review verification is recorded in the Review findings and dispositions section above. The earlier full S6 result is not claimed as a new full-suite replay after the review changes.

Evidence root: `data/devscratch/r6-s6/P51-calibration/`. Literal validation commands/results are recorded below and in `checks.jsonl`. Browser renders come from **Linux Chromium in `r6-calib-browser-15230`**, websocket port 15230, with harness port 15220. Browser fixtures are synthetic and reject unmocked API/external requests. They are not deployed, real-storage or physical/device acceptance.

Final results are recorded below. The native fixture is compiled with the existing Vite toolchain, `NODE_ENV=development` and minification, then delivered by a scratch Playwright route. The wrapper temporarily adds only native HTML/asset delivery **after** the unchanged rejection of API/media/static/external requests, and restores `tests/s0/guards.ts` byte-for-byte in `finally`. Original/restored SHA256: `9deedf8425e96dd811c219a1abeab7c03fb8251626f04aa80b29825505e26220`. The scratch config extends S6 with the specified Linux executable, websocket/exposeNetwork settings, desktop and 390px projects, harness port 15220, and a 120-second test budget. Native 5-second assertion budgets remain unchanged. No HTTP/Vite server is needed in this final delivery mode.

Earlier direct Vite/static-server attempts and their failures are retained. They are not acceptance. The coordinator cleaned the interrupted servers/containers during the credit pause; the resumed run starts and stops only its own named browser container.

Passed resumed backend command (repository root):

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/r6-s6/P51-calibration/backend tasks.tests.test_calibration_traceability tasks.tests.test_work_execution_routine tasks.tests.test_closeout_reconcile tasks.tests.test_closeout_security common.test_api.AttachmentAPITests common.test_api.AttachmentThumbnailAPITests tasks.tests.test_scheduling_service tasks.tests.test_scheduling_api
```

**102/102 passed, 0 skips, exit 0**. Breakdown: calibration 18, routine 7, closeout reconciliation 23, closeout security 10, attachments 2, thumbnails 9, scheduling service 19, scheduling API 14. `backend/history/20261020T153603408297Z/backend.log`, its adjacent `manifest.json`, and `backend-resume-run.log` retain that historical run; current backend evidence contains the review replay. Each invocation gets a new network-disabled/read-only-source container; this replay uses the slice's own retained isolated SQLite via the runner's native `--keepdb`. The new tasks migration/model state matches. The runner reports existing migration drift in `aichat`, `company`, `order`, `part`, and `stock`; none is a tasks change and none was altered here.

Earlier backend evidence is retained under `backend/history/`: the first six-label run had 64 tests, 50 pass/13 fixture setup errors/1 routine query-time failure under load. The aware timestamp fixture was corrected to explicitly exercise production timezone support, with a separate naive-time regression. The interrupted later eight-label attempt reached a passing migration-state test but exposed two malformed-command errors before stopping. The structured serializer fix is verified by the complete 102-test green replay. No failed or interrupted attempt is described as acceptance.

Resumed frontend commands run in `src/frontend` by `python3 data/devscratch/r6-s6/P51-calibration/resume_checks.py` (wrapper launched from repository root):

| Exact command | Result |
| --- | --- |
| `node node_modules/typescript/bin/tsc --noEmit` | 0 type errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s0.json` | 0 type errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s1.json` | 0 type errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s2.json` | 0 type errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s3.json` | 0 type errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s4.json` | 0 type errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s6.json` | 0 type errors; exit 0 |
| `node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s7.json` | 0 type errors; exit 0 |
| `node --input-type=module -e 'import {startVitest} from "vitest/node"; const context=await startVitest("test",[],{config:"vitest.config.ts",run:true,watch:false,maxWorkers:2},{envFile:false}); await context.close(); process.exit(context.state.getCountOfFailedTests() > 0 ? 1 : 0);'` | 634/634 tests in 62/62 files passed; exit 0 |

All **8/8** TypeScript configurations passed. The unit run includes **14** calibration receipt-boundary cases. Logs: `resume-checks.jsonl`, `resume-checks-progress.log`, `resume-types-*.log`, `resume-units.log`. Earlier checks in `checks.jsonl` passed 8 type configurations and 632 units before the two additional positive-recorder-ID cases; `units-final.log` then passed 634. Extra default/S6 replays and their logs are retained; interrupted pre-pause replays are not used as final evidence.

Passed browser commands (repository root; the wrapper runs the exact clean-environment Playwright command recorded in `browser-commands.jsonl`):

```sh
python3 data/devscratch/r6-s6/P51-calibration/run_browser.py browser-opening-version-green tests/s6/pui_calibration_traceability.spec.ts --grep 'opening versions'
python3 data/devscratch/r6-s6/P51-calibration/run_browser.py browser-resume-targeted tests/s6/pui_calibration_traceability.spec.ts --max-failures=1
```

The opening-version regression passed **2/2** (desktop + 390px), after the retained red run reproduced the overwrite risk. The complete calibration spec passed **28/28**, **14 per viewport**, in both light and dark for color-sensitive states. This includes record/correct round trips, retained history, explicit missing/provider-unavailable/expired/unknown states, viewers, native attachment pagination and unavailable files, exact uncertain-outcome retry with locked controls, 403/409 refusal, an external background refresh while editing, and long-value wrapping. `browser-opening-version-green.log` and `browser-resume-targeted.log` retain the results. No source change was needed after this green run.

The actual nested browser invocation is:

```sh
env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15220 AIMMS_CHECK_LABEL=<label> AIMMS_STATIC_FIXTURE_ROOT=/Users/Aniketkumar/IdeaProjects/InvenTree-r6/p51-calibration/data/devscratch/r6-s6/P51-calibration/fixture-site-locked node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s6/P51-calibration/browser-direct.config.mts --reporter=list <arguments from the wrapper command>
```

That invocation runs in `src/frontend`; `<label>` is the first wrapper argument. Its remaining CLI arguments are exactly those shown above. `run_browser.py` supplies the clean environment directly rather than through a shell.

The passing targeted run saved **62 PNGs** under `browser/browser-resume-targeted/`; `targeted-captures.json` records every path/dimension/hash. I inspected **21 unique current-source captures**, including full Work layouts and traceability/form details on desktop and 390px, light/dark, missing/history/saved states, expired/unknown/within-recorded-date labels, and long recorded values/file names. `inspected-captures.json` records the exact inspected files and findings. Fields align, mobile controls and labels fit, badges/filenames and long IDs wrap, history/actor/time stay readable, and no horizontal clipping appears. These are real Linux Chromium renders of synthetic native fixtures, not physical-device or deployed acceptance.

Earlier browser attempts are retained for review:

| Log stem | Result | Disposition |
| --- | --- | --- |
| `browser-initial` | 1 pass, 1 load failure, 18 not run; interrupted | Direct Vite was slow under load; reran this spec alone using compiled native assets. |
| `browser-targeted` | 2 pass, 1 locator failure, 1 interrupted, 16 not run | Replaced the ambiguous certificate label locator with its accessible combobox role. |
| `browser-final-targeted` | 1 interrupted, 23 not run | Rebuilt the fixture to include the current certificate URL wiring. |
| `browser-verified-targeted` | 2 pass, 1 assertion failure, 1 interrupted, 20 not run | Corrected the test to use the fixture's actual Work version 7 rather than assuming 1. |
| `browser-direct-targeted` | 14 pass, 1 five-second modal assertion failure, 9 not run | Receipt and overview had succeeded; synchronized assertions to those exact responses, retaining the five-second assertion budget. |
| `browser-polished-targeted` | 26/26 pass | Earlier source snapshot, before the opening-version regression. |
| `browser-full-s6` | 4 pass before interruption; scheduled 148 | Stopped after discovering the stale-form version drift; not a full-suite result. |
| `browser-opening-version-red` / `browser-opening-version-red-retry` | 0 tests, startup failures | Orphaned static server occupied 15220; coordinator cleaned it during the pause. Final delivery needs no server. |
| `browser-opening-version-red-direct` | 1 intentional failure | Proved an old draft posted newer Work/revision tokens. Pinning opening tokens fixes this; green 2/2 and targeted 28/28 follow. |

For wrapper-backed attempts the exact CLI, configuration, arguments, clean environment, duration, exit and guard restoration hashes are in `browser-commands.jsonl`. Earlier direct runs used `browser.config.mts`, `browser-static.config.mts`, `browser-final.config.mts` and `browser-verified.config.mts` respectively, with the same port and Docker browser. Their `.log`, traces, screenshots and state files remain under this evidence root. The scratch delivery/configuration changes do not alter product API fixtures or functional assertions.

Fixture build command (in `src/frontend`):

```sh
env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15220 NODE_ENV=development node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s6/P51-calibration/fixture-locked.config.mts
```

The resumed build passed (9,770 modules), producing the exact native code used for all final browser replays. It emits the expected large-chunk warning for the bundled test fixture; no production bundle/performance claim is made. Prior versions of that command used the `fixture-build`, `fixture-fast`, `fixture-final`, `fixture-verified`, `fixture-polished`, `fixture-delivery` and `fixture-release` scratch configs; all seven completed, with logs of the matching stem. The first invocation failed because `fixture-build.config.mts` did not yet exist (`fixture-build-missing-config.log`), and the pre-pause `fixture-locked.log` stopped during transform. Those are superseded by `fixture-locked-resume.log`; no incomplete build is used as evidence.

Browser lifecycle command (repository root; existing image only):

```sh
docker run -d --rm --pull never --name r6-calib-browser-15230 -p 127.0.0.1:15230:3000 --mount type=bind,src=/Users/Aniketkumar/IdeaProjects/InvenTree-r6/p51-calibration/src/frontend/node_modules,dst=/frontend/node_modules,readonly --entrypoint node ghcr.io/browserless/chromium:latest /frontend/node_modules/playwright/cli.js run-server --port 3000 --host 0.0.0.0 --path /r6-calib --max-clients 1 --unsafe
```

`docker ps --filter name=r6-calib-browser-15230 --format '{{.ID}} {{.Names}}'` showed no existing owned container before startup. Startup returned the new container ID; `docker logs --tail 6 r6-calib-browser-15230` showed its websocket listener. `docker inspect --format '{{.Id}} {{.Image}} {{.State.Running}}' r6-calib-browser-15230` records the running container/image identity in `browser-container-running.txt`. Only this named container is started/stopped by the resumed browser run.

Host hooks (repository root):

```sh
python3 data/devscratch/r6-s6/P51-calibration/run_final_prek.py prek-resume-all
/opt/homebrew/bin/prek run --files contrib/ui-ux/r6/reports/P51.md src/backend/tasks/api.py src/backend/tasks/models.py src/backend/tasks/services/scheduling.py src/backend/tasks/work_execution/fields.py src/backend/tasks/work_execution/routine.py src/frontend/lib/types/RoutineWork.ts src/frontend/src/pages/tasks/workorders/execution/RoutineWork.tsx src/frontend/tests/s6/pui_routine_work.spec.ts src/frontend/tsconfig.s6.json contrib/ui-ux/r6/reports/P51-calibration.md src/backend/tasks/calibration_api.py src/backend/tasks/calibration_models.py src/backend/tasks/calibration_serializers.py src/backend/tasks/migrations/0037_calibration_traceability.py src/backend/tasks/services/calibration.py src/backend/tasks/tests/test_calibration_traceability.py src/frontend/src/pages/tasks/workorders/execution/CalibrationTraceability.tsx src/frontend/src/pages/tasks/workorders/execution/calibrationCommand.test.ts src/frontend/src/pages/tasks/workorders/execution/calibrationCommand.ts src/frontend/tests/s6/pui_calibration_traceability.spec.ts
/opt/homebrew/bin/prek run --files src/backend/tasks/calibration_serializers.py
/opt/homebrew/bin/prek run --config data/devscratch/r6-s6/P51-calibration/migration-prek.yaml --files src/backend/tasks/migrations/0037_calibration_traceability.py
```

The all-files hook command passed **exit 0 over 21 file arguments**, with **no files modified**. Its exact command/file hashes are in `prek-resume-all.json`; output is in `prek-resume-all.log`. The two handoff edits were also individually checked before the backend run (`prek-resume-serializer.log`, `prek-resume-migration.log`, both exit 0). The migration is excluded by the root hook's global pattern, so its scratch config copies the repository hook configuration and changes only that exclusion to include this explicit file. The pinned host formatter/linter runs unchanged; no container Ruff is used. Earlier initial formatting fixes and successful source/refinement replays remain in `prek-*.log`. The report-only completion check is recorded below after the full S6 result.

Full S6 command (repository root):

```sh
python3 data/devscratch/r6-s6/P51-calibration/run_browser.py browser-resume-full-s6
```

**150/150 passed, exit 0**, 75 desktop + 75 phone, in 32.8 minutes. Breakdown: calibration 28, routine Work 42, closeout reconciliation 6, closeout report 36, closeout story 8, final update 24, service restoration 6. This includes the full requested S6 routine spec. `browser-resume-full-s6.log`, `browser-resume-full-s6-run.log` and `browser-commands.jsonl` retain the result and exact nested command. The full suite saved **288 PNGs** under `browser/browser-resume-full-s6/`, enumerated in `full-s6-captures.json`; these supplement the 62 targeted PNGs. The 21 inspected captures listed above come from the current-source passing targeted run; unchanged-screen full-suite captures were not all manually inspected.

The wrapper restored the native API/external-request guard byte-for-byte after every completed run, including this full suite. No implementation file changed after the final fixture build or backend/type/unit replays; the all-files hook also made no changes. `git diff --check` and final state verification are recorded after report completion. No resumed browser case timed out or needed a retry. Earlier failed/interrupted attempts remain disclosed above.

Browser cleanup command:

```sh
docker stop r6-calib-browser-15230
docker ps --filter name=r6-calib-browser-15230 --format '{{.ID}} {{.Names}}'
```

Cleanup results are retained in `browser-container-stop.log` and `browser-container-after.txt`. No other container was touched. No HTTP/Vite server was started by the resumed direct-delivery harness.

The process-list guard `ps aux | grep '[m]anage.py test'` was attempted and denied (`Operation not permitted`). Owned backend commands are sequential and use only the offline runner's own disposable network-disabled SQLite container. No shared dev-container test is run.

Completion checks (repository root):

```sh
python3 data/devscratch/r6-s6/P51-calibration/run_final_prek.py prek-reports-completion contrib/ui-ux/r6/reports/P51-calibration.md contrib/ui-ux/r6/reports/P51.md
git diff --check
git status --short
```

The completion hook checks **2 reports**, with all applicable hooks passing and exit 0; implementation files already passed the 21-file hook without modifications. `prek-reports-completion.log` / `.json` retain the exact command and file hashes. Final whitespace/state checks have no whitespace errors, the 21 intended changed files, no guard diff, and no remaining owned browser container. `completion-manifest.json` records the final file hashes, source-check integrity, counts, evidence manifests and cleanup. At that initial checkpoint, all 19 implementation files retained the bytes tested by those runs. No shared ledgers were edited.

## Remaining facts and CI

This O20 record is not calibration acceptance. No facts about as-found/as-left pairing, method/conditions, complete acceptance basis, qualifications, applied isolation, permissions to operate or service restoration are inferred. P51's other provider gaps remain as recorded in its parent report; O21 is outside this slice. Azure and deployed integration are unverified by instruction.

CI should add `tasks.tests.test_calibration_traceability` beside routine, closeout reading/security and attachment suites, include the migration/model consistency and governed deletion regression, run all existing frontend TypeScript projects and units, and run the new S6 spec on desktop/390px with the existing full S6 suite. Include missing/expired/unknown dates, exact-reading/foreign-Work refusal, denied/revoked permissions, stale revisions, corrections with history, uncertain-response identical retry, unavailable file states, and a background refresh while an old correction form remains open. No shared workflow or ledger was edited.
