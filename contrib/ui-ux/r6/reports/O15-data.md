# O15 data decisions — implementation and verification record

Status: **built and tested locally for the O15 review fixes; broader R6/product acceptance remains partial**. Work is local/offline in `InvenTree-r6/o15-data`, left uncommitted for coordinator review. No deployment, physical fact, R6 acceptance or manual review is supplied by fixture success.

<!-- O15 REVIEW START -->

## Review findings and dispositions

**Review status: built and tested locally for the eleven findings below.** Current owner-delegated O12 follow-up. The corrections below supersede the original batch's consumed-as-installed and same-person verification rules. All changes remain local and uncommitted; fixture/offline success is not deployment or product acceptance.

| Finding | Root fix and regression evidence |
| --- | --- |
| 1 — consumption asserted as verified fit | Only a person choosing `spare_installed` in resolve may create/verify a closeout fit. Refresh and walk-up consumption retain native usage without asserting installation. Real custody consume/refresh/walk-up regression and explicit-resolution tests cover this separation. |
| 2 — late reconciliation replaced newer fit | Closeout ordering uses Work actual completion, labelled as that basis rather than actual installation. Missing completion is nullable/pending; older or equal completion cannot replace the slot's newer fit. Late-Work, missing-completion and pending-to-verified version tests cover it. |
| 3 — substitution blocked native reconciliation | Match the slot to the stock item's actual part and return no promotion on row/item/slot mismatch. Promotion no longer raises a domain ValidationError. A real governed substitution after consumption exercises refresh, installed resolution and another refresh without creating a false fit. |
| 4 — self-verification | The recording actor cannot verify their own manual fit. The API refuses and the UI disables self-verification; an independent authorized person's verification succeeds. The old self-acceptance test is replaced with refusal plus peer success. |
| 5 — cross-Work corrected overlap | Start/correction serialize on the affected person's row. Correction checks every current interval for that person across Work, including open intervals. Same-Work and other-Work closed/open overlap refusals are exercised. |
| 6 — migration branches | No migration renumbering. Tasks remains 0034 depending on 0032; the coordinator owns the merge with external 0035_process_decisions. Assets remains 0023; its local-only fitted timestamp is nullable for genuinely missing completion. No Azure migration or write. |
| 7 — remapped critical decision disappeared | Preserve the latest critical obligation with `applies_current=false` and `review_required=true`. It counts as missing critical coverage; Health/evidence/registry explicitly require review and retain decision attribution. Backend, unit and desktop/phone light/dark checks cover the remap. |
| 8 — shared stored-command slot | Time and downtime use separate `time`/`downtime` storage scopes. A legacy unscoped intent is adopted only by its matching family. A co-mounted in-flight start and downtime test checks both retained bodies, reload, exact independent recovery and legacy start adoption. |
| 9 — inactive provenance | Active status survives the AI signal and repair diagnostic allowlists. Health marks retained inactive rows and excludes them from live trend batch requests. Registry only mentions a retained critical designation when it exists. Asset-AI, repair and registry/Health regressions cover these consumers. |
| 10 — time accrued after closure | Bound open intervals and gaps by actual completion or the event entering canceled; later canceled-state audit events cannot move the horizon. Missing closure leaves totals unknown; an open interval is flagged for supervisor correction and clock controls are disabled. Real capture review additionally replaced the misleading Working label on closed Work. |
| 11 — ineffective tests | Quality and gap tests each begin with a valid complete window and perturb only one condition. Tests now actually reject overlap and call GET/POST/proposal on a foreign tenant's Work. They do not rely on a second defect to pass. |

Phone Work coverage includes a synthetic 96 px fixed bottom dock, strict button geometry/hit testing and retained native Start/Pause/Stop checks. Scroll margin supports dock clearance. The capture follow-up removes excess interior phone padding. The actual P47 merge is still the coordinator's integration step; no claim is made about an unmerged screen.

### Checks and actual capture review

| Check | Result |
| --- | --- |
| `review-backend-red` | Ran 6 tests in 2.197s; FAILED (failures=6, errors=1) |
| `review-backend-green` | 0 tests executed; AI-only settings package rejected native s0 settings during discovery |
| `review-backend-scoped` | Ran 352 tests in 77.958s; FAILED (errors=1) |
| `review-backend-final` | Ran 353 tests in 56.974s; OK |
| `review-types` | 5/5 TypeScript projects passed |
| `review-units` | 34 passed (34); 304 passed (304) |
| `review-s4-time` | 18 passed |
| `review-s6-reconciliation` | 20 passed |
| `review-s2-parts` | 18 passed |
| `review-s3-health` | 72 passed |
| `review-s3-shared-views` | 22 passed |
| `review-s2-inspector` | 7 failed; 13 passed |
| `review-s4-closed-render-final` | 18 passed |
| `review-critical-captures-final` | 4 passed |
| `review-pending-fit-captures-final` | 2 passed |
| `review-backend-closure-final` | Ran 25 tests in 0.721s; OK |
| `review-s4-padding-final` | 18 passed |
| `review-inspector-replay-alone` | 20 passed |
| `review-critical-record-counter-final` | 4 passed |
| `review-s6-final-whole` | 20 passed |
| `review-types-final` | 5/5 TypeScript projects passed |
| `review-fitted-completion-types` | exit 0 |
| `review-host-final` | 9 hooks passed; 9 skipped; 0 failed |
| `review-python-final` | 2 hooks passed; 0 skipped; 0 failed |
| `review-cleanup-final` | exit 0 |

Commands, earlier failures, counts and logs are in [the exact command ledger](../../../../data/devscratch/r6-o15/data/validation-commands.md). The six-case pre-fix run reported six failures and one error, including subtest failures; its closure case had a fixture error (missing event correlation ID), so that case is not claimed as pre-fix reproduction. The first broad native run likewise had one fixture error among 352 cases. After correcting that fixture and adding a pending-fit version regression, the offline replay passed 353/353 in 27 modules. The final canceled-event refinement then passed both affected time modules, 25/25; these overlap the broad run and are not 378 distinct tests. The optional extra AI-core modules could not load under native s0 settings; they require their separate AI-only test settings. The affected `assets.ai_read` and native repair evidence tests are included in the passing offline native module run. The extra AI-core attempt executed zero tests and is retained as a discovery failure, not a pass or acceptance skip. The first scratch bundle failed on an extensionless `.mts` import and the corrected build is retained separately. No existing semantic assertion or case budget was weakened.

Final default/s2/s3/s4/s6 types passed 5/5, units 304/304 in 34 files, host hooks 9 passed/9 skipped and forced host Python hooks 2/2 passed. Browser file replays passed Parts 18/18, Health/registry 72/72, shared Performance 22/22, final time 18/18 and final reconciliation/downtime 20/20. Inspector initially had 13 passes/7 failures at the original ready deadline, then passed 20/20 alone with unchanged assertions and budgets. This does not establish load as the sole cause or replace earlier full-suite results.

Renders use Linux Chromium in this task's own Docker browser on 15215, harness 15205; desktop and 390 px, light/dark where colour matters. All **62 named current review captures** were opened (or exactly SHA-256 matched to an original actually opened): Parts/slot 12, time/closure/dock 14, registry 4, critical remap/inactive 4, shared views 4, reconciliation 10, reconciled installation 8, pending installation 2 and inspector 4. Current review paths and hashes are recorded in [review/capture-coverage.json](../../../../data/devscratch/r6-o15/data/review/capture-coverage.json), `captures-inspected.jsonl` and [the reviewed gallery](../../../../data/devscratch/r6-o15/data/captures-reviewed.html). The final transition/hash audit is [review/final-audit.json](../../../../data/devscratch/r6-o15/data/review/final-audit.json). Intermediate captures stay labelled. The V8 render review found the closed-Work Working label and excess spacing; V9 verifies the corrected label, V10 removes the phone padding, and V11 clarifies the Health record counter when retained inactive bindings exist. Final time/dock checks use V10; the V11 counter replay passes 4/4. The final full reconciliation file uses V11 and coherent fixture completion timestamps, passing 20/20. Element crops can include native sticky-shell overlays; the retained full-page audits supply layout context. Synthetic Health mapped counts now exclude inactive rows, capture setup waits for the selected trend/evidence, and pending-fit mocks agree across native usage and material reads. The final direct S6 shell command and stdout are retained, but its exact start time and expanded PATH/HOME were not separately captured; that retention gap is explicit in the journal.

Open boundaries: earlier full-suite failures remain historical results; these targeted checks do not turn them into a green full replay. The actual P47 merge, separately authorized disposable PostgreSQL rehearsal and real physical/operational records remain unprovided. No Azure write/app start and no other checkout/container mutation is performed. The owned browser stop and closed-port audit are retained in the command ledger at completion.

CI should include the new `tasks.tests.test_recorded_fits_reconcile`, revised recorded-facts/work-time tests, asset-AI/repair active-provenance tests, the remap coverage unit, separate-intent/reload case, pending-fit case and fixed-dock phone case, alongside native closeout/job-kit and permission regressions. No shared workflow or ledger was edited.

### Files changed by this review

- `src/backend/InvenTree/assets/ai_read.py`
- `src/backend/InvenTree/assets/migrations/0023_registry_recorded_facts.py`
- `src/backend/InvenTree/assets/record_api.py`
- `src/backend/InvenTree/assets/record_models.py`
- `src/backend/InvenTree/assets/recorded_facts.py`
- `src/backend/InvenTree/assets/tests/test_ai_read.py`
- `src/backend/InvenTree/assets/tests/test_recorded_facts.py`
- `src/backend/InvenTree/repair/services.py`
- `src/backend/InvenTree/repair/test_health_diagnostics.py`
- `src/backend/tasks/services/closeout_reconcile.py`
- `src/backend/tasks/services/work_time.py`
- `src/backend/tasks/tests/test_recorded_fits_reconcile.py`
- `src/backend/tasks/tests/test_work_time.py`
- `src/frontend/lib/types/RecordedFacts.ts`
- `src/frontend/src/components/records/useRecordedCommand.ts`
- `src/frontend/src/pages/assets/CriticalSignalReview.tsx`
- `src/frontend/src/pages/assets/FittedIdentity.tsx`
- `src/frontend/src/pages/assets/health/SignalEvidence.tsx`
- `src/frontend/src/pages/assets/health/SignalTable.tsx`
- `src/frontend/src/pages/assets/health/healthCoverage.test.ts`
- `src/frontend/src/pages/assets/health/healthCoverage.ts`
- `src/frontend/src/pages/tasks/workorders/RecordedDowntime.tsx`
- `src/frontend/src/pages/tasks/workorders/WorkMaterialsPanel.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/RecordedWorkTime.tsx`
- `src/frontend/tests/s2/pui_equipment_parts.spec.ts`
- `src/frontend/tests/s3/healthFixtures.ts`
- `src/frontend/tests/s3/pui_critical_designation.spec.ts`
- `src/frontend/tests/s3/pui_health_condition.spec.ts`
- `src/frontend/tests/s4/pui_work_time_recording.spec.ts`
- `src/frontend/tests/s6/pui_closeout_reconciliation.spec.ts`

<!-- O15 REVIEW END -->

The native Django/React/Mantine recording providers implement the owner's O15 D1–D5 decisions. No dependencies, shared ledgers or CI workflow were changed. No git write, PR, issue or Azure operation was performed.

- **D1 / P37:** engineer/admin registry review appends attributed, reasoned critical-designation versions. A designation applies to the exact binding meaning (source, pointer, dictionary, unit/kind/transform). Meaning changes require re-review; a latest critical decision remains missing critical coverage without erasing history. Cited retired bindings remain inactive and designated missing/unknown coverage until reviewed. Only designated bindings enter critical coverage; unassessed evidence never becomes healthy. Unmarked stays not designated.
- **D2 / P13, P07, P46:** only an explicit installed disposition promotes tracked stock on an exact Work machine, registry slot and catalogue-part match, using known Work completion for ordering; older/missing completion stays pending. Serial/lot is snapshotted; Work, date and recorder are retained. Untracked usage still reconciles natively and leaves fitted identity unrecorded. Existing-fit manual entries require a separate explicit verification command, recording entered/verified people and times; a different authorized person must verify the manual entry. Native registry Work-change and Stock-view permissions apply. Parts, slot detail, position inspector and material reconciliation show sourced current fits/history; corrected installed usage retains a retracted original. Closeout dates are labelled as Work completion for fit ordering; actual installation time is not separately recorded. Manual fitted dates are explicitly entered with a timezone.
- **D3 / P27, P46:** per-person server-clock Start/Pause/Stop on Work, including phone. Totals count recorded intervals only, at the last server read. Unclassified gaps remain explicit and unrecorded people's coverage unknown. Supervisor/admin correction closes forgotten open intervals or corrects closed intervals with an explicit end and reason, supersedes a retained original and reuses expected Work versions. Assignment wall elapsed and native job dates remain separate; no activity/labor classification or browser timer is invented.
- **D4 / P46:** the native bounded trend reader finds an unambiguous documented run/stop binding. Complete observed run→stop→run transitions propose windows for technician confirmation/correction. Initial already-stopped samples, bad quality, gaps, shifted clocks, missing/ambiguous bindings, truncation and incomplete windows do not invent downtime. Confirmed machine stops remain separate from unavailable service-outage facts. Signed actor/Work/binding-bound proposals expire; binding changes, overlapping duplicate windows and reasonless corrections are refused. Originals survive correction.
- **D5 / P39:** creators own shared machine Performance display views; native machine readers see them; engineers/admins curate defaults. Creator and curator attribution and times are visible; mutations carry expected versions. Restore stays explicit and uses current mapping; saved display choices grant no evidence/access. Personal browser views stay available. Only an owner deletes their view; removing a default also requires engineer authority.

A successful recording acknowledgement must contain a positive integer version; malformed 200 replies retain uncertainty and never show Saved. Recording intent is retained in local storage before send and read back, scoped by actor/server/endpoint and by binding for registry forms. Unknown outcomes, 408/429 and refused reconciliation retain the exact original payload/key/version across reloads. Reconciliation-only retry returns the original saved result or a durable terminal not-applied receipt; it never starts a late operation. The same key cannot later execute after an absence receipt. Current authorization and scope still apply before replay. Query keys include actor, host and authentication generation where shared reads require it. The existing CORS request-header allowlist admits the reconciliation-only header; actual middleware tests confirm allowed-origin preflight and refusal of a disallowed origin without changing origin or authentication policy.

Migrations are local artifacts only: `assets/0023_registry_recorded_facts` depends on assets 0022 and tasks 0032 (plus native stock/user dependencies); `tasks/0034_work_time_downtime` depends on tasks 0032 and assets 0023. Binding ownership is assets; no other app number was needed. No migration was applied to Azure.

Evidence: [exact commands and retained results](../../../../data/devscratch/r6-o15/data/validation-commands.md), [per-ticket files](../../../../data/devscratch/r6-o15/data/files-by-ticket.md), [source hashes](../../../../data/devscratch/r6-o15/data/source-final.json), [all PNG captures](../../../../data/devscratch/r6-o15/data/captures.json) and [actual capture reviews](../../../../data/devscratch/r6-o15/data/captures-inspected.jsonl).

## Pre-review checks and retained failures

Default and all seven `tsconfig.s*.json` projects passed **8/8**. Asset/work unit checks passed **303/303 in 34 files**. The final offline backend replay passed **260/260 in 18 modules**, including initial-stop boundaries, open-interval supervisor correction, current-catalogue verification, reconciliation-only CORS preflights, migration state and permission suites. Host source hooks passed **9 applicable hooks**, with **9 skipped**; the forced host Python configuration passed **2/2 hooks**, covering tests/migrations excluded by normal hooks. These are pre-review checkpoint results; the review section above is authoritative for the affected final source. No fixture run establishes product acceptance.

| Run | Result |
| --- | --- |
| `browser-final-critical` | 4 passed |
| `browser-final-s2` | 53 failed; 3 skipped; 28 passed |
| `browser-parts-synchronized-alone` | 18 passed |
| `browser-final-s4` | 1 failed; 171 passed |
| `browser-assignment-time-alone` | 8 passed |
| `browser-time-provenance-alone` | 1 failed; 13 passed |
| `browser-time-ack-synchronized-alone` | 14 passed |
| `browser-final-s6` | 2 failed; 88 passed |
| `browser-closeout-report-alone` | 36 passed |
| `browser-reconciliation-resumed-alone` | 16 passed |
| `browser-report-source-trace-alone` | 2 passed |
| `browser-resumed-s3-health-badge-fixed-targeted` | 4 passed |
| `browser-resumed-s3-source-type-fixed-targeted` | 4 passed |
| `browser-resumed-s3` | 22 failed; 218 passed |
| `browser-resumed-comparisons-alone` | 3 failed; 19 passed |
| `browser-resumed-comparisons-untraced-alone` | 22 passed |
| `browser-resumed-scale-500-light-case-alone` | 1 passed |
| `browser-resumed-s2-controls-alone` | 15 failed; 3 skipped |
| `browser-resumed-s2-inspector-alone` | 20 failed |
| `browser-resumed-s2-scene-alone` | 8 failed; 6 passed |
| `browser-resumed-s2-schematic-alone` | 2 failed; 12 passed |
| `browser-resumed-s3-health_condition.spec-alone` | 28 passed |
| `browser-resumed-s3-health_inspection.spec-alone` | 14 passed |
| `browser-resumed-s3-health_trends.spec-alone` | 22 passed |
| `browser-resumed-s3-performance_native.spec-alone` | 2 passed |
| `browser-resumed-s3-performance_scale.spec-alone` | 1 failed; 17 passed |
| `browser-resumed-s3-performance_truthful.spec-alone` | 10 passed |

Latest isolated S3 files: Health condition 28 passed; Health inspection 14 passed; Health trends 22 passed; native Performance 2 passed; scale 1 failed; 17 passed; truthfulness 10 passed; untraced comparisons 22 passed. The scale file's remaining light desktop 500-channel case timed out at the final navigator scroll after its strict request-identity assertion passed; its one-case diagnostic is 1 passed. Original full-suite results and all failed diagnostics remain retained; no union of isolated passes is reported as a green full suite.

At the pre-review checkpoint, native S2 initial ready/failed scene deadlines remained unresolved; the current review inspector result is separately recorded above. The four isolated native files total 18 passed, 45 failed and 3 skipped across 66 cases; these are separate from the full S2 result. The inspected scene trace eventually reached ready after the five-second assertion failed, with successful script requests and a recorded GPU ReadPixels stall. Its sparse snapshots and recording overhead do not establish the original delay's sole cause. No scene engine source or native semantic deadline was changed.

The pre-review source checkpoint was V7; the review transition and final V11 evidence above supersede it for affected areas. The final all-project type replay and host-hook result are listed in the command journal. `final-freeze-audit.json` checks the frontend source and bundle hashes and retained backend result; `screen-capture-coverage.json` checks all 50 named ticket captures against actual review hashes. The task-owned browser is stopped at completion; the stop command is retained in the journal.

Browser renders: **Linux Chromium in the task-owned cached-image Docker container `r6-data-browser-15215`, browser port 15215, fixture harness 15205. Desktop and 390 px phone; light/dark where colour matters.** The initial Parts run used the repository's 45-second case budget and passed 5/16; its 45-second Parts-only replay passed 7/18. Both logs/traces remain. Later scratch-config targeted and full-suite runs use 120 seconds on this shared machine; assertions are unchanged. The frozen Health phone cases alone took 48–52 seconds, so a 45-second full-suite budget would have stopped them before their assertions. The incorrect initial new dialog selector was corrected. Earlier incremental comparison replay retained timeouts while mounting/interacting with full signal/chart fixtures and overlapped frontend edits. V1 uses `source-browser-frozen.json`; its full S3 attempt was intentionally interrupted after 111 completed cases (97 passing, 14 timeouts) to move to the final reviewed V2 bundle. Later connection failures in that obsolete raw log are interruption fallout. V2 critical replay passed 4/4; full V2 S2 finished 28 passed, 53 failed and 3 skipped. Its failures group as 50 scene-ready checks, one scene-failed check and two first-row Parts load checks. The Parts fixture now waits for the mocked provider response and settled query state before unchanged five-second value assertions; Parts alone on V3 passed 18/18. A separate V3 render-copy fix makes the Parts introduction distinguish registry slot review from fitted identity. Only InstalledPartsPanel.tsx and the Parts spec changed after V2; an additional S2 type check passes on both through the native harness. V2 inputs/outputs stay preserved; `source-browser-v3.json` and `source-browser-v3-transition.json` freeze the two changed inputs and `bundled-harness-v3-manifest.json` hashes V3. Full S4 used V3. A render finding then corrected missing interval end actors to Not recorded and separated the end-record basis; V4 Time and full S6 use that fix. The Time correction spec now waits for the actual saved acknowledgement before its unchanged five-second fact assertions. Full S4 retained one generic case timeout, followed by an unchanged legacy assignment-time file replay passing 8/8. Time recording replay passed 14/14 after its acknowledgement synchronization. Full S6 retained two source-loading assertion timeouts in the same native closeout-report case, followed by the unchanged whole report file passing 36/36. These isolated passes do not rewrite the full-run results. All retain the 120-second case budget and original semantic assertions. Those completed final results and isolated replays determine regression status. The timeout cause is not established by the earlier runs. The new shared-view case uses one real fixture binding because it checks ownership/default metadata; the preserved native multi-channel cases remain in the full suite.

Earlier backend results are retained: initial 50/52 (URL/query-count expectations fixed), broad 231/233 (provider assertion and self-FK migration state fixed), and 251/254 (new tests now use production `USE_TZ=True`; native test mode otherwise disables zones). The migration-state check now uses the loader/autodetector without a live DB and passes in the broad run. The runner still prints pre-existing migration drift in unrelated aichat/company/order/part/stock; this slice adds no migrations to those apps. Nonzero formatting passes and early test/type failures are retained in the journal rather than counted as successful final checks. Early direct type/hook output also remains in `types-initial.log`, `types-s6-initial.log`, and `prek-*-initial.log`; their original direct hook argument subsets were not retained and are not reconstructed.

Backend execution used only `backend_baseline.py`: new network-none disposable containers, copied source, isolated synthetic SQLite and clean environment. A fresh migration run passed 250/250; later replays use a read-only backup of that owned synthetic test schema with the runner's existing keepdb behavior (see `backend-storage.md`). No existing development DB is used. The mandated `ps aux | grep '[m]anage.py test'` guard was attempted before each run and denied by sandbox policy; no Django test commands in this checkout overlap and its test SQLite path is exclusive.

## Continuation after credit interruption

The coordinator reported possible intervening edits in 14 frontend files. Each git diff was read and compared with saved source hashes. All are correct and retained: named type-only imports replace inline import types in Assets, MachineHealth, WorkExecution, machinePositions, materials and workExecutionFixtures; imports move into existing import blocks in EquipmentRegistry, PositionInspector, DisplayPreferences, WorkMaterialsPanel, TimeAssignment and ComponentSlotDetail; tsconfig.s3/s4 indentation is normalized. No intervening domain behavior was added or reverted. `resumed-source-diff.json` and `resumed-edit-review.md` retain this review. Backend hashes are unchanged from the final 260-test source (`resumed-backend-source-audit.json`).

The reconciliation capture replay was interrupted after 3/16 completed passing cases; it has no completed CLI result. Its log is retained and a newly named full-file replay follows above. Final S3 had not begun. The coordinator removed old servers/containers. This continuation started a fresh task-owned browser on 15215, rebuilt the reviewed frontend as V5, hashed its outputs and source, reran all eight type projects and host hooks, then ran the remaining browser commands serially. V5 changes from V4 are the 14 import/style edits only. The full-page reconciliation capture setup uses the existing capturePanel helper to start at the top; domain assertions are unchanged. The source-read case also runs alone with tracing explicitly enabled; the first isolated comparison and V6 badge target retained failure traces for diagnosis. The comparison trace later showed simple checked-state reads taking tens of seconds and a pause-label click consuming the case budget before containment assertions. That traced failure stays retained; the subsequent full-file untraced comparison replay passed 22/22. Subsequent failed S3 file replays use tracing off, matching the full-run mode; native S2 scene alone retains failure traces. These transitions and inspected events are recorded in browser-untraced-replay-transition.json and resumed-comparison-trace-review.json. The full S3 config was already loaded with tracing off; later resumed S3 commands kept tracing off. This mode difference is recorded and does not establish the cause of the original timeout. The exact source-read case passed 2/2 with tracing on; both traces were read. Narrative assertions resolved in 1.995 seconds desktop and 0.752 seconds phone; source reads returned 200, the two export gates and revision order passed, and no traced errors occurred. This supports an intermittent initial-source/readiness timeout; no O15 report regression was reproduced. The original full-run trace was off, so its exact delay remains unproven. See `browser-resume-findings.md` and `report-source-trace-summary.json` for the trace review. Earlier and resumed logs stay separate. Full S3 scale errors were also read: two light request-identity assertions included legitimate later Live polls during long retained-control interactions; other scale cases retained setup/case timeouts. After the full frozen command exited, three test-only files were changed to synchronize actual initial mocked history and pause Live through the native control before comparison/scale retained-control assertions. Live/loading scenarios keep the original helper default. Scoped S3 types and host hooks passed before the isolated replays. Source-browser-v5-after-perf-pause.json and performance-pause-transition.json preserve the three-input test transition; application source and the V5 bundle stay unchanged. No semantic assertion or case budget is changed. The original full S3 phone critical-coverage cases exposed a real remaining Connected badge truncation: a 720 px scrolling table still allowed the badge to shrink. The V6 runtime uses intrinsic max-content width with no max-width cap for connected/failing/disabled badges, retaining native horizontal scrolling. Host hooks, scoped S3 types, a fresh V6 bundle and the unchanged four desktop/phone light/dark coverage cases gate subsequent browser replays. Source-browser-v6.json/transition and the V6 output manifest retain this render fix; original V5 full S3 evidence is preserved. Actual V6 phone rasters then exposed the SCADA source-type chip shrinking while Connected was complete. V7 wraps that chip in a native intrinsic-width Box, adds its own unclipped-label assertion, and waits for fixture reads before captures. Source-browser-v7.json/transition and its output manifest retain the two-input render/test follow-up. The same four coverage cases passed 4/4 before the remaining native replays; all four final V7 originals were inspected.

## Captures reviewed and resulting fixes

See `browser-harness-notes.md`, the versioned source-browser snapshots and hashed bundled-harness manifests (V1–V7 retained) for the fixture-server/trace changes. The final review gallery indexes only unaltered PNG files whose exact hashes match actual inspection records; intermediate and stale captures remain distinguishable in the journal. The initial S2 interrupt was denied and that attempt was not interrupted. Saved PNGs and retained failure traces live under `data/devscratch/r6-o15/data/browser/`; the review journal records exact paths, hashes and notes for images actually opened. Current/intermediate review flags distinguish old captures. The pre-review checkpoint's 50 named area captures were saved and reviewed (an original raster opened or an exact SHA-256 match to one actually opened): Parts/slot 12, work time 10, registry designation 4, Health coverage 4, shared views 4 and reconciliation 16. The coverage JSON identifies each exact path; this does not claim every saved failure screenshot was inspected. Render review prompted compact table facts, a separate slot-assignment warning, an accurate pending-fit summary, a Parts introduction that separates slot review from fitted identity, native scrolling for the source-status table, a shorter phone correction button, full-page correction/confirmation audit captures, an amber incomplete-coverage warning and clearer reconciliation date wording. Captures never stand in for deployed or persisted authenticated product acceptance.

## Open facts and CI follow-up

The coordinator still needs PostgreSQL migration rehearsal on a separately authorized disposable database and target role configuration, real critical designations, verified physical fit evidence, actual technician intervals and live unshifted run/stop history. No supplied fixture makes those records real. Wider assignment factors/accepted handover, service impact, removed/retained/repair/warranty/core/quarantine provenance remain outside this data slice. Current interval records are separate from the accepted account/export labor-total field, which is not supplied; no accepted labor total is inferred from current intervals or assignment elapsed time. Unrecorded/unavailable states remain explicit. Any unresolved browser failures above prevent a full regression-green claim; their logs/traces name the cases and missing runtime verification.

CI should run the 18 offline modules listed in the exact command journal, including permissions, migration state, remap/retirement, fit reconciliation/retraction/manual verification, interval attribution/correction/version/replay, durable absence receipts and censored/gapped/duplicate downtime. Add default/all sprint types, asset/work units and changed S2/S3/S4/S6 desktop/390px cases. Keep the full native sprint suites and isolate/replay timeout specs. No workflow was edited because the coordinator owns shared integration.

## Changed files by ticket

The complete expanded mapping is in `files-by-ticket.md` and JSON, including shared recording infrastructure and tests. Reports changed: this file plus P13, P27, P37, P39 and P46. Shared source edits are enumerated there; all source/config/test hashes are in `source-final.json`. Shared ledgers and other checkouts were not edited.
