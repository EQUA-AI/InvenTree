# UI tables cleanup — 22 October 2026

Worktree `ui-tables`, branch `r6/ui-tables`, base `8a8335959`. Implemented and verified against isolated fixtures; ready for coordinator review and the coordinator’s full Mac suite run.

## Design contract and screens

Applied `masterdesignprompt.md`, `designprompt1.md` and the installed EQUA Mantine/InvenTree frontend skill. Preserve the native React/Mantine architecture, Details/header, routes, role boundaries, failed reads and owner O27’s telemetry switch. The owner’s final direction supersedes earlier summary-note requests: missing values have plain em dashes with accessible hover/focus tooltips; no new visible explanatory table notes or secondary captions.

| Screen | Primary user and task | Source and boundary |
|---|---|---|
| Machines dashboard | Operator/supervisor: locate/select machines and compare condition, alarms and Work | Native location-machine API and existing summary readers; no inferred operating state |
| Location workspace | Operator/supervisor: browse the physical hierarchy and scoped machine results | Existing location context, counts and placement permissions |
| Health | Operator/reliability engineer: compare current readings and investigate their evidence | Existing Health signals/summary, batch trends and selected history; no invented unit or limit |
| Station/pump Parts | Technician/storekeeper: identify a registry slot, catalogue family, fitted identity and provenance | Existing scoped registry, Work, stock and fit providers; a catalogue family never becomes a fitted item |

## Issue 1 — Machines and locations

Selection now shares the machine identity cell, vertically centred beside the name; phone selection sits beside the identity too. Columns have defined proportions, numeric counts align right, and operational states use compact native badges. Zero counts remain real zeros; unknown and missing facts retain their own accessible reasons. Failed reads remain visible. Removed repeated explanatory captions and zero-summary chips; optional location metadata is in the existing disclosure. Phone facts stack cleanly, controls have 44 px targets, long tooltips wrap within the viewport, and checkbox/placeholder/text contrast is checked in both schemes.

Changed: `LocationWorkspace.tsx`, `MachinePageSummary.tsx`, `locations.module.css`, affected native/S1/S8 assertions and fixture capability state.

## Issue 2 — Health signals and header

Values align right with their supplied units. Missing values or units show an em dash; the unit-missing tooltip retains the actual raw value for traceability. Unknown state text and the duplicate assessment prose are replaced by a tooltip dash. One muted **No limits set** chip occupies the Limits column. Mapping/engineering-approval information is in the limits tooltip. Tags and component metadata remain available through the native row disclosure. Observations, quality and trends align; phone facts use two-column pairs and 44 px focus targets. Removed unavailable backend-assessment and general table/anomaly-count captions. Explicit read failures, stale/last-known values, critical coverage warnings and alarm actions remain.

Tooltip clicks preserve the selected investigation/chart context. Keyboard tests use actual Tab navigation through the new explanation targets; assertions still prove the exact missing-unit reason and each failed-history reason.

Changed: `HealthSummary.tsx`, `MachineHealthPanel.tsx`, `SignalTable.tsx` / CSS, `SignalReading.tsx`, `readingPresentation.ts`, focused units and Health assertions.

## Issue 3 — Station and pump Parts

The component API retains raw `provenance` and adds read-only `provenance_display` and `provenance_imported_at`. Exact workbook-import provenance displays as **Equipment register import** with the component’s stored original creation/import timestamp; importer replay tests prove that later replays preserve it. SCADA inference displays as **SCADA tag mapping**, with no borrowed date. Manual/missing/other sources do not acquire an import timestamp. Dates are formatted from the supplied timestamp in UTC; no hash, filesystem date or review time is used to invent one. Raw import provenance remains in the source tooltip.

Repeated missing model/fitted/stock/work descriptions become accessible tooltip dashes; known zero Work/stock remains zero. Draft is one compact chip with its assignment explanation in a focus/hover tooltip. Removed the decorative logical-group chip and the table’s explanatory caption. Widths and wrapping are explicit, complete headings remain readable, and phone rows stack with full-width identity/catalogue/source facts. Links and Draft/code text pass rendered contrast checks; native filters, pagination, slot detail, return navigation, fit recording and permission boundaries are preserved.

Changed: registry serializer and provenance/importer tests; frontend component types, `InstalledPartsPanel`, `MachineComponentTable`, `ComponentFacts`, `ComponentSlotDetail`, `componentPresentation` and affected Parts tests.

## Browser verification

All renders use **Linux Chromium** in the task-owned Docker browser `r6-ui-tables-browser-3011`, WS **3011**, fixture harness **15246**. These are synthetic native-component fixtures, not a real login, live plant-data, deployment or product-acceptance run.

The owner’s follow-up narrowed verification to specs covering these screens; the coordinator runs the full sprint suites on the Mac. Final passing-case inventory: `data/devscratch/ui-tables/focused-passing-cases.json` (**195 distinct focused cases**):

| Coverage | Verified cases | Evidence |
|---|---:|---|
| Native location workspace, including its explicit phone cases | 19 | `s0-focused.log` desktop results; `s0-phone.log` final 2/2 phone-layout replay |
| Machines explorer desktop/phone | 30 | `s1-focused.log`, `s1-corrective.log`, final `s1-phone-tooltip.log` |
| Parts desktop/phone, both schemes, access/fit/navigation | 22 | `parts-browser.log` 22/22; final `s2-corrective.log` 4/4 contrast/stack/keyboard replay |
| Health condition, inspection, investigation, native forms and trends desktop/phone | 108 | `s3-corrective.log` 103 pass; `s3-keyboard.log` 2/2; `health-keyboard-final.log` 4/4, with one repeated passing case |
| Affected S8 summary, controls and viewer Parts permissions | 16 | `s8-tables.log` 16/16 |

No skipped tests, weakened assertions or extra timeout budget were added. Intermediate failures and traces are retained. `verification-scope.md` records the partial full-suite attempt, the accidental non-native S0 phone projection, stale-caption fixes, native keyboard-modality/tab-order fixes, phone tooltip containment repair and stale stylesheet detection. The original full S0 attempt stopped after 46 passing cases and is **not** recorded as a completed suite.

The explicit `AIMMS_SOURCE_REVISION` fixture refresh now includes CSS as well as TS/JS. A stale stylesheet from the interrupted harness initially hid the new palette. Fresh-query bytes, final contrast assertions and final captures verify the implemented styles; earlier captures are not relabelled as final evidence.

## Before/after captures and visual review

`data/devscratch/ui-tables/screenshots.json` indexes **20 before/after variants**, their SHA-256 hashes and actual dimensions: desktop **1440×900**, phone **390×844**, light/dark for all five screens. Full-page after images are also retained. Before and after images were inspected, including an independent agent review of all 20 final variants and the phone full pages. Final review found no remaining blocker in the changed UI. Health charts/history are settled, and the earlier sticky-header capture-position artifact is resolved. This is an agent review under O12/O26, not a human audit or product acceptance.

| Screen | Before directory | Final after directory |
|---|---|---|
| Machines | `capture-before-selection/capture-machines-{light,dark}-{desktop,phone}/test-finished-1.png` | `capture-after-complete/capture-machines-{light,dark}-{desktop,phone}/machines-{scheme}-{project}.png` |
| Location | `capture-before-selection/capture-location-{light,dark}-{desktop,phone}/test-finished-1.png` | `capture-after-complete/capture-location-{light,dark}-{desktop,phone}/location-{scheme}-{project}.png` |
| Health | `capture-before/capture-health-{light,dark}-{desktop,phone}/test-finished-1.png` | `capture-after-complete/capture-health-{light,dark}-{desktop,phone}/health-{scheme}-{project}.png` |
| Station Parts | `capture-before/capture-station-parts-{light,dark}-{desktop,phone}/test-finished-1.png` | `capture-after-complete/capture-station-parts-{light,dark}-{desktop,phone}/station-parts-{scheme}-{project}.png` |
| Pump Parts | `capture-before/capture-pump-parts-{light,dark}-{desktop,phone}/test-finished-1.png` | `capture-after-complete/capture-pump-parts-{light,dark}-{desktop,phone}/pump-parts-{scheme}-{project}.png` |

All directory paths in this table are under `data/devscratch/ui-tables/`; append `-full` before `.png` for final full-page captures. Capture run: `capture-after-complete.log`, **20/20**.

## Engineering checks and backend limits

- `tsc --noEmit -p` passes for `tsconfig.json` and every `tsconfig.s*.json` (nine projects); `types-final-results.json` and corresponding logs.
- Changed formatter units: **8/8** in two files; `changed-units-final.log`. Health’s related focused units previously passed **24/24**; an earlier full Vitest run passed **948/948** (`vitest.log`).
- Lingui extraction and compilation pass; **117 locale files restored byte for byte**, `lingui-results.json`. Later changes introduce no new UI strings.
- Changed-file prek passes on all 37 changed/new files (`prek-complete.log`); `git diff --check` and tracking pass (58 work items, 95 screen dispositions, 26 contracts). Final app typecheck after the last keyboard corrections also passes (`types-complete-app.log`).
- Requested raw offline `assets` runner: **358 tests, 1 failure, 151 errors, 7 inherited skips**. Read-only HEAD comparison: **355 tests, 1 failure, 148 errors, 7 skips**; all **149 inherited failure/error signatures match**, with zero changed/removed inherited signatures. The three new provenance methods encounter the same missing RuleSet-column setup error in the raw profile. `backend-failure-comparison.json` retains exact signatures and log hashes.
- Original affected backend modules `assets.tests.test_registry_parts_context assets.tests.test_population assets.tests.test_registry` pass **85/85, zero skips**, using an explicit guarded scratch-only SQLite schema supplement for the runner’s documented legacy-boundary omissions. `backend-supplemented/` contains final evidence and preserved failed intermediate attempts; reproduction scripts and source hashes are under `backend-verification/`. No tracked schema/runner workaround or unrelated product fix was introduced. This is fixture verification, not a whole-backend/PostgreSQL/migration/deployment pass.
- The optional final raw-profile repetition was interrupted during setup; it is not counted. Native authenticated `pui_machine_installed_parts.spec.ts` wording was updated and typechecked; its app/login run remains with the coordinator.

## Handoff and runtime

No Git write commands, issue/PR, Azure connection, `data/azure-db.env` access or other worktree/container modifications. Parent backlog statuses are audited and unchanged; contracts D01/D02/D03/D08 and the S8 ledger record this bounded follow-up. Full Mac sprint runs and deployment/product acceptance remain with the coordinator.

Runtime cleanup result is recorded in the final section below; the interrupted harness may require host cleanup because the sandbox denies signals to its prior-session PID.

## Final runtime and evidence checkpoint

The owned browser container is stopped/removed and WS port3011 is clear. The harness left by the interrupted prior-session full run still listens on15246 as **PID80523**. Direct `kill -TERM80523` was denied by the sandbox (`operation not permitted`); the prior execution session no longer exists for stdin cleanup. The coordinator must stop that host PID after reviewing/reusing the evidence. No other listener or container was stopped. Temporary Vite-config cleanup probes were restored byte for byte; `vite.s0.config.ts` has no diff.

Final evidence: 195 distinct focused passing browser cases,20/20 reviewed capture variants, changed units8/8, nine TypeScript projects, final app typecheck, bytewise Lingui restore, all changed-file hooks, tracking and whitespace pass. Backend supplemental85/85 and original-profile limits remain separate. Full Mac suites and native authenticated app replay remain with the coordinator. There are no remaining product implementation or changed-UI visual blockers in this bounded fixture-verified slice; host harness cleanup remains explicitly outstanding.
