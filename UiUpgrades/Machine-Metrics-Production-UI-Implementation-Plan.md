# Machine Metrics and Widgets: Production UI Implementation Plan

Date: October 22, 2026
Status: implementation specification; application changes have not been made
Repository: EQUA-AI/InvenTree
Checkout inspected: `fix/attachment-exact-target`, including uncommitted changes

## 1. Objective

Keep the machine, location, and dashboard metrics presentation, but make it work
with ordinary production data. Remove the feature's synthetic-data provisioning,
session machinery, backend workarounds, management commands, and database
migrations.

The application must not need seeded machines, a generated dataset, an activated
feed, or a session selection to display metrics. Data supplied through the normal
production integrations must appear through the same UI.

This document specifies the implementation. It does not report completed
application changes, executed tests, or production readiness.

### Confirmed decisions

| Decision | Requirement |
| --- | --- |
| Data source | Use the existing production machine-health and work-order APIs. Do not introduce a replacement backend metrics engine. |
| Missing history | Keep the historical widgets visible with an explicit data-unavailable state until a suitable real source is connected. |
| Naming | Remove or rename the metrics feature's APIs, files, symbols, query parameters, cache keys, test fixtures, and active references containing `demo`. |
| Delivery boundary | UI work plus removal of the inventoried backend scaffolding. No new ingestion, seeding, replay, scheduler, schema, or deployment work. |

### Scope boundary

The naming cleanup applies to this metrics implementation and its consumers,
not to every occurrence of `demo` throughout InvenTree. Existing, unrelated sample
data commands, upstream demonstration features, historical Git commits, backup
checkouts, and other projects are not targets for a repository-wide replacement.

The primary file inventory is
[EQUA_Demo_Metrics_File_Inventory.md](../LocalDocs/UiUpgrades/EQUA_Demo_Metrics_File_Inventory.md).
It is a starting point, not a complete current-state manifest: it references a
different bounded-loop worktree, and subsequent UI changes introduced additional
consumers. Inspect the current checkout before deleting or restoring anything.

The [chat drawer and OpenUI plan](./Chat-Drawer-UI-and-OpenUI-Implementation-Plan.md)
is a separate workstream. Do not change its transports, presentation protocol,
approval model, or conversation state as part of this implementation.

## 2. Preserve these behaviors

- Machine health, signal trends, anomaly displays, and their existing actions.
- Physical-location browsing, machine placement, transfers, and permission gates.
- The attention-first layout above machine inventory.
- Critical-machine, work-order, overdue, and data-quality indicators, where real
  production data supports their definitions.
- Readable machine names, work-order references, lifecycle labels, and drill-downs.
- Location and direct/descendant filters, including return navigation.
- Dashboard widget selection, configuration, layout, and existing stable IDs.
- The maintenance board's unrelated improvements, including card density,
  filtering, touch/keyboard controls, and task-versus-work-order identity.
- Responsive layouts, accessibility, localization, and explicit read failures.
- Existing scope enforcement and authorization. Removing the seed system is not
  a reason to restore permissive health reads.

Do not blanket-revert shared files to HEAD or to the inventory's original commit.
The checkout contains substantial unrelated work.

## 3. Existing production API contract

### 3.1 Reuse, do not replace

| Existing surface | Intended use | Constraints |
| --- | --- | --- |
| `/api/assets/locations/` | Location graph, current direct/descendant totals, and authorized machine placement | Remove only the session-specific filter and membership hooks. Preserve ordinary scope checks and pagination. |
| `/api/assets/locations/machines/` | Resolve the authorized machine population for a location | Use `location` and `include_descendants`; do not send a session identifier. Inventory table search must not silently change dashboard totals. |
| `/api/machine-health/machines/{id}/health/` | Current machine state, signal freshness, configuration, and anomaly counts | Reuse `MachineHealthSummary`; do not reconstruct a different condition algorithm. |
| Existing machine-health signals, anomalies, trend, and snapshot endpoints | Machine detail and supported telemetry visualizations | Keep their production semantics and action permissions unchanged. |
| `/api/aichat/ui/maintenance-metrics/` | Existing scoped work-order and dashboard projections | Reuse its current-role/current-scope contract, response validation, filters, reporting timezone, completeness indicators, and contributor pagination. Its namespace does not make it a new AI-generation dependency. |
| `/api/kanban/work-orders/` and existing board-card/detail APIs | Existing board and detail-page data | Preserve established behavior. Do not use a broadly fetched board list as a substitute for an authorized metrics projection. |

Relevant existing implementations:

- [MachineHealth.tsx](../src/frontend/lib/types/MachineHealth.tsx)
- [MachineHealthPanel.tsx](../src/frontend/src/pages/assets/health/MachineHealthPanel.tsx)
- [MaintenanceWidget.tsx](../src/frontend/src/components/dashboard/widgets/MaintenanceWidget.tsx)
- [maintenanceMetrics.ts](../src/frontend/src/components/dashboard/widgets/maintenanceMetrics.ts)
- [maintenance_metrics.py](../src/backend/InvenTree/aichat/maintenance_metrics.py)
- [dashboard_metrics.py](../src/backend/tasks/dashboard_metrics.py)
- [readQueryPolicy.ts](../src/frontend/src/functions/readQueryPolicy.ts)

### 3.2 Removal is not a backend API rename

Delete `/api/assets/demo-metrics/` and its session, metrics, work-order, and history
routes. Do not retain aliases, redirects, compatibility wrappers, or a renamed
version of the same session engine.

No replacement `/api/assets/machine-metrics/` endpoint is required by this plan.
The replacement is frontend composition of existing production reads.

Delete the session payload types and request parameters instead of disguising
them with generic names. In particular, do not turn `demo_session` into a
`metrics_session` dependency.

### 3.3 Location aggregation

The existing maintenance-metrics API accepts machine and client filters; its
inspected filter contract does not accept a physical-location filter. The UI
must not send an unsupported location parameter and assume that it was applied.

For location-specific metrics:

1. Resolve the authorized location machine population through the existing
   placement API, respecting direct versus descendant scope.
2. Traverse all required machine-list pages before claiming a full-scope count.
   The first inventory page is not the dashboard population.
3. Use existing per-machine health reads and machine-filtered maintenance metric
   reads where needed. Bound concurrent requests and reuse cached reads; do not
   launch an unrestricted request for every machine on each render.
4. Combine only additive measures over disjoint machines. Count machines
   distinctly; do not count task cards as work orders. Do not sum percentages,
   medians, overlapping groups, or truncated contributor lists.
5. Honor response completeness and contributor pagination separately from
   authoritative totals. A total supplied by the production API is not the
   number of rows currently displayed.
6. Cancel obsolete requests on scope changes. Do not show the previous location's
   data while the next location is loading.
7. If a required read fails, show an explicit failed/incomplete state for the
   affected metric. Never present a partial sum as a full total.

Use the same production projections directly for dashboard widgets and
single-machine views where their filters already express the requested scope.
If a metric cannot be computed correctly from existing contracts, mark that
metric unavailable rather than adding a backend workaround.

## 4. Metric semantics and UI states

### Current machine state

- Use the backend's `normal`, `warning`, `critical`, `offline`, and `unknown`
  states and the existing health presentation helpers.
- Do not relabel the production summary as "fresh signals only": its state can
  also reflect active anomalies.
- Show configuration, freshness, and degraded-data information separately from
  condition. Missing monitoring does not imply normal equipment.
- A failed or denied health request is not an unconfigured machine or an
  `unknown` condition returned successfully.
- Show observation time where available. A frontend fetch timestamp must not be
  labeled as the time telemetry was observed.

### Work orders

- Preserve the production definition of open work: planned, ready, in progress,
  on hold, and verifying. Drafts and inactive work must not be silently added.
- Use existing production overdue calculations and the reporting timezone, not
  browser-local string comparisons against due dates.
- Distinguish open work-order count from machines with open work and from board
  task-card count.
- Keep contributor links and filters consistent with the displayed metric.
- Label bounded contributor lists and provide pagination or an appropriate
  drill-down; do not imply the visible rows are the full contributing set.

### Historical widgets

Keep the history section and its intended downtime/coverage/availability
presentation. Until a suitable real source exists:

- Display "Historical data unavailable" with a short explanation that validated
  operating-time and downtime history is not connected.
- Render unavailable values as unavailable, not zero and not a synthetic series.
- Do not issue a request to a removed history endpoint or retry a known missing
  capability.
- Do not fabricate a successful empty response to drive an empty-state chart.
- Disable historical controls that have no effect, or state explicitly that
  changing the selected period cannot supply missing data.
- Retain any genuinely supported signal-trend UI; telemetry trends and measured
  historical availability are different capabilities.

The existing `Recorded downtime` production widget is a separate, supported
measure: amended downtime recorded on completed jobs. Preserve it when available,
including its existing limitations. Do not present it as unique outage duration,
location-at-event history, measured availability, MTTR, or OEE.

### Read-state handling

Keep loading, successful-empty, unavailable, forbidden, invalid-response, and
temporary-failure states distinct. Follow existing read-recovery and error-notice
patterns rather than adding a second retry framework.

Scope/cache identity must include the relevant host, authenticated identity or
authorization generation, machine/location, direct/descendant mode, metric,
filters, and actual reporting window. No session component remains.

On sign-out or denied access, earlier authorized payloads must no longer be
readable. Avoid cache-removal/refetch loops. Stale values retained after a
temporary refresh failure must be clearly labeled and must never survive an
authorization boundary.

## 5. Frontend rename and responsibility map

Paths in this section are relative to `src/frontend/`.

| Current path or surface | Required result |
| --- | --- |
| `src/pages/assets/locations/DemoMetricsPanel.tsx` | Rename to `MachineMetricsPanel.tsx`; export `MachineMetricsPanel`. Remove session selection, synthetic badges, session fetches, and session-specific notices. Render production-backed attention, state, and work summaries. |
| `src/pages/assets/locations/DemoHistoryPanel.tsx` | Rename to `MachineHistoryPanel.tsx`; export `MachineHistoryPanel`. Retain historical presentation with an honest unavailable state; remove synthetic attribution and session copy. |
| `src/pages/assets/locations/demoMetrics.ts` | Replace with `machineMetrics.ts` containing only needed production types, scope/query helpers, and aggregation/presentation logic. Reuse existing types and helpers first. |
| `src/pages/assets/locations/demoMetrics.test.ts` | Rename to `machineMetrics.test.ts`; replace session-engine assumptions with production scope, count, failure, and unavailable-history tests. |
| `src/pages/assets/locations/LocationWorkspace.tsx` | Wire the renamed panel. Remove session props, parameters, state, and cache-key components from summary and machine reads. Keep location filtering and placement behavior. |
| `src/pages/assets/locations/MachineLocationCard.tsx` | Remove session propagation. Preserve real physical placement, move permissions, and location links. |
| `src/pages/assets/locations/locationTree.ts` | Remove session fields and URL construction/parsing. Preserve source-view, location, direct/descendant, and return-route behavior. |
| `src/pages/assets/locations/locationTree.test.ts` | Update link and breadcrumb expectations for production-only scope. |
| `src/pages/assets/MachineDetail.tsx` | Remove `MachineDemoScopeNotice`. Preserve machine health, placement, detail panels, and source-list navigation. |
| `src/pages/maintenance/MaintenanceBoard.tsx` | Remove session work queries and `DemoScopeBanner`. Apply genuine location filters to board cards when arriving from a location metric; preserve all unrelated board changes. |
| `src/pages/tasks/workorders/WorkOrderDetail.tsx` | Replace session-specific link helpers with the production scope helper. Preserve the scoped return to the board. |
| `src/components/dashboard/widgets/MaintenanceWidget.tsx` and `maintenanceMetrics.ts` | Reuse existing production contracts and stable widget IDs. Extract shared read/presentation logic only where actually needed; do not recreate the widget system. |
| `src/components/dashboard/DashboardWidgetLibrary.tsx` | Change only wiring required for the retained metrics UI. Do not reset saved dashboard selections or unrelated widgets. |
| `src/locales/*/messages.po` | Extract updated production copy with Lingui; remove obsolete active strings/references using the normal extraction workflow. Preserve unrelated translations. |

### Symbols, URLs, and caches

- Remove `DemoSessionSummary`, session fields, synthetic-only metadata, and
  session-dependent capability assumptions.
- Replace needed `DemoMetrics`, `DemoWorkList`, and history types with accurate
  production types, preferring existing shared contracts.
- Replace `demoQueryKeys`, `demoAvailability`, `clearDeniedDemoQueries`,
  `demoHref`, and `parseDemoScope` only where their responsibilities remain.
  Reuse existing read-policy and location-navigation helpers instead of retaining
  duplicate renamed code.
- Remove `demo_session` from generated links and API calls. Preserve `location`,
  `scope=direct`, and source-view navigation.
- Old bookmarks containing a removed session parameter must not trigger session
  reads. Remove/ignore that obsolete parameter without inventing an alias;
  preserve independently valid location scope.
- Replace the `demo-metrics` cache namespace with a production namespace where
  needed; coordinate refresh/invalidation with existing health and maintenance
  keys.
- Remove synthetic-session labels, fixture-fallback copy, test IDs, browser
  globals, and session-specific storage-key components.
- Do not rename existing production API paths or stable dashboard widget IDs
  merely because old frontend helpers referenced them.

### Browser test infrastructure

Retain useful UI regressions by renaming and updating their mocked contracts:

| Current path | Required result |
| --- | --- |
| `playwright.demo-metrics.config.ts` | `playwright.machine-metrics.config.ts`; update test match and output directory. |
| `playwright/demo-metrics.html` | `playwright/machine-metrics.html`; update entry reference and title. |
| `playwright/demo-metrics.tsx` | `playwright/machine-metrics.tsx`; mount real production-facing components with test-only API interception. Remove session mode and synthetic cache hooks. |
| `playwright/demo-metrics-lingui.ts` | `playwright/test-lingui.ts`; update every importer, including `machine-locations.tsx` and `voice-deferred-focus.tsx`. |
| `tests/pages/pui_demo_metrics.spec.ts` | `tests/pages/pui_machine_metrics.spec.ts`; test production payloads and unavailable history. Preserve applicable machine-health and board regressions. |
| `playwright/tests/playwright.demo-metrics-e2e.config.ts` | Delete: its real-backend acceptance path depends on the removed provisioning stack. |
| `playwright/tests/pui_demo_metrics_e2e.spec.ts` | Delete or move any independently useful UI assertion into the production UI suite before deletion. |

Also update comments and imports in `playwright.chat.config.ts`,
`playwright/aichat-drawer-lingui.ts`, `tests/pages/pui_asset_locations.spec.ts`,
and any additional consumers found by the final reference scan.

Test-only mocked API responses are allowed; they must never become an
application data fallback or an alternative runtime data engine. Mocked browser
tests must not be described as authenticated production integration tests.

## 6. Delete the inventoried backend scaffolding

Paths below are relative to the repository root. Delete only files present in
the current checkout and verified as part of this feature.

### Engine, models, and HTTP adapter

Delete the source files and fixture resources under:

```text
src/backend/InvenTree/assets/demo_metrics/
```

This includes the planner, apply service, replay/history/cohort logic, cleanup,
effect guards, fingerprint helpers, CLI contract, reference code, and test
support. Do not leave importable generated bytecode behind as an apparent source
module.

Delete:

```text
src/backend/InvenTree/assets/demo_metrics_api.py
src/backend/InvenTree/assets/demo_metrics_models.py
src/backend/InvenTree/assets/demo_metrics_bounded_models.py
```

The bounded-loop model file is conditional: the inventory lists it in another
worktree, but it was not found in this checkout. Do not edit that other worktree.

### Management commands

Delete the feature-specific commands:

```text
src/backend/InvenTree/assets/management/commands/activate_demo_feed.py
src/backend/InvenTree/assets/management/commands/apply_demo_metrics.py
src/backend/InvenTree/assets/management/commands/cleanup_demo_metrics.py
src/backend/InvenTree/assets/management/commands/plan_demo_feed.py
src/backend/InvenTree/assets/management/commands/plan_demo_metrics.py
src/backend/InvenTree/assets/management/commands/replay_demo_metrics.py
src/backend/InvenTree/assets/management/commands/stop_demo_feed.py
src/backend/InvenTree/assets/management/commands/stop_demo_metrics.py
src/backend/InvenTree/assets/management/commands/verify_demo_metrics.py
```

The feed commands are conditional on presence. Do not delete unrelated existing
commands such as `load_asset_demo_data.py`, `load_asset_location_demo.py`, or
`load_water_workflow_demo_data.py`.

### Database migration source files

Remove:

```text
src/backend/InvenTree/assets/migrations/0017_demometricscoverageinterval_demometricssession_and_more.py
src/backend/InvenTree/assets/migrations/0018_alter_demometricsreceipt_operation_kind.py
src/backend/InvenTree/assets/migrations/0019_demometricsfeedgrant_demometricsfeedstate_and_more.py
```

The third migration is conditional on presence and was not found in this
checkout. Check dependencies from retained migrations before removing files.
Do not add replacement migrations for this UI-only feature.

Source removal is not a database rollback. Do not execute reverse migrations,
drop tables, delete seeded records, flush data, or modify migration bookkeeping.
If any deployment has already applied these migrations, record that fact and
obtain a separate, approved schema-retirement procedure before deploying a
change that would leave its migration graph inconsistent.

### Engine-only backend tests

Delete:

```text
src/backend/InvenTree/assets/test_demo_metrics_apply.py
src/backend/InvenTree/assets/test_demo_metrics_authorization.py
src/backend/InvenTree/assets/test_demo_metrics_bounded_loop.py
src/backend/InvenTree/assets/test_demo_metrics_cohort_api.py
src/backend/InvenTree/assets/test_demo_metrics_contract.py
src/backend/InvenTree/assets/test_demo_metrics_history_cleanup.py
src/backend/InvenTree/assets/test_demo_metrics_lifecycle.py
src/backend/InvenTree/assets/test_demo_metrics_models.py
src/backend/InvenTree/assets/test_demo_metrics_notifications.py
src/backend/InvenTree/assets/test_demo_metrics_reconcile.py
```

The bounded-loop test is conditional on presence. Keep production health,
placement, scope, work-order, and dashboard tests.

## 7. Remove hooks from shared backend files

| File | Surgical change |
| --- | --- |
| `src/backend/InvenTree/InvenTree/ready.py` | Remove the synthetic import context, command tables, command detection, and startup exemptions. Preserve ordinary import and management-command behavior. |
| `src/backend/InvenTree/InvenTree/tasks.py` | Remove imports and calls into the deleted effect-guard package from single, batched, and bulk task dispatch. Preserve normal dispatch behavior. |
| `src/backend/InvenTree/assets/api.py` | Remove the HTTP adapter import and the session metrics route. |
| `src/backend/InvenTree/assets/models.py` | Remove model registration imports for the deleted ledger models. |
| `src/backend/InvenTree/assets/location_api.py` | Remove session membership parsing, imports, cohort intersections, and session-specific comments. Preserve authorized location and machine/work-order filtering. |
| `src/backend/InvenTree/aichat/tests/test_rollback_floor.py` | Remove or rewrite the bootstrap-specific explanation, not the valid read-only regression assertion. |
| `src/backend/InvenTree/aichat/apps.py` | Preserve the independently valid read-only setting lookup; it is not a replacement data engine. |
| `src/backend/InvenTree/InvenTree/test_tasks.py` | Preserve independently useful plugin-registry test setup. |
| `src/backend/tasks/scope.py` and `src/backend/InvenTree/machine_health/api.py` | Preserve production machine-scope enforcement. Delete only genuinely obsolete engine references, if any remain. |
| `src/backend/InvenTree/machine_health/tests/fixtures.py`, `test_api.py`, and `test_trends.py` | Preserve production scope-denial tests and scoped fixtures supporting retained authorization. |

Do not equate "introduced while building the old feature" with "safe to revert."
In particular, removing fail-closed machine authorization would change production
security behavior and is outside the requested cleanup.

## 8. Remove provisioning probes, runbooks, and companion sources

Delete the inventoried feature-specific files under `contrib/container/`:

```text
bootstrap_probe/harness_validators.py
bootstrap_probe/net_guard.py
bootstrap_probe/sitecustomize.py
bootstrap_probe/test_harness_validators.py
demo-metrics-bootstrap-probe.sh
demo-metrics-e2e.sh
demo-metrics-job-input.example.json
demo-metrics-job-spec-tests.py
demo-metrics-job-spec.py
demo-metrics-release-image-probe.sh
demo-metrics-runbook.md
demo-metrics-session-regression-tests.py
demo_metrics_bootstrap_fingerprint.py
demo_metrics_bootstrap_negative.py
demo_metrics_bootstrap_settings.py
demo_metrics_bootstrap_setup.py
demo_metrics_e2e_proxy.py
demo_metrics_e2e_session_regression.py
demo_metrics_e2e_settings.py
demo_metrics_e2e_setup.py
demo_metrics_release_fingerprint.py
demo_metrics_release_guard.py
demo_metrics_release_image.py
demo_metrics_release_negative.py
demo_metrics_release_settings.py
demo_metrics_release_setup.py
test_demo_metrics_release_guard.py
```

Remove the one-shot provisioning section from
[AIMMS-release-checks.md](../contrib/container/AIMMS-release-checks.md).
Keep unrelated build-identity and release checks.

The inventory also identifies a Git-ignored source companion at
`LocalDocs/UiUpgrades/EQUA_Demo_Metrics_Data_Companion/`. Inspect it and remove the
obsolete dataset generator, seed fixtures, mapping example, generator test, and
bootstrap instructions that belong to this feature. Do not move them elsewhere
as a hidden supported provisioning path.

Keep historical acceptance evidence and prior review documents as historical
records, not runnable implementation dependencies. Mark active instructions
superseded where necessary. This document necessarily uses old names to identify
removal targets; that is not a retained runtime reference.

The inventory's `.gitleaksignore` entry was not found in this checkout. Do not
create or weaken scanning exclusions. If an entry exists at implementation time,
remove only verified engine-specific exceptions, preserving unrelated rules.

Update
[UI-Implementation-Notes.md](./UI-Implementation-Notes.md)
to describe the new production behavior and renamed validation entry points.
Treat the earlier
[Dashboard-Visualization-Review.md](./Dashboard-Visualization-Review.md)
as historical evidence; do not rewrite its old test results as new verification.

## 9. Dependency-ordered implementation

### Work package 1: Establish the removal boundary

1. Capture current tracked/untracked changes and inspect the inventory's paths.
2. Confirm additional importers and routes, including shared browser fixtures.
3. Inspect migration dependencies without modifying any database.
4. Separate engine hooks from unrelated work and retained production hardening.

### Work package 2: Build production-facing UI reads

Depends on work package 1.

1. Rename the UI files and remove session concepts from component contracts.
2. Reuse existing health types, maintenance projections, and read-recovery helpers.
3. Implement location composition with complete pagination, bounded concurrency,
   cancellation, correct aggregation, and explicit failure states.
4. Preserve supported widgets and show unsupported history as unavailable.

### Work package 3: Wire all destinations and naming

Depends on work package 2.

1. Update location, machine, maintenance-board, and work-order-detail consumers.
2. Remove session parameters, storage/cache components, labels, and notices.
3. Preserve production location filters in drill-down and return navigation.
4. Rename the browser harness and shared Lingui initialization; update importers.
5. Extract locale changes through the existing tooling.

### Work package 4: Remove the old implementation

Depends on work packages 1-3 so no retained consumer depends on deleted modules.

1. Remove shared backend hooks and route/model registration.
2. Delete engine modules, commands, migration sources, and engine-only tests.
3. Delete provisioning tools and obsolete companion source files.
4. Update active release and UI documentation.
5. Check for imports, runtime strings, bytecode artifacts, and migration references
   that still point at removed files.

### Work package 5: Verify and document the result

Depends on work packages 2-4.

1. Run focused unit and browser regressions.
2. Run TypeScript checking and the affected backend checks.
3. Perform the feature-scoped residual-name scan.
4. Inspect the final diff for unrelated changes and missing surfaces.
5. Record actual results and limitations in the implementation notes.

## 10. Acceptance and regression matrix

| Case | Required outcome |
| --- | --- |
| No seeded dataset, feed, or session exists | Production machine pages and widgets load without a session request or selector. |
| Machines span multiple placement pages | Dashboard totals include the whole authorized scope, not just displayed inventory rows. |
| Direct versus descendant mode | Metrics, inventory scope, contributing work, and drill-downs agree. |
| Machine-table search changes | Dashboard totals remain location totals and are labeled accordingly. |
| Critical state with active anomalies | Production health semantics remain intact; the UI does not discard the state because it was not derived solely from fresh signals. |
| No monitoring configured | Explicit configuration/data-health message; no implied healthy equipment. |
| Health or work request fails independently | The affected metric reports failure; other independently valid sections remain usable. |
| One machine in an aggregate is denied or fails | No misleading complete aggregate; no unauthorized cached payload remains visible. |
| Known counts | Exact counts match the production projection, including drafts/inactive exclusions and distinct machines. |
| Reporting-date boundary | Overdue and period metrics use the production reporting clock, not the browser timezone. |
| Contributor list is paginated | Correct authoritative count plus clearly bounded/paginated contributors. |
| Historical availability source missing | Visible unavailable state; no zero, synthetic series, removed-route request, or useless retry loop. |
| Recorded job downtime exists | Supported production widget still works without claiming unique outage duration or measured availability. |
| Scope changes during an in-flight request | Obsolete reads are canceled/ignored and cannot populate the new scope. |
| Sign-out, account/host change, or permission denial | Earlier authorized payloads are cleared/hidden and not reused across identities. |
| Location metric to board to work order to return | Valid location/direct filters survive; no session parameter or synthetic banner appears. |
| Existing dashboard configuration | Stable widget IDs and saved layout/configuration remain usable. |
| Shared browser fixtures | Chat, location, and voice fixtures still import the renamed locale initializer successfully. |
| Desktop and phone layouts | Attention cards, unavailable history, contributor tables, and controls remain readable and keyboard/touch accessible. |
| Startup and task dispatch | No imports of removed modules; ordinary startup and dispatch behavior remain intact. |
| Migration graph | Retained migration files do not depend on deleted nodes. No database modification is performed as part of verification. |

### Validation entry points

Use existing tools and the smallest relevant selectors:

- Frontend unit tests for `machineMetrics.test.ts`, `locationTree.test.ts`,
  retained trend geometry, and any shared read helpers changed.
- The renamed `playwright.machine-metrics.config.ts` suite, plus targeted
  location and maintenance-widget regressions.
- TypeScript checking using the project's installed compiler and configuration.
- Backend startup/import and migration-graph checks in the configured project
  environment; targeted health/scope, location, task-dispatch, and production
  dashboard regressions where changes warrant them.
- Lingui extraction/compilation and the normal affected-file formatting checks.

Do not provision a database, run deployment jobs, install new frameworks, or
start a seed/feed command to validate this UI cleanup. If backend tests require
an unavailable isolated database, report that limitation rather than using a
shared production database.

Scan the touched runtime code, frontend source metadata, tests, test
configuration, and active operator instructions case-insensitively for `demo`.
Every remaining match must either be an unrelated pre-existing facility or a
documented historical/removal reference. There must be no retained feature API,
file name, symbol, request parameter, cache key, user-facing label, or runnable
provisioning reference containing that term.

## 11. Definition of done

- Metrics UI works with existing production APIs without synthetic sessions.
- Historical widgets stay visible and honestly unavailable where real supporting
  data is absent.
- Applicable production machine-health and dashboard behaviors are preserved.
- All feature-specific runtime names and active references are production-neutral.
- The inventoried engine, provisioning tools, migration sources, commands, and
  engine-only tests are removed from the current checkout.
- No backend replacement engine, ingestion workaround, session alias, or new
  database migration is introduced.
- Authorization hardening and unrelated work are preserved.
- No live data or database schema was modified during this cleanup.
- Focused tests and type checking have recorded results; any blockers are explicit.
- Implementation notes reflect the final names, behavior, validation, and limits.
- No commit, issue, or pull request is created automatically.
