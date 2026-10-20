# S8-01 part 2 — pump Health and Performance

Status: **built and tested — six O12 review findings corrected with regression coverage**. O16 design pass within the R6 scope. No deployment, product acceptance, independent human audit or O22 score is claimed. All **65 changed/new files** (64 frontend + this report) remain uncommitted in `r6/design-pass-2`, with no staged files. The final source/path audit confirms unchanged frontend hashes and no prohibited paths.

## Built

**Health:** compact assessment/coverage workspace; current signals precede past records. The default signal table uses compact scan rows at engineering widths as well as phone width, with per-row details and the retained all-column table. All ten native fields and the original batch sparklines/full blue trend remain. Values withheld by the API stay unavailable; stale last-known values are explicitly labelled. Engineer-designated critical bindings and review requirements appear in the row, while designated coverage keeps missing, retired and unassessed evidence unknown. Source, units and configured bound directions remain accessible in row details and signal evidence. Assessment and observation times remain distinct; no assessment time is invented.

**Performance:** channel search and section navigation precede the current evidence strip and native chart groups, keeping search in the first phone viewport. Phone search/section fields stack. Section buttons expose their pressed state. Existing 200-target batches, the complete channel inventory, retained chart controls, window limits and quality/gap adapters are preserved. Configured directional warning/critical boundaries produce shaded excursions on native family charts only when the existing family logic supplies a shared limit. The key names units, Health binding mapping provenance and unavailable engineering approval; no OEM or plant-approved limit is invented. Compatible engineering roles/units still overlay; incompatible selections get separate synchronized charts and an explicit explanation. Comparison controls precede saved-view details. Personal and shared display choices keep their existing storage, permissions, ownership and versioned commands. A missing winding-family KPI now explicitly says “Unavailable · no usable observation”; the new UI assertion failed before this correction and passes after it.

**Shared presentation:** monitoring-local contrast classes, one badge wrapper with icon/text and explicit alarm state vocabulary, browser-clock formatting and one clock declaration per pump panel. The Station consumer retains its existing clock declaration while pump/comparison windows use the panel declaration. Stale/unknown/disabled states stay neutral; absence of a freshness budget cannot produce a green Live indicator. Source tables preserve exact freshness seconds. Full instants use a 24-hour date format; invalid dates are unavailable. Native theme defaults, theme switching, architecture and dependencies are preserved.

## Process and boundaries

Audits and operator tasks: `data/devscratch/r6-s8/S8-01-part2/{health,performance}/audit.md`. Initial captures, intermediate failures and refinements are retained. Render inspection corrected density, selected-row link contrast, amber title contrast, descriptions, input/switch borders and scatter/area chart labels.

S8 renders use the **production ThemeContext**, including its actual InvenTree colour-scheme manager, primary colour, radius and breakpoints. Health mounts the native MachineDetail/PanelGroup; Performance mounts the native PerformancePanel in its fixture router. Compact fixture diagnostics are separate from product chrome. The fixture-only LanguageContext global reads are intercepted explicitly. This is the native-component harness path, not an authenticated deployed shell walkthrough. Existing S3 tests retain their original theme path unless they explicitly request `nativeTheme`.

Renders come from **Linux Chromium in the task-owned cached-image Docker container `r6-design2-browser-15234`**, browser ws port 15234, harness port 15224; the container was stopped after verification (exit 0; no matching running container remains). No browser, package or image download. No backend change, backend test, database connection or Azure write. No shared ledger, MachineDetail/header, Machines explorer, Equipment view or Fault History screen implementation was edited. Shared `health/common.tsx`, badge/clock presentation, charts, read provenance and harness changes need reconciliation with part 1 by the coordinator; the complete file list below names them. Native host tab clipping on phones and host title contrast remain outside this panel work.

## Review findings and dispositions

Agent review under owner delegation O12 confirmed the following six findings. This records an agent review and implementation response, not independent human review or O22 approval.

| Finding | Root correction | Regression coverage |
| --- | --- | --- |
| 1. Shifted anomaly times lacked provenance | A shared `AnomalyTimeShift` row label reuses `faults/occurrenceLabels.shiftSpan` in both AnomalyList and AlarmHistory. Signed offsets remain distinct from timezone formatting; missing amounts stay unavailable. The Health clock declaration explicitly excludes separately labelled shifted anomaly times. | S8 shifted-alarms state supplies shifted active and resolved rows, requires both labels and the qualified header, and captures every width/scheme. |
| 2. Permission checks matched no row-specific names | Health selectors use a word-boundary action prefix. Populated viewer checks require at least four controls; refusal checks require at least one before checking disabled state. | Full S8 permission matrix plus a dedicated native-theme viewer check requiring all four row actions to be disabled. |
| 3. Vibration fallback lost its reason and misdescribed unreviewed tags | Tile and section use “Unavailable · no vibration channel with a confirmed role and unit”. The section explains the confirmed-role/unit requirement without claiming that unbound tags appear in Other. | Legacy Performance suite retains exact count/tile, heatmap, axis, classification and zoom checks; checks the section fallback reason in its retained DOM (absent families stay hidden from navigation). Its older component harness now supplies native version constants and consistent fixture API/query exports at the task port. Runtime-error assertions live in the Node test spec, outside browser-shared fixtures. |
| 4. Fresh Live badge contrast was 4.368:1 | Filled green uses black foreground on the native green.9 background, preserving the native palette. | New fresh-live matrix state requires the actual Live badge and the existing 4.5:1 text contrast sweep at all widths/schemes. The added state failed in both themes before correction. |
| 5. Scope CSS recolored disabled actions and Alert titles | Deleted component-wide scope selectors. Links/primary subtle actions opt into `.link`, which excludes disabled controls; gray actions keep Mantine styles. Dark selectors use `:global([data-mantine-color-scheme='dark']) .x`. Only measured warning coverage titles opt into a local warning-color class. | Native-theme tests compare disabled Dismiss and Alert-title computed styles inside/outside the scope, require four real disabled row actions, and capture viewer/custom-range states. The contrast reader handles normalized CSS `color(srgb)` values. |
| 6. Modal and secondary timestamps diverged | Expanded-trend caption declares the shared browser clock. Preliminary generation and custom history extent use `displayInstant`, including year/seconds and unavailable invalid timestamps. | S3 asserts the clock inside the expanded dialog and the exact generated instant; S8 asserts the full history extent format. Modal/generation captures are retained. |

An existing S3 expansion assertion compared the modal caption with the inline caption. It now requires the exact same selected window and source with the requested modal clock declaration inserted; inline caption, selection, focus return, chart styling and request-count assertions remain.

The follow-up also found stale Parts test wording in the integrated permission suite. The test now checks the existing Draft state, “Slot assignment not verified” and the fixture's exact unverified-assignment provenance, retaining its stock/write/link assertions. No Parts implementation changed. Mimic permission captures center the action cell in both scroll axes before requiring all three controls to be fully in the phone viewport; permission and visibility expectations remain unchanged.

Capture refinement exposed custom-date placeholder contrast and an unnamed expanded-trend close button. These now use an explicit local metadata class and a named native Modal close button. The contrast helper reads normalized CSS `color(srgb)` correctly; its Switch border check compares the outer boundary with the surrounding panel, rather than the checked fill. Text remains ≥4.5:1 and interactive boundaries ≥3:1; no threshold was reduced or failed element excluded.

## Verification

Follow-up checks on the corrected production source: **9/9 TypeScript projects** (default plus every existing `tsconfig.s*.json`), **218/218 asset unit tests in 21 files**, **34/34 S8 design/state/review cases**, and **8/8 legacy Performance cases**. The final capture-only cleanup replay also passed **4/4** S8 review cases. The complete **280/280 S3 replay passed** (140 desktop, 140 phone; 22.1 minutes), with no failures/retries. The complete **78/78 S8 permission/guard replay passed** (39 desktop, 39 phone; 4.6 minutes). Host hooks pass (**6 passed / 12 skipped / 0 failed**) and `git diff --check` passes. Exact commands, intermediate failures and retained logs: `data/devscratch/r6-s8/S8-01-part2/review/commands.md`; original design-pass history: `validation-commands.md`.

All **136 panel measurements** pass: zero page overflows, exactly one panel clock declaration, minimum measured text contrast **4.58:1**, and minimum input/switch border contrast **3.32:1**. Fresh Live measures **4.81:1**. This sweep covers the monitoring panels and excludes disabled controls and native host chrome; it is not a complete accessibility audit.

The regression red runs and failed refinements remain in the command journal with their actual counts, including the stopped S3 caption replay and permission capture positioning failures. Final full runs have no failure/retry. No partial or failed replay is recorded as acceptance. No backend check is needed or claimed.

## Captures

Final scoring inventory: **278 PNGs**, hashed in `captures-final.json`: **272 monitoring matrix PNGs** under `browser/captures/{health,performance}/review-final/` plus **6 native-theme S3 clock proofs** under `browser/review-s3-final/` (expanded modal light/dark at desktop/390 px, generated-analysis instant at desktop/390 px). The matrix has **136 measurement JSON files**, indexed in `measurements-final.json`. Widths: 1100×800, 1440×900, 1920×1080 and 390×844, each light/dark, viewport and full-page. Matrix browser clock: Asia/Riyadh, UTC+03:00; dedicated S3 clock proofs explicitly use UTC. No plant timezone is inferred.

| Screen | Final matrix sets (each has all four widths, light/dark, viewport/full-page) | PNGs |
| --- | --- | --- |
| Health | base, stale, no-data, unknown, critical-coverage-missing, threshold-breach, shifted-alarms, viewer-actions | 128 |
| Performance | base, stale, no-data, threshold-breach, comparison-incompatible, shared-views, 500-channels, fresh-live, custom-range | 144 |

Pattern: `browser/captures/<screen>/review-final/<set>/<width>-<scheme>[-full].png`. All **17 contact sheets / 136 viewport originals** were inspected (`inspections-gallery.json`); the custom-range sheet was reopened after fixture diagnostic cleanup (the other 16 were unchanged by SHA-256). **53 final originals** were also opened directly and hashed in `inspections-final.json`: 47 selected matrix originals (every base viewport, light-desktop/dark-phone full pages of each key state, plus custom-range phone light) and all 6 S3 clock proofs. This does not claim every full-page PNG was individually opened. Initial audit captures, intermediate batches and failed traces remain separate historical evidence.

## Open facts and CI

O22 requires both external Mantine & InvenTree Frontend Engineer scores of at least 95; coordinator review/scoring is outstanding. No score or approval is fabricated. Real source freshness, persisted authenticated shared-view journeys, actual critical designations, threshold engineering approval, duty/load compatibility and deployed historian completeness are not established by synthetic fixtures. These are not silently promoted to healthy, compatible or accepted.

CI should run default/all sprint TypeScript projects, asset units (including clock/band tests), full S3, all three S8 monitoring specs, the S8 permission matrix/guard specs and `playwright.performance.config.ts` through their fixture servers; retain failures and isolate timeouts. Run the design matrix with the production theme and all four widths/schemes. Preserve native blue Health trends, signal fields, canonical anomaly/work association, bounded history/batch identity, incompatible-unit separation and shared-view authorization. No shared workflow file was edited.

## Files changed

- `contrib/ui-ux/r6/reports/S8-01-part2.md`
- `src/frontend/playwright.performance.config.ts`
- `src/frontend/playwright/FixtureTheme.tsx`
- `src/frontend/playwright/s0-native.tsx`
- `src/frontend/playwright/s3-performance.tsx`
- `src/frontend/src/pages/assets/displayClock.test.ts`
- `src/frontend/src/pages/assets/displayClock.ts`
- `src/frontend/src/pages/assets/health/AlarmHistory.tsx`
- `src/frontend/src/pages/assets/health/AnomalyList.tsx`
- `src/frontend/src/pages/assets/health/AnomalyTimeShift.tsx`
- `src/frontend/src/pages/assets/health/HealthHistoryProvenance.tsx`
- `src/frontend/src/pages/assets/health/HealthSourceStatus.tsx`
- `src/frontend/src/pages/assets/health/HealthSummary.tsx`
- `src/frontend/src/pages/assets/health/MachineHealthPanel.tsx`
- `src/frontend/src/pages/assets/health/PreliminaryResults.tsx`
- `src/frontend/src/pages/assets/health/RetainedHealthEvidence.tsx`
- `src/frontend/src/pages/assets/health/SignalCondition.tsx`
- `src/frontend/src/pages/assets/health/SignalEvidence.tsx`
- `src/frontend/src/pages/assets/health/SignalTable.module.css`
- `src/frontend/src/pages/assets/health/SignalTable.tsx`
- `src/frontend/src/pages/assets/health/SignalTrend.tsx`
- `src/frontend/src/pages/assets/health/SignalTrendChart.tsx`
- `src/frontend/src/pages/assets/health/StatusBadge.tsx`
- `src/frontend/src/pages/assets/health/common.tsx`
- `src/frontend/src/pages/assets/investigation/HistoryProvenance.tsx`
- `src/frontend/src/pages/assets/monitoring.module.css`
- `src/frontend/src/pages/assets/performance/ChannelNavigator.tsx`
- `src/frontend/src/pages/assets/performance/ComparisonWorkbench.tsx`
- `src/frontend/src/pages/assets/performance/DisplayPreferences.tsx`
- `src/frontend/src/pages/assets/performance/HistoryReadContext.tsx`
- `src/frontend/src/pages/assets/performance/KpiStrip.tsx`
- `src/frontend/src/pages/assets/performance/PerformancePanel.tsx`
- `src/frontend/src/pages/assets/performance/RelationChart.tsx`
- `src/frontend/src/pages/assets/performance/SensorGroupCard.tsx`
- `src/frontend/src/pages/assets/performance/SensorHeatmap.tsx`
- `src/frontend/src/pages/assets/performance/SharedDisplayViews.tsx`
- `src/frontend/src/pages/assets/performance/TimeRangeControl.tsx`
- `src/frontend/src/pages/assets/performance/TrendChart.tsx`
- `src/frontend/src/pages/assets/performance/WindowStatus.tsx`
- `src/frontend/src/pages/assets/performance/format.ts`
- `src/frontend/src/pages/assets/performance/kpis.ts`
- `src/frontend/src/pages/assets/performance/sections/OperationSection.tsx`
- `src/frontend/src/pages/assets/performance/sections/OtherSection.tsx`
- `src/frontend/src/pages/assets/performance/sections/ThermalSections.tsx`
- `src/frontend/src/pages/assets/performance/sections/common.tsx`
- `src/frontend/src/pages/assets/performance/thresholdBands.test.ts`
- `src/frontend/src/pages/assets/performance/thresholdBands.ts`
- `src/frontend/tests/performance/api.ts`
- `src/frontend/tests/performance/main.tsx`
- `src/frontend/tests/performance/pui_performance.spec.ts`
- `src/frontend/tests/performance/vite.config.ts`
- `src/frontend/tests/s3/healthFixtures.ts`
- `src/frontend/tests/s3/performanceFixtures.ts`
- `src/frontend/tests/s3/pui_health_condition.spec.ts`
- `src/frontend/tests/s3/pui_health_inspection.spec.ts`
- `src/frontend/tests/s3/pui_health_investigation.spec.ts`
- `src/frontend/tests/s3/pui_health_trends.spec.ts`
- `src/frontend/tests/s3/pui_performance_truthful.spec.ts`
- `src/frontend/tests/s3/themeFixtures.ts`
- `src/frontend/tests/s8/monitoringEvidence.ts`
- `src/frontend/tests/s8/pui_monitoring_design.spec.ts`
- `src/frontend/tests/s8/pui_monitoring_review.spec.ts`
- `src/frontend/tests/s8/pui_monitoring_states.spec.ts`
- `src/frontend/tests/s8/pui_permission_matrix.spec.ts`
- `src/frontend/tsconfig.s8.json`
