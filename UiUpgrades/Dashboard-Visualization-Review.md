# Dashboard and Visualization Review

Review date: October 21, 2026
Branch reviewed: `fix/attachment-exact-target`
Scope: all identified site/location dashboards, machine-health trends, maintenance views, and new demo-metrics panels.

This review covers the uncommitted dashboard-related changes and the existing health components they connect to. The findings describe the working tree inspected during the review; line numbers may move as development continues. No application source files were changed as part of the review.

## Overall assessment

The biggest design opportunity is hierarchy: urgent work and missing telemetry should be apparent before users scan tables. The site workspace currently places its new demo-metrics panel below the machine inventory, and most cards have similar visual weight despite representing very different levels of urgency.

Preserve these foundations:

- Explicit synthetic-data labels and deliberate opt-in to a demo session.
- Separate reporting of observation coverage and machine condition.
- Missing historical coverage represented as unavailable or null rather than fabricated zero downtime.
- Session and location context carried through drill-down navigation.
- Clear distinctions between preliminary analysis, acknowledging an anomaly, and creating repair work.
- An explicit status selector on maintenance cards for keyboard and touch users.

Address the two health-data correctness issues first, followed by dashboard hierarchy, chart interpretation, drill-down actions, and responsive layouts.

## Correctness issues

These two issues are in existing machine-health components, rather than newly introduced by the uncommitted dashboard changes. They matter to the overall dashboard experience.

### P1 — Failed health requests can appear as empty results

**Evidence:** [MachineHealthPanel.tsx:172](../src/frontend/src/pages/assets/health/MachineHealthPanel.tsx#L172).

Signals and anomalies default to empty arrays without checking their individual request errors. Only a summary-request error receives a dedicated error view. If the summary succeeds but an anomaly request fails without cached data, an operator can see “No active anomalies.” A failed signals request can similarly appear as “No signals are mapped.”

**Impact:** Operators may interpret unavailable evidence as confirmation that no problem exists or that no monitoring is configured.

**Proposed change:** Give the signals and anomalies sections independent loading, error, empty, and ready states. Include a section-specific Retry action. If previously loaded values remain visible after a refresh failure, label their age and the failed refresh.

**Acceptance criteria:**

- Fail the signals endpoint while allowing the summary to succeed: the signals section reports an error, not an empty configuration.
- Fail the anomalies endpoint independently: the anomalies section reports an error, not “No active anomalies.”
- Successful empty responses still receive the appropriate empty-state message.
- Retry recovers the affected section while other successful sections remain usable.

**Evidence limit:** This is a source-based finding; those health-specific failure scenarios were not exercised in the browser during this review.

### P1 — Sparklines can misrepresent readings

**Evidence:** [SignalTrend.tsx:49](../src/frontend/src/pages/assets/health/SignalTrend.tsx#L49).

The sparkline converts values with `Number(sample.value)`, discards timestamps and quality flags, then spaces retained readings evenly. `Number(null)` becomes zero. Removing invalid values also joins the remaining points across missing observations. Irregular sampling therefore appears regular, and missing or questionable data can look like a measured change.

**Impact:** Operators investigating equipment behavior may infer a dip, a continuous trend, or a rate of change that the source data does not support.

**Proposed change:**

- Validate numeric samples explicitly before conversion; preserve missing samples as gaps.
- Position readings using their observation timestamps and sort chronologically.
- Distinguish questionable-quality samples and avoid presenting bad-quality readings as ordinary evidence.
- Provide the time window, units, source, and a meaningful text summary alongside the visual.
- Make the local scale understandable so small variations do not appear disproportionately dramatic.

**Acceptance criteria:**

- Null values do not produce zero-valued points.
- Irregular timestamps produce corresponding horizontal spacing.
- Missing observations break the line rather than implying continuous coverage.
- Bad and uncertain quality states remain distinguishable.
- The time window and source are available without relying solely on the line shape.

**Evidence limit:** This is a source-based finding; the edge cases above were not rendered in the browser during this review.

## Five highest-impact design improvements

### 1. P2 — Put the next decision above the inventory

**Evidence:** [LocationWorkspace.tsx:730](../src/frontend/src/pages/assets/locations/LocationWorkspace.tsx#L730) renders the machine list before the demo-metrics panel. The existing full-app desktop screenshot shows urgent counts below the table.

**Current problem:** Users encounter location administration and inventory before the information that tells them what needs attention. Observation coverage, condition, session size, and work counts receive similar card treatment.

**Proposed change:** Put a compact attention summary near the selected location header. Lead with critical machines, overdue work, and machines without fresh readings. Keep the current location, descendant scope, data source, and an “as of” timestamp visible. Follow with history and prioritized work, then the machine inventory.

When no machines have fresh, good-quality readings, explicitly show “Condition unavailable” with the affected count. A condition card showing only zero normal, warning, and critical machines is too easy to read as reassurance.

**Preserve:** The distinction between coverage and condition, explicit synthetic-data labeling, and meaningful scope information.

**Responsive consideration:** On phones, present a compact attention summary before expandable coverage details.

**Acceptance criteria:** A user can identify overdue work and unavailable condition data without scrolling through the machine inventory. Distinct location totals and session-cohort totals remain clearly labeled.

### 2. P2 — Make history easier to interpret

**Evidence:** [DemoMetricsPanel.tsx:452](../src/frontend/src/pages/assets/locations/DemoMetricsPanel.tsx#L452). The default window is calculated at [DemoMetricsPanel.tsx:113](../src/frontend/src/pages/assets/locations/DemoMetricsPanel.tsx#L113).

**Current problem:** Large planned-machine-minute bars dominate smaller downtime values. The chart has no visible date-range control or explicit axis-unit label. Its title does not explain the period or timezone. An absent downtime bar is also difficult to distinguish visually from measured zero downtime.

**Proposed change:**

- Show the selected date range, UTC timezone, engineering units, and measured-cohort coverage together.
- Provide useful range selection rather than leaving the 14-day window implicit.
- Consider separate, aligned downtime and coverage plots so losses remain legible without conflating different measures.
- Mark unobserved days explicitly, for example with a coverage strip or labeled missing-data treatment.
- Add an accessible daily-values table and a short text summary.
- Explain that availability describes the measured cohort, especially when only a subset of selected machines contributes evidence.

**Preserve:** Null coverage, synthetic attribution, and the warning that current placement does not establish historical location.

**Responsive consideration:** Reduce tick density at narrow widths while retaining units and the visible coverage summary.

**Acceptance criteria:** Users can identify the period, timezone, units, and measured population without opening a tooltip. Unobserved days and measured zero downtime are distinguishable in both the chart and the accessible values.

### 3. P2 — Turn counts into useful investigation paths

**Evidence:** [DemoMetricsPanel.tsx:76](../src/frontend/src/pages/assets/locations/DemoMetricsPanel.tsx#L76) defines static metric cards. Work-order references at [DemoMetricsPanel.tsx:375](../src/frontend/src/pages/assets/locations/DemoMetricsPanel.tsx#L375) are plain text, while machine links show numeric IDs.

**Current problem:** Users can see how much work exists but cannot directly investigate a specific count or open a contributing work order from its reference.

**Proposed change:** Make “Overdue work orders” open the corresponding filtered list. Link work-order references to their details, and prefer machine names alongside immutable IDs. Render lifecycle values as readable, translated labels rather than raw codes such as `in_progress`.

**Preserve:** Session, location, and descendant filters throughout navigation, plus explicit truncation information when a contributing list is bounded.

**Responsive consideration:** Keep the primary reference and machine links easy to activate in horizontally scrollable tables or a compact row presentation.

**Acceptance criteria:** Keyboard and pointer users can move from an overdue count to the contributing work and then to a work-order detail without losing scope. Full totals remain distinguishable from the displayed row count.

### 4. P2 — Let maintenance work dominate the board

**Evidence:** [MaintenanceBoard.tsx:1384](../src/frontend/src/pages/maintenance/MaintenanceBoard.tsx#L1384) and the existing full-app maintenance-board screenshot. Card actions are rendered at [MaintenanceBoard.tsx:1646](../src/frontend/src/pages/maintenance/MaintenanceBoard.tsx#L1646).

**Current problem:** Seven filters, column administration, and repeated card actions consume substantial space. The screenshot shows tall cards with repeated descriptions and controls, while empty columns stretch alongside the populated column.

**Proposed change:** Keep Search, Priority, and active filter chips visible. Place secondary filters behind “More filters,” and move deletion and column administration into menus. Tighten card spacing and prioritize the task title, machine, owner, urgency, and due date. Keep longer descriptions in task details.

**User requirement — optional collapsed card view:** Provide a board-level choice between **Collapsed cards** and **Expanded cards**. Collapsed cards show only the important details: work-order reference and title, machine, priority, due date or overdue indicator, and a compact owner label. The board column already communicates the stage. Omit descriptions, extended metadata, and the always-visible editing controls from collapsed cards.

Clicking a collapsed card opens its full-page work-order detail. This is navigation to the complete work order, rather than expanding the card inline or opening an edit modal. Use an accessible primary link so keyboard users can open the same destination and browser link behavior remains available. Any separate menu or status control must operate without triggering that navigation, and dragging a card must not accidentally open it.

**Preference handling:** Switching card density retains the current filters, column order, and task state. Recommended: remember the user's selected card density across visits.

**Preserve:** Visible ownership and urgency, a reachable status selector for keyboard and touch users through expanded cards or a secondary action, and the existing behavior that retains an unsaved draft when opening contextual chat.

**Responsive consideration:** Make the density choice usable on desktop and narrow screens. Collapsed cards retain readable task identity and urgency, allow long titles to wrap sensibly, and provide a comfortable touch target. Ensure secondary filters and action menus remain reachable on touch devices.

**Acceptance criteria:**

- Users can explicitly switch between collapsed and expanded card views.
- Collapsed cards show only the essential details listed above; expanded cards retain access to the fuller information and controls.
- Clicking or keyboard-activating the primary card link opens the correct full-page work-order detail, with active session/location scope preserved where applicable.
- Returning to the board preserves the selected density and active filters.
- Menus, status changes, and dragging do not accidentally trigger navigation.
- Users reach actionable cards sooner, active filters remain understandable, and moving a task remains possible without drag-and-drop.

### 5. P2 — Fix narrow-width overflow and reduce vertical scrolling

**Evidence:** [DemoMetricsPanel.tsx:222](../src/frontend/src/pages/assets/locations/DemoMetricsPanel.tsx#L222) fixes the session selector width at 320px. A browser check at a 320px viewport measured a document content width of **352px** and a selector width of **320px**.

**Current problem:** The selector overflows its padded container at narrow widths. Pixel 7 component rendering avoids horizontal overflow, but stacking six cards produces a long scroll before users reach work details and history.

**Proposed change:** Give the selector a fluid width with a maximum width. Use a compact attention summary and expandable coverage details on phones rather than stacking every desktop card at full height.

**Preserve:** Clear demo-session selection, synthetic-data labeling, readable counts, and the separate treatment of coverage and condition.

**Acceptance criteria:**

- The panel has no document-level horizontal overflow at 320px and at the tested Pixel 7 size.
- Long session names remain usable.
- Urgent counts appear before the extended coverage breakdown.
- Table scrolling stays within the table container.

## Browser evidence and validation

| Check | Result | Limits |
| --- | --- | --- |
| Existing demo-metrics acceptance suite | **40 passed**, desktop and Pixel 7, approximately 2.6 minutes | Uses mocked APIs and real component rendering; this was not a fresh backend E2E run. |
| Desktop and Pixel 7 component screenshots | Captured and inspected | Standalone fixtures do not establish full-app navigation or access. |
| Existing full-app screenshots | Inspected site/current metrics, history, maintenance board, machine-detail capture, and phone capture | These are pre-existing artifacts. The machine-detail capture showed loading, so it did not establish the final rendered health layout. |
| Additional 320px check | **Overflow confirmed:** 352px document width in a 320px viewport | The fixed-width session selector exceeded its padded container. |
| Health-section request failures and sparkline edge cases | Source reviewed | Browser scenarios described in the correctness acceptance criteria were not executed. |

The existing full-app phone screenshot shows the mobile dashboard-access notice rather than the dashboard. Therefore, passing Pixel 7 component checks alone does not establish that phone users can reach these screens through the full app. Full-app mobile access remains unverified and should be checked against the intended mobile-mode configuration.

The normal test configuration initially could not start its web server because `yarn` was unavailable. Vite was started directly using the installed local executable, and the existing suite then reused that server. The review-started server was stopped after testing.

## Suggested implementation order

1. Correct independent health-request error states and sparkline data handling.
2. Move an attention summary above the inventory and clearly communicate unavailable condition data.
3. Improve historical-chart context, coverage encoding, and accessible values.
4. Connect metric counts and work references to scoped investigation paths.
5. Add the optional collapsed/expanded maintenance-card view with full-page work-order navigation, simplify maintenance controls, and finish narrow-width layouts.

Implementation was not performed during the review. This document records recommendations and verification results; it does not certify that the proposed fixes have been applied.
