# O27 — recorded telemetry as live

21–22 October 2026. Worktree `live-view`, branch `r6/live-view`; inspected starting
HEAD `51901680a06f0938c9cf8f48bfa2809f8b474be4`. This is local source and isolated
fixture verification. It is not deployment, real plant connectivity, model-provider
verification, human approval or product acceptance.

## Owner decision and switch

`AIMMS_RECORDED_TELEMETRY_AS_LIVE` is the **one switch**, registered as a Django
boolean in `aimms_flags.py`, environment-settable and **on by default**. Set
`AIMMS_RECORDED_TELEMETRY_AS_LIVE=false` and restart the application to restore
O23 presentation. There is no separate frontend or AI setting. The backend sends
`recorded_telemetry_as_live`; AI projections also receive the derived server-owned
`telemetry_presentation` policy.

O27 supersedes O23's recorded/historical presentation while on. Both states retain
`display_shifted` and `display_shift_seconds`. Source queries use inverse-shifted
plant instants; stored observations, source ranges and all migrations are unchanged.
The displayed range is capped at the latest 30 days on; the original July 1–August 1
range remains in `source_from`/`source_to`, and off retains the old offered range.

## Screen definition and resulting behavior

Applied `masterdesignprompt.md`, `designprompt1.md`, installed EQUA industrial
telemetry, Mantine/InvenTree, browser-testing and visual-QA skills. No Figma,
framework replacement, dependency addition or redesign of Machine Details/header.
The table supplies the master prompt's screen/user/objective/provider/constraint
block for this bounded change.

| Screen and user objective | O27 on | O23 off and retained boundaries |
|---|---|---|
| Station/pump mimic: operator reads current states, values and alarms | Live/just-now presentation from displayed observation times; no recorded-history badge or shift note. Approved local readings remain available with scheduled polling stopped. | Existing labels and polling-disabled behavior remain. Missing, stale, invalid and failed readings stay explicit; no poll task is enabled. |
| Machine Health: engineer assesses signals and source reporting | Shifted current readings drive source reporting and last reading. No historical/shift caveats. 7/30-day choices use sampled series beyond the native complete-read limit; date ticks and gaps use actual sampled resolution. | Original caveats and complete-trend 6-hour limit remain. Original 24-hour navigation/slicing policy is preserved, including before range metadata arrives. |
| Performance and station/comparisons: engineer investigates current month | 7/30-day options, displayed date bounds and sampled source reads. Live freshness uses the latest source observation, separately from the older last chart sample. | Original 24-hour series/window policy and shift disclosures remain. Failed/empty/unavailable responses cannot become Live. |
| Machines list conditions/alarms: operator prioritizes equipment | Existing shared health-condition reader uses displayed observations; native alarm lifecycle and severity remain authoritative. | Same batch scope/query boundaries and unknown/unavailable states; no per-row history reads or permission changes. |
| Fault history/monitoring: reliability engineer inspects deviations and evidence | Occurrence observations and case pickers use display dates; canonical investigation links convert chosen bounds back to plant time once. Source reporting uses displayed readings. | O23 disclosures and original clock behavior retained. Snapshot/sample/capture facts, human actions and maintenance history stay immutable. A retained snapshot is still identified as retained evidence; missing captured history is never reconstructed. |
| Assistant telemetry answers: operator asks about the same data | Scoped machine/diagnostic projections carry display-clock observations, reporting/read-failure facts and a server-owned instruction to use live presentation without recorded/replay caveats. | Off policy requests O23 labelling. Native scopes, fenced untrusted values, real failures and audit/provenance remain. No live model provider was called. |

## Source and safety details

- `display_time.py` centralizes the policy, shift provenance and displayed-source
  reporting. Source failure is distinct from stale observation and from an old
  resolved error/poll record; a station-specific checkpoint failure is retained.
- `activation.py` separates the presentation's effective availability from the
  unchanged scheduler flag. Existing checkpoint, source/station/client activity,
  configured station and approved-binding protections remain. Latest observation
  is folded into the existing aggregate: the original 9-query mimic assertion passes.
- Long sampled series retain point/document limits. O27 sampled Cosmos windows
  longer than 24 hours have a shared 60-second source-work deadline; shorter
  windows retain their existing budgets. Exhausted work returns `SOURCE_TIMEOUT`, not successful
  empty data. Complete historian scans retain their existing smaller bound.
- Fault observations shift only where appropriate; retained evidence is returned
  as captured. Snapshot comparisons normalize occurrence bounds to the evidence
  clock. Case forms show display dates while their links retain exact plant bounds.
- API serializers, activation status, available/unavailable range envelopes,
  summary/signals, mimic, trends, series, occurrences and AI projections propagate
  the one policy. No stored records, migrations, permissions or tenant scopes changed.

## Execution ledger

| Slice | Status | Evidence |
|---|---|---|
| O27/O23 and native audit | Complete | Decisions, source and initial clean worktree inspected |
| Backend/API/assistant implementation | Complete; isolated verification | 103 selected tests in original test classes pass with explicitly supplemented synthetic schema |
| Frontend implementation and units | Complete; broader browser replay in progress | 940 units / 94 files pass; O27 desktop/phone focused 12/12 |
| O23 rollback preservation | Passed in full S3 plus corrective replay | Original clock/navigation assertions retained under explicit off fixtures; 8/8 earlier focused cases and corrected retained cases pass |
| Full S3 | Verified with corrective replay | Original 292 cases reconciled; 12 additional failure cases; 304 unique covered |
| Full S7 | Verified with corrective replay | All 178 unique cases reconciled; original failures and unchanged reproduction retained |
| Full S8 and remaining inspected renders | In progress | Task-owned Linux Chromium WS 3009 / harness 15242 |
| S0 preservation | 110 distinct cases verified; clean final replay scheduled | Initial 108 pass + exact two timezone replays pass |
| Final tracking/hooks/runtime cleanup | Pending final browser results | Parent sprint/backlog acceptance statuses remain unchanged |

## Actual verification

Evidence root: `data/devscratch/live-view/`. Browser renders come from **Linux
Chromium** in `r6-liveview-browser-3009`, using only WS 3009 and harness 15242.
The scratch wrapper is `browser.config.mts`; no browser or package was downloaded.

| Check | Actual result | Evidence |
|---|---|---|
| Full Vitest | 940 passed / 94 files | `vitest-scheduler-final.log` |
| TypeScript, all 12 configs | App/library/all eight sprint projects pass; app/S7/S8 also rechecked after scheduler fixes. Standalone Node diagnostics remain byte-identical; broad Playwright diagnostics match initial messages apart from fixture line positions | `typecheck-final-tree/results.json`; respective logs |
| Lingui extract + compile | Both pass after final scheduler strings; all 117 locale files restored bytewise | `lingui-scheduler-final/results.json`; `locales-before.json` |
| Changed-file prek | 83-file root run plus final fixture-file hooks pass; final 85-file replay pending | `prek-scheduler-final.log`; per-file hook logs; `changed-files.json` |
| R6 tracking | 58 work items / 95 screen dispositions / 26 contracts pass | `validate_tracking.py` |
| S0 desktop/phone | 108 initial pass + 2 exact timezone replays pass = 110 distinct cases | `s0.log`; `s0-timezone-replay.log` |
| O27 desktop/phone scenarios | 12/12 pass | `s3-o27-final-v2.log`; `browser-s3-o27-final-v2/` |
| Full S3 + corrective replay | Initial 285 pass / 7 failures; all 7 pass in fresh 24/24 replay; 304 unique cases verified including 12 added refusal cases | `s3.log`; `s3-repair-replay.log`; captures in corresponding directories |
| Full S7 + corrective replay | Initial 168 pass / 10 failures; all 10 pass in fresh 14/14 replay; all 178 unique cases verified | `s7.log`; `s7-keyboard-reproduce.log`; `s7-repair-replay.log` |
| S8 desktop/phone only | Stable restart running, 536 collected; Work excluded | `s8-awake.log`; `browser-s8-awake/`; stable probe 12/12; focused mimic 4/4 |

The first S0 wrapper omitted the example's `Asia/Riyadh` timezone; two unchanged
assertions expected 15:00 but the Linux default rendered 05:00. Explicit Riyadh
configuration fixed both without product or test assertion changes. Initial O27
focused runs exposed new-test locator mistakes, preserved with their captures;
corrected semantic locators pass. Full S3 retained seven initial failures: one previously documented Linux dialog-footer visibility result and six fixture expectations loaded before the explicit off-policy repair. A fresh-process 24/24 replay passes all seven, including the unchanged full-visibility footer assertion, and the 12 newly added failure cases. Render inspection also exposed source-clock case
inputs beside shifted observations; the form now displays the same clock as the
occurrence while preserving source-clock URLs.

The two baseline TypeScript failures are optional bundler declarations in
`tsconfig.node.json`, and existing generated-version/import/mimic-union errors in
`tsconfig.playwright.json`. Node diagnostics compare bytewise equal to the initial checkout. Broad Playwright
diagnostics match exactly after normalizing line/column positions shifted by the
new native station test (`typecheck-final-tree/baseline-comparison.json`). No configuration was weakened or dependency installed to hide
them. All application/library/sprint projects pass on the final source and fixtures.

Full S7's ten initial failures are six screenshot-helper waits for the old
deviation heading after unavailable/refused reads, and four keyboard sequences
that omit an existing native `Talk about this work order` launcher. The error,
scope and no-healthy-verdict assertions pass before the capture waits. The
capture helper accepts the two exact policy headings; successful off fixtures
explicitly retain the original heading assertion. The keyboard launcher is
confirmed in unchanged HEAD; an unchanged desktop replay reproduces both widths.
The test now asserts the Talk launcher before retaining every original subsequent
Ask/workflow/tab assertion. The fresh 14/14 replay passes all ten original failures
and successful off headings in both projects. No product focus behavior or timeout
limit changed. S7 TypeScript and changed-test hooks pass.

The first focused S8 attempt stopped before the fixture banner: the harness's
static Layout import reaches `ConversationFocusControl`'s module-scope translated
labels before its body activates Lingui. Both the import order and translated
constant are identical to HEAD. The run retains one startup failure, one
interrupted case and two unrun cases. S8 now reuses the existing S0/S3 first-import
locale bootstrap; no native product translation code, guard, assertion or timeout
is changed. Cold focused replay passes 4/4; the full desktop/phone lane is running.

The first full S8 attempt then exposed the runner's working-directory assumption:
`monitoringEvidence.ts` defaults relative to frontend cwd, but this scratch
runner starts at the worktree root. Nineteen cases failed to create the resulting
outside-checkout evidence directory; the sandbox denied every attempt. The stopped
partial run preserves 89 passes, 19 evidence-path failures, two skips, one
interrupted case and 425 unrun cases. The final run sets the existing
`AIMMS_MONITORING_EVIDENCE_ROOT` to the absolute worktree-local
`data/devscratch/live-view/monitoring-evidence-final` path. Its accessibility helper
uses cached axe-core **4.12.0** copied from this task's own browser container;
`axe-source.json` records the source and SHA-256. No download, package installation,
permission broadening or UI/test-assertion change was needed. Final full replay is
`s8-final.log` / `browser-s8-final/`; original partial artifacts remain intact.

That restart subsequently encountered widespread runtime progress loss: navigation,
module loading, evaluation and fetch waits across unrelated screens timed out,
including CDP-session closure. Traces show approximately 16m50s host/browser timing
discontinuities; the exact OS/VM cause is unproven. The run was stopped with
114 passes, 41 failures, two skips, one interrupted and 378 unrun cases; its reported
10.5-hour wall duration includes those disruptions. This is not reported as product
failure or completed coverage. Only the owned browser container was reset, using
the original cached-image command. Host/container UTC clocks then aligned.
An unchanged representative probe uses a temporary `caffeinate -i -s` wrapper tied
to the test process; `pmset` confirms its assertions, recorded in
`caffeinate-runtime-probe.txt`. No persistent power setting, timeout or product code
changed. The unchanged probe passes **12/12 in 56 seconds**, with normal timings including
the previously stalled Machines paths, O27 mimic states and accessibility checks.
Stable full replay is now `s8-awake.log` / `browser-s8-awake/`, using the same
temporary process-bound guard (`caffeinate-s8-awake.txt`); its coverage remains
pending.

### Backend: original profile and separate supplemental evidence

Commands use the requested offline runner, separate labels and output directories:

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/live-view/backend/machine_health machine_health
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/live-view/backend/assets assets
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/live-view/backend/repair repair.test_health_diagnostics
```

| Original profile | O27 initial broad run | Untouched HEAD comparison | O27 final broad run |
|---|---|---|---|
| machine_health | 372 run; 25 errors; 1 skip | 371 run; same 25 errors; 1 skip | 373 run; same 25 errors; 1 skip |
| assets | 355 run; 147 errors; 1 failure; 7 skips | 351 run; 143 errors; same 1 failure; 7 skips | 355 run; 148 errors; same 1 failure; 7 skips |
| repair diagnostics | 12 run; 7 errors | 11 run; same 6 inherited errors | Covered by final supplemental methods below |

All **175 inherited failure/error signatures match exactly**. Four initial new
fixture validation errors were fixed, and the new occurrence case encountered the
same missing Ruleset-column limitation. These broad runs are not reported green.
Comparison hashes, exact signatures, runner and read-only HEAD snapshots are in
`backend/baseline-head/failure-comparison.json` and `snapshot-manifest.json`.
Only disposable container code copies were overlaid; no worktree source was reset.

Final original-profile reruns use the same command with output directories
`backend/final-machine_health` and `backend/final-assets`. All **169** baseline
signatures across those two labels still match exactly, with no changed or removed
baseline failure. Five extra error entries are new on/off test cases blocked by
the same missing `WorkOrder.recurrence_of_id`, Ruleset authority column and
`SignalDesignation` table. They pass in the separate supplemented fixture run.
The initial invalid registered-pump fixture errors are gone. Final hashes and exact
signatures are in `backend/final-failure-comparison.json`.

The existing legacy schema checkpoint omits runtime managed tables/columns on
SQLite. A separate, guarded **synthetic schema supplement** creates only documented
missing test structures inside `/evidence/test.sqlite3` in the network-none runner.
It runs unchanged original test classes/assertions: complete mimic/series/API
modules, original off plus new on fault-history cases, and four new assistant
methods. **103/103 pass**, including source failure/staleness, disabled poller,
source/display inverse conversion, O23 boundaries, immutable evidence, query count
and month timeout behavior. This is not migration/PostgreSQL/deployment acceptance.

Reproduction sources are `core_supplemental_tests.py` and `supplemental_tests.py`
under the evidence root; their manifest records hashes and limitations. Temporary
loader copies in backend source were removed after execution. Final output is
`backend/supplemental/backend.log` and `manifest.json`; unsuccessful checkpoints
remain in its timestamped history. Runner image remains cached
`89981388aff5f8329596f78ec9ce1bfb141ff9369e52fc7f9ae19305440fa96e`.

### Inspected renders

Inspected on/off Health, Performance and fault evidence captures on desktop and
phone under `browser-s3-o27-final-v2/` (earlier captures retained separately).
Verified 30-day control containment, neutral live presentation versus O23 caveats,
sampling/gap explanation, displayed case dates and unchanged retained evidence.
Neutral missing-reference and invalid-window alerts were also inspected on desktop and phone, plus a failed station source retaining unavailable status. Pump mimic captures have been inspected in all four desktop/phone light/dark combinations, and off-mode Monitoring source/deviation details on both widths. Station captures remain queued in final S0. Exact paths/hashes are in `inspected-renders.json`.

## Files and handoff

85 changed files at the last inventory: **21 backend, 59 frontend, 5 R6 tracking/
report files**. Exact paths are in `changed-files.json`. Main entry points:

- Backend: `aimms_flags.py`, `machine_health/services/display_time.py`,
  `summary.py`, `mimic.py`, `series.py`, `trends.py`, serializers/APIs,
  `assets/activation.py`, `machine_history.py`, `ai_read.py`, diagnostic readers/tools.
- Frontend: shared `telemetryPresentation.ts`, native mimic/Health/source-status
  components, sampled trend adapter/gaps, Performance time controls/comparisons,
  fault/monitoring/provenance components and API types.
- Tests retain O23 assertions under off; new cases cover on, failure states,
  display/source clocks, month windows and sampling fidelity.
- R6 handoff, S3 ledger, backlog and D03/D21 evidence track this bounded slice.
  Parent ticket statuses and deployment/product acceptance remain unchanged.

No Git write command, PR, issue, Azure connection, `data/azure-db.env` read,
other worktree edit or unrelated container/port operation was performed.
Owned runtime cleanup will be recorded after final browser execution.

## Fixed review findings

| ID / severity | User impact and cause | Correction and acceptance evidence |
|---|---|---|
| O27-V1 / P2 | Fault investigation showed plant-date inputs beside shifted observations; source links could double-shift | Display-date form plus inverse-shifted canonical links; desktop/phone on/off form and URL assertions pass |
| O27-V2 / P1 | A sampled month's last chart point could make a current source look stale | Reporting uses displayed source observation, with chart sample time separately identified; sampled-month browser case passes |
| O27-V3 / P1 | Missing month slots could be joined because plant cadence is unavailable | Sampled resolution drives gap threshold; unit verifies omitted slots break the line; inspected phone gap capture |
| O27-V4 / P2 | Off mode inherited new limits while metadata loaded; Health navigation limit was confused with its read limit | Signal policy used immediately; conservative Performance policy until extent arrives; 8 original O23 link/manual/dark cases pass |
| O27-V5 / P1 | Empty-month bucket scans could outlive the UI request | Shared 60-second source deadline; fake-container timeout test passes and response is unavailable |
| O27-V6 / P2 | Old poll times or resolved error codes could misstate current reporting; station-specific errors could be hidden | Explicit displayed-observation reporting and current read-failure facts; both-state backend assertions pass with real failures retained |
| O27-V7 / P1 | A current source observation could make an empty requested series look Live | Requested-series emptiness remains authoritative; unrelated current source metadata cannot supply a reading or a just-now claim; unit and desktop/phone browser assertions pass |
| O27-V8 / P2 | Linked-window refusal copy still disclosed the recorded clock and old limits on | Neutral on-mode refusal and 30-day limit; exact O23 copy off; 12 desktop/phone cases pass and verify no source read |
| O27-V9 / P1 | Availability was labelled as polling activity; a current source timestamp alone could supply a Live badge on an unavailable station | Actual scheduler state on, original effective-state presentation off; mode-aware activation/disabled copy; Live requires availability. Four scheduler-state units pass; pump mimic desktop/phone light/dark phases pass 4/4; station phases queued in final S0 |
