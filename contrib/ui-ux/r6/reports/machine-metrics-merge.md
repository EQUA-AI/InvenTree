# Machine metrics merge

23 October 2026. Source integration of Lowkey-wizard’s `d26cce7bd` into AIMMS
`537b6f4de` in `mm/merge-trial`. Read the commit, all three `UiUpgrades/` handoff
records, R6 `ui-r2.md` follow-ups 2–12 and R8 `polish.md` before resolving.
No Git write command, product migration, deployment or owner-data change.
The merge index remains with the coordinator; resolved working files do not
constitute a staged or completed Git merge.

## Execution ledger

- Applied `masterdesignprompt.md`, `designprompt1.md`, installed EQUA Mantine/InvenTree,
  industrial telemetry, browser-testing and visual-QA skills. Native React/Mantine,
  TanStack queries, shared missing-value controls, existing routes and permissions remain.
- Parallel integration and independent review covered backend authorization, location
  explorer, Health/metrics/Board and R8 Work. Each owner reported precise checks.
- Working-source integration and requested checks complete; ready for coordinator/human review with inherited gates below.
  Evidence root: `data/devscratch/mm-merge/`. Owned browser `r6-mm-browser-3026`,
  Playwright WS3026, fixture harness15276. Both ports now clear and owned browser removed (`cleanup.json`). Fixture API responses only, no real login.

## Every conflicted file

Paths are repository relative. All 15 working files have had conflict markers removed.

| File | Resolution |
| --- | --- |
| `src/backend/InvenTree/aichat/tests/test_rollback_floor.py` | Incoming generic read-only-command docstring; retained our cold-cache/absent-marker assertion that no setting row or SQL write is produced. |
| `src/backend/InvenTree/machine_health/api.py` | Retained request-aware machine authorization before nested reads and our exact snapshot UUID lookup, including malformed/missing citation 404. Result matches premerge HEAD bytewise because our line already contained the incoming hardening. |
| `src/backend/tasks/scope.py` | Retained incoming fail-closed `require_machine_scope` delegation plus our O24 `work_machine_authorized` boundary; sharing Work does not grant machine authority. Result matches HEAD bytewise. |
| `src/frontend/playwright.chat.config.ts` | Kept ours independent chat harness contract; resolved comment-only conflict. No chat behavior change. |
| `src/frontend/playwright/aichat-drawer-lingui.ts` | Kept ours early locale activation and existing imports; resolved comment-only conflict. |
| `src/frontend/playwright/machine-locations.tsx` | Shared incoming `test-lingui` activation, our native theme/provider, route and list fixture behavior. |
| `src/frontend/src/pages/assets/health/MachineHealthPanel.tsx` | Our compact condition/coverage presentation, shared clock, O27 freshness, keyboard/evidence pipeline and native sections; incoming explicit initial load and independent summary/signals/anomalies error/retry through `HealthQuerySection`. Summary errors do not expose cached condition; pending signals mean unknown coverage rather than failed readings. |
| `src/frontend/src/pages/assets/health/SignalTrend.tsx` | Incoming geometry with our prepared full source window, quality/cadence gaps, display clock and server plot-value mappings. Dots and centered flat signals survive; source, units, range and quality remain accessible. Our quiet missing/failed presentation and keyboard reach remain. |
| `src/frontend/src/pages/assets/locations/LocationWorkspace.tsx` | Our complete root machine table, full API site/unassigned counts, archived/inactive filters, compact/full paths, selected descendants, station scope, stable zero-transition phone Filters. Added incoming production metrics behind a keyboard disclosure in existing controls; metrics receives the same inactive filter and refresh invalidation. Phone disclosure shares the scope row to preserve first-card fold. |
| `src/frontend/src/pages/assets/locations/MachineLocationCard.tsx` | Our compact placement styling and real placement/move permission controls; incoming production-only direct/source navigation with no session propagation. |
| `src/frontend/src/pages/assets/locations/locationTree.test.ts` | Union of root/list-state/path/return regressions and incoming production-scope expectations, including safe location IDs and no session parameters. |
| `src/frontend/src/pages/assets/locations/locationTree.ts` | Our safe-integer location parsing, all root/list/empty-state/return helpers; incoming production breadcrumb semantics. No obsolete session field. |
| `src/frontend/src/pages/maintenance/MaintenanceBoard.tsx` | Incoming complete location population filtering, `BoardTaskCard`, filters/density/gesture behavior; our board/task/work-order identity and native modal AI action. Removed instructional heading prose and replaced visible numeric location ID with a scoped Back to location link and concise scope label. |
| `src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx` | Our entire seven-tab R8 workspace, procurement/fallback wiring, Closeout locks, retained drafts, strict numeric Work ID and responsive focus remain. Incoming shared scope helpers now preserve location/direct scope in error links and direct-entry Board fallback; normal history Back remains. Panel changes preserve query scope. Independently reviewed against ours. |
| `src/frontend/tests/pages/pui_asset_locations.spec.ts` | Both suites’ navigation/count/read-failure/legacy URL contracts; disambiguated inventory versus metrics reads, retained root server-count cases and added keyboard metrics disclosure with active/inactive production reads. |

## Incoming UI adjustments and additional consistency

`MachineMetricsPanel`, `useMachineMetrics`, `machineMetrics`, `MachineHistoryPanel`,
`BoardTaskCard`, `HealthQuerySection`, `trendGeometry`, dashboard `MaintenanceWidget`
and `maintenanceMetricsRead` remain part of the integration. There is no new metrics
backend, session selector, synthetic history, provisioning or schema.

- Removed provenance/developer captions and repeated descriptions. History remains
  visibly unavailable; its explanation is in the accessible information control,
  its period control is disabled, and values are dashes with reasons. Metric,
  lifecycle, due and missing allocation facts use native missing-value controls;
  absent allocation is never invented as zero. Normal with unknown coverage never
  claims full assessment. The contributor table uses our existing `WorkTableScroll`
  so an overflowing phone viewport is a named keyboard stop; Tab and ArrowRight
  are verified rather than relying on pointer-only horizontal scrolling. Supported recorded-downtime/dashboard contracts and IDs remain.
- Trend source/window/unit/quality detail is in the chart’s accessible name and tooltip,
  preserving measured data, server display mappings and our cadence-gap contract.
- Independent scope review found and fixed three incoming issues: a health503 masking
  work403 cleanup; real object-level health404 not latching denial; and contributor
  selection surviving removal from refreshed location population. Separate cleanup hooks,
  health-object404 handling and current-population validation now cancel/purge denied
  payloads and reset departed-machine offsets. New browser cases exercise all three,
  including explicit recovery and absence of refetch loops. Collection404 remains distinct.
- Fixed the stale `voice-deferred-focus.tsx` locale import to `test-lingui`; added new
  metrics fixture/config/spec to `tsconfig.playwright.json`. Node config mirrors the app’s
  `skipLibCheck` for optional bundler peer declarations, retaining source checking;
  built its declaration prerequisite for referenced legacy fixture projects.
- The broad unit run exposed an inherited stale `FreshnessLabel` expectation (“old”
  versus the existing “ago” presentation). Test and source both matched HEAD before
  the fix. Updated the two literal expectations, retaining fresh/stale/missing semantics.
- Incoming Health fixture omissions were completed with required typed summary/signal/
  trend fields; phone trend checks open the native Details control by keyboard. Product
  guards and the authoritative zero-versus-unknown distinction were preserved.

## Verification

Frontend verification:

| Check | Actual result and evidence |
| --- | --- |
| TypeScript, all frontend projects | All 13 pass: app, lib, node, legacy Playwright, R8 and S0/S1/S2/S3/S4/S6/S7/S8. `types.json`, `types-*.log`; node declaration prerequisite built. Initial missing optional bundler peers and legacy missing locale import are resolved. |
| Vitest, full frontend pure-module suite | 1101/1101 pass across 116 files, `vitest-final.log`. Initial inherited age-literal failure retained in `vitest-all.log`. |
| Lingui | Extract/compile pass; all 79 `.po` files restored bytewise and match initial hashes (`lingui.json`, `po-before.json`). This records the actual `.po` count in this checkout. |
| Locations | 20/20 pass (`locations/browser.log`), plus final keyboard disclosure/active-inactive population replay 1/1. |
| S1 explorer and Work panels | Broad51/52; one cold Search helper timeout before Work assertions. Exact unchanged failed desktop Work-count case passed on fresh warm server (`locations/browser-s1-workcount-verified.log`); phone counterpart and every other case passed broadly. |
| S8 owner | 16/16 pass (`locations/browser-owner.log`). |
| S8 wider-font phone fold | Final 2/2 pass unchanged; first-card bottom 808.08px within 844px, light/dark screenshots inspected. Initial wrapping defect and stale-transform rerun failures retained separately. |
| Maintenance widgets/read recovery | 20/20 pass, desktop+Pixel 7 (`browser-maintenance.log`). |
| Machine metrics, final restored source | 55 passed, 9 explicit duplicate capture skips (`browser-metrics-release.log`). All original 36 cases, three scope regressions on both projects, keyboard horizontal scrolling, direct-entry R8 tab/scope return and nine captures pass. Prior clean 53-case run retained separately. |
| Full R8 | 148 passed, 7 intentional duplicate dark-phone polish skips, exit0 (`browser-r8.log`, `r8-report.md`). Scope recovery, procurement, source refusal and viewer controls retained. |
| S8 follow-ups/health states/permission matrix | Broad 269/276, then 18/18 unchanged proper-timezone/warm replays covering all 7 failures plus neighboring clock contexts. Six container Los Angeles versus Riyadh assertion assumptions; one optimizer reload reset model selection. `locations/browser-s8-clock-verified.log` and classification JSON; no tracked S8 assertion edited. |
| Changed source/document prek | Clean source/document run after initial formatter changes; generated compiler artifacts excluded and removed before handoff. Final file inventory/log: `changed-files.json`, `prek-final.log`. |
| Tracking validator | Inherited `Ticket coverage differs`: 60 rows versus validator’s older inventory. Current and HEAD ticket IDs match and validator matches HEAD (`tracking-baseline.json`). Parent statuses remain unchanged. |

No intermediate failure is counted as a clean final run. The three new scope
regressions were also checked against narrowly restored old behaviors: all 3 failed for the intended reason (9 retained cached payloads in both
denial cases, explicit departed Pump 5 retained instead of Pump 6). No unrelated
behavior was reverted. `security-regressions/mutation-plan-final.diff`,
`browser-red.log` and `red-result.json` record the run. A guarded `finally` restored
both source files bytewise; `restored-status.json` confirms matching SHA-256 and
inactive mutation state. The final restored-source run passes 55 applicable cases,
including all three fixed regressions on desktop and Pixel 7 and the new
keyboard-scroll case on both projects.

Backend detail and exact commands: `data/devscratch/mm-merge/backend/report.md`.
The requested canonical runner covered `machine_health`, seven tasks scope/dashboard
modules and `aichat.tests.test_rollback_floor` (nine labels total).

| Backend check | Actual result |
| --- | --- |
| Requested stock offline profile | 570 discovered; 484 ran, 98 errors, 1 skip. All errors classify into inherited checkpoint missing-schema categories; some classes could not initialize. |
| Supplemental retained real ORM migrations | 570 total: 566 passed, 3 errors, 1 skipped. Fresh network-disabled read-only container; scratch settings only excludes `legacy_boundary`, as documented in R6 follow-up12. No synthetic schema or permission mocks. |
| Scope/dashboard/rollback subset | All 100 selected tasks cases and 8 rollback-floor cases pass. Health API, snapshots, denied scope, signals/evidence and trends pass in the ORM profile. |
| Estate errors against premerge HEAD | Same 3 naive/aware datetime errors reproduced on read-only archived HEAD with matching testcase names, complete chained exceptions and source-frame signatures; `estate-baseline-comparison.json`. |
| `makemigrations --check --dry-run` | Exit 1: inherited drift in aichat/users/assets/repair/tasks, also documented in R8 polish. No generated migration or changed model/migration. |
| Backend configured hooks | All applicable checks pass for the 3 backend conflicted files. |

Security policy and threat model were read. Authorization remains authenticated API
scope enforcement; frontend locking cannot grant machine, business-system or OT
authority. This report does not claim a production vulnerability or deployment result.

## Captures and open items

Requested Metrics/Board/machine desktop-light, desktop-dark and 390×844 phone matrix:
all 9 captured and individually inspected by the root verifier. Re-captured from final
restored source after adding keyboard scrolling; all 9 are SHA-256-identical to the
inspected images (`capture-review-final.json`). Final files are under
`data/devscratch/mm-merge/browser-metrics-release/`, with these case folders:

- `pui_machine_metrics-merge-capture-panel-{desktop-light,desktop-dark,phone}-desktop/`
- `pui_machine_metrics-merge-capture-board-{desktop-light,desktop-dark,phone}-desktop/`
- `pui_machine_metrics-merge-capture-machine-{desktop-light,desktop-dark,phone}-desktop/`

The review checked aligned compact metrics, explicit unavailable history/dashes,
readable dark text, bounded contributor scrolling, complete Board controls/cards,
phone wrapping and unchanged native machine header/Details. No overlap or page-wide
overflow found. No inferred equipment value, history chart or source caption added.
The phone contributor table now has a named overflowing keyboard stop; Tab and
ArrowRight move its viewport. Owner/fold/Health/R8 supporting captures were also
inspected by their delegated reviewers; primary 9 is not inflated by those images.

Browser execution reproduced through scratch wrappers importing the requested
checked-in configs: metrics, locations, maintenance, S1, S8 and R8. Each uses
`baseURL=http://127.0.0.1:15276` and Playwright `connectOptions` to
`ws://127.0.0.1:3026/mm-merge` with loopback exposure. The normal backend proxy is
disabled by the existing native fixture server (`playwright/r8-server.mjs`, no
bundle override). S8 clock replays explicitly use Asia/Riyadh, as its exact
assertions require. Metrics uses the requested machine-metrics config through its
wrapper, preserving both desktop and Pixel 7 projects.

Reproduction: from `src/frontend`, build node declaration prerequisites with
`node node_modules/typescript/bin/tsc -b tsconfig.node.json --emitDeclarationOnly`.
Run `python3 data/devscratch/mm-merge/typecheck.py` from the repository root.
From `src/frontend`, pure units use
`node node_modules/vitest/vitest.mjs run --configLoader runner`. Browser commands
run from `src/frontend`, e.g.
`node node_modules/@playwright/test/cli.js test --config ../../data/devscratch/mm-merge/metrics.config.mts --workers=2`.
The final R8 run uses its equivalent `r8.config.mts` wrapper with workers 1; exact
S8 replay inventory/log is in `locations/`. Compiler declaration/build-info
artifacts were removed after successful checks (`compiler-cleanup.json`).

Cleanup confirms owned browser removed, ports 3026/15276 clear, `.po` hashes all 79
match the initial bytes, and Git index/HEAD/MERGE_HEAD all match the starting
hashes. No Git write command or locale content change occurred. All 15 index
conflicts deliberately remain unmerged for the coordinator to review and stage.

Browser tests use actual components with
intercepted API responses; no authenticated backend integration, Mac-host layout,
PostgreSQL behavior, live telemetry, deployment or human/product acceptance is inferred.

Open inherited gates: stock SQLite checkpoint schema, three estate timezone cases,
model/migration drift and the tracking validator’s older ticket inventory. Human review and coordinator
integration/deployment acceptance remain separate. No PR or issue is created.

Final integrity: `data/devscratch/mm-merge/final-integrity.json` confirms no working
conflict markers, unchanged Git state and source digest `5f97e2144337e6ce`.
Cleanup evidence: `data/devscratch/mm-merge/cleanup.json`.


## Review fixes

Independent read-only review identified three incoming behaviors lost during the
merge. This bounded follow-up restores them; no Git writes or backend changes.
Verification completed under `data/devscratch/mm-merge/review-fixes/`; results below.

1. **Independent Health reads:** summary loading/error is now confined to Current
   condition. Successful anomalies, signals, trends, past alarms and retained
   evidence remain mounted. Summary announcements, source lookup/table and live
   presentation require a successful summary; cached failed summary data stays
   suppressed. Regressions cover initial summary503 with successful sections,
   independent signals/anomalies failures and separate retries, failed refresh
   with cached condition, and pending summary with successful independent reads.
2. **Allocation facts:** missing allocation renders an inline dash followed by the
   known required quantity. Its hover/focus explanation retains both “Allocated
   quantity not supplied” and any supplied allocation note. The non-null branch
   remains unchanged; the regression includes both missing `/7` and supplied
   `2/4` controls with their own explanations.
3. **Ownership:** both card densities keep an unconditional assignee slot. Blank
   assignee is a dash with the keyboard/hover explanation “Assignee: Unassigned”.
   No ownership or allocated amount is invented.

Changed product files are `src/pages/assets/health/MachineHealthPanel.tsx` and
`src/pages/maintenance/components/BoardTaskCard.tsx` under `src/frontend/`.
Regressions are in `tests/pages/pui_machine_metrics.spec.ts`. The native Board
navigation checks in `tests/s0/pui_native_work.spec.ts` now target the incoming
primary link rather than the retired button, retaining exact parent Work ID,
URL, overview-read and heading assertions. No unrelated Work behavior changed.

| Affected check | Actual result | Evidence under `review-fixes/` |
| --- | --- | --- |
| TypeScript | All 13 frontend projects pass; final S0/legacy Playwright checks repeated after native locator/capture edits | `types.json`, `types-*.log`, `types-specs-final.log` |
| Vitest | 216 passed across 21 Health/tooltip/metrics/work-list modules | `vitest.log` |
| Full machine-metrics suite | 69 passed, 9 intentional duplicate capture skips, zero failures | `browser-metrics-release.log` |
| New Board keyboard/hover regressions | 4 passed across desktop/Pixel 7, plus 2 settled allocation-capture replays | `browser-metrics-board-final.log`, `browser-metrics-board-capture.log` |
| Health condition/native | 42 passed, desktop/phone and light/dark | `browser-health.log` |
| Health stale/missing/bad-quality/failed states | 4 passed | `browser-health-states.log` |
| Native Board parent navigation | 6 passed, desktop/phone | `browser-board-verified.log` |
| Lingui | Extract and compile pass; all 79 `.po` files restored bytewise and matching initial hashes | `lingui.json` |
| Changed-file hooks | All applicable checks pass after formatting | `prek-final.log` |

Initial new Board tests used programmatic focus immediately after hover; they
now prove actual Tab/Shift+Tab focus and select the exact explanation while the
neighboring tooltip completes its normal close transition. The initial 65/69
applicable metrics result and focused corrections remain recorded, followed by
the clean full 69-case run. No product behavior was weakened for those tests.
The native Board attempt stopped at old button selectors before its updated
six-case replay. The Health runner’s initial browser executable-path setup
failure is retained separately; its actual UI run passes all 42 cases.

The root verifier individually inspected the summary-503 desktop/phone captures:
short summary failure + Retry, successful anomaly/actions, signal, trend/evidence,
resolved alarm history and snapshots remain, with cached summary/source data
suppressed. Delegated reviewers inspected nine final Metrics/Board/machine
light/dark/phone images and four focused ownership/allocation images. Dash `/7`,
provided note, known `2/4` and Unassigned explanation remain readable and
keyboard-reachable without clipping. Image paths and SHA-256 are recorded in
`metrics-matrix-inspected-sha256.txt` and `board-inspected-sha256.txt`.

All browser checks use the same fixture-only WS3026/harness15276 setup as the
original merge; no backend, database, migration, deployment or product acceptance
is inferred. The original merge evidence above remains historical, including its
inherited backend/migration/tracking gates. Current follow-up source hashes,
Git/locale integrity and runtime cleanup are in `final-integrity.json` and
`cleanup.json` in this follow-up evidence directory.
