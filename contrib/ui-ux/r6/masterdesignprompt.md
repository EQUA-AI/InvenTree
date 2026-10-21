# Master design prompt — industrial UI/UX, SCADA and digital twin

**Owner instruction (20 October 2026):** "keep below prompt as a masterdesign prompt". This is the master design prompt for every EQUA AIMMS UI screen. Apply it together with `designprompt1.md`. When they differ, the R6 pack's scope and acceptance, the honesty rules (unknown is never zero, healthy or done; no invented facts, limits or geometry), and the existing native architecture and permissions win; this prompt sets the design and verification bar within them. For each screen, fill in the "CURRENT SCREEN TO IMPLEMENT" block at the end from the R6 ticket.

The prompt, verbatim:

MASTER PROMPT — SENIOR INDUSTRIAL UI/UX, SCADA & DIGITAL TWIN PRODUCT DESIGN
ROLE AND EXPECTATIONS
Act as a Principal Product Designer, Senior Industrial UX Architect, SCADA/HMI Interface Specialist, Digital Twin Visualization Designer, and Senior Frontend Engineer with extensive experience designing enterprise-grade industrial monitoring systems, wastewater treatment applications, pump station management platforms, IoT telemetry dashboards, and real-time operational intelligence software.
Design and implement a premium, production-quality interface for the Equal AIMMS / InvenTree wastewater and pump station monitoring application.
The application should look like a sophisticated industrial operations platform used by real plant operators, maintenance engineers, control room personnel, reliability engineers, and management teams.
The design quality should combine the clarity of modern enterprise software, the technical credibility of industrial SCADA systems, the analytical capabilities of an advanced monitoring platform, and the visual sophistication of a digital twin application.
Do not create a generic admin dashboard. Design a purpose-built industrial product.
1. UNDERSTAND THE INDUSTRIAL DOMAIN FIRST
Before changing anything, inspect the existing application, repository, routes, components, APIs, telemetry models, available data, and current screen.
Understand how the system represents:

* Wastewater treatment plants and pump stations.
* Individual pumps, motors, motors' electrical parameters, and related equipment.
* Station, pump, and component hierarchies.
* Live and historical telemetry.
* Alarm limits, warnings, equipment states, and operational events.
* Maintenance status, equipment health, and abnormal operating conditions.
* Trends, performance metrics, and historical comparisons.
* SCADA signals, equipment tags, engineering units, and data quality.
* Station locations, connectivity, and communication status.

Use the actual terminology, fields, data models, and workflows found in the existing project. Do not invent business rules or assume an equipment relationship without verifying it.
Where applicable, structure navigation and information hierarchically:
Treatment Plant → Region / Station → Pump → Equipment / Telemetry Point.
Adapt this structure to the application's actual domain model.
2. DESIGN DIRECTION — PREMIUM INDUSTRIAL OPERATIONS CENTER
Create a distinctive industrial visual identity.
The interface should feel:

* Precise, reliable, engineered, and operationally focused.
* Modern and visually impressive without looking like a gaming interface.
* Information-rich but highly readable.
* Appropriate for large control room displays, engineering workstations, and laptops.
* Consistent across station dashboards, equipment details, telemetry views, alarms, and analytics.

Use a refined industrial color palette, such as:

* Deep navy, charcoal, or neutral backgrounds for a dark control-room theme.
* High-contrast text and restrained secondary text.
* A carefully selected cyan or blue accent for navigation, selection, and interactive elements.
* Green for confirmed normal operation.
* Amber for warnings and attention-required conditions.
* Red for alarms and critical conditions.
* Grey for inactive, unavailable, disconnected, or unknown states.

Use colors consistently and distinguish between an actual alarm, a warning, an inactive asset, a communication failure, and missing telemetry.
Do not rely on color alone to convey severity; include labels, icons, and appropriate accessible indicators.
Avoid excessive gradients, excessive rounded cards, glowing borders everywhere, unnecessary glassmorphism, oversized KPI cards, and generic template layouts.
The interface must look designed by a senior industrial product designer, not automatically assembled from a dashboard component library.
3. PRECISE LAYOUT, ALIGNMENT, AND INFORMATION HIERARCHY
Treat alignment and information hierarchy as engineering requirements.
Use a consistent grid, spacing scale, typography system, component sizing, and visual rhythm.
Design the page according to the actual operational workflow.
A typical station overview may contain:
A. Global application header

* Application identity.
* Current plant or station context.
* Breadcrumb navigation.
* Global search where relevant.
* Current timestamp and time zone.
* Backend connection or telemetry freshness status.
* User profile and relevant application actions.

B. Contextual navigation

* Plant or station selector.
* Asset hierarchy.
* Overview.
* Live telemetry.
* Alarms and events.
* Equipment health.
* Historical trends.
* Reports and analytics.
* Configuration or administration, subject to permissions.

Reuse existing navigation and avoid introducing redundant menus.
C. Operational summary
Display the most important operational indicators in a compact, carefully aligned summary.
Examples, only when supported by actual data:

* Total stations.
* Operational pumps.
* Pumps stopped or unavailable.
* Active alarms by severity.
* Communication status.
* Flow rate.
* Wet-well or sump level.
* Power consumption.
* Equipment health.

Use compact metric summaries with units, timestamps, comparison context, and sparklines where useful.
Do not make every metric a large card. Prioritize operational importance.
D. Main operational workspace
Give the primary operational view the most visual prominence.
Depending on the screen, this may be:

* A station and pump overview.
* A process schematic.
* An interactive asset topology.
* A live telemetry dashboard.
* An equipment performance view.
* An alarm investigation workspace.
* A historical trend analysis interface.

Do not force all these elements onto one page. Choose a layout that serves the screen's primary purpose.
4. BUILD A REALISTIC PUMP STATION VISUALIZATION
Where relevant, create an informative and technically credible visual representation of the pump station.
Potential elements include:

* Pump symbols or equipment illustrations.
* Motors and drive equipment.
* Suction and discharge pipelines.
* Valves and flow direction.
* Wet wells, sumps, or tanks.
* Inlet and outlet connections.
* Relevant pressure, flow, and level measurement points.
* Equipment state indicators.
* Live values and engineering units.

Use a process schematic or simplified 2D digital twin when that is the clearest representation.
Use interactive 3D only when the available geometry, asset data, and user workflow justify it.
Provide equipment selection, hover details, contextual tooltips, and drill-down navigation when appropriate.
If the application contains actual plant drawings, P&IDs, CAD models, or equipment geometry, reuse those assets where possible.
If those assets are unavailable, create a clearly identified illustrative schematic. Never represent a decorative or invented arrangement as an approved engineering drawing or an exact model of the real plant.
Equipment states must come from actual backend values whenever available.
5. REAL-TIME TELEMETRY AND INDUSTRIAL CHARTS
Act as an industrial data visualization specialist.
Choose visualizations based on the engineering question being investigated.
Time-series charts
Use line charts for measurements such as:

* Motor or winding temperature.
* Vibration.
* Electrical current and voltage.
* Active power.
* Pump speed.
* Flow rate.
* Pressure.
* Wet-well level.
* Suction and discharge conditions.

Only display parameters that actually exist in the application's data model.
Threshold and deviation visualization
Where valid engineering limits are configured, show:

* Normal operating regions.
* Warning and alarm thresholds.
* Current measurement.
* Historical excursions.
* Duration of abnormal conditions.
* Missing or stale samples.
* Event markers.

Make threshold lines and units clear. Do not invent OEM limits or apply arbitrary thresholds to production telemetry.
Equipment comparison
Where meaningful, compare pumps using:

* Current operating conditions.
* Power consumption.
* Temperature.
* Vibration.
* Flow or pressure.
* Runtime or starts, if available.

Use comparable units and equivalent time windows. Identify missing data rather than silently treating it as zero.
Additional visualizations
Select appropriately from:

* Compact sparklines for quick trend recognition.
* Multi-series line charts for correlated parameters.
* Heatmaps for recurring alarms or time-based operating patterns.
* Bar charts for pump-to-pump or station comparisons.
* Scatter plots for relationships such as load versus vibration.
* Distribution charts for sensor behavior.
* Event timelines for alarms, starts, stops, and communication interruptions.
* Tables for precise measurements, timestamps, equipment tags, and investigation.

Every chart must have a clear purpose, appropriate scale, readable axes, units, useful tooltips, and a sensible time range.
Avoid excessive donut charts, misleading scales, unnecessary 3D charts, and redundant metrics.
6. MAKE THE APPLICATION FEEL TRULY LIVE
The application should feel like an operational monitoring system receiving continuously updated data.
Implement live behavior using existing WebSocket, MQTT, server-sent event, Kafka-backed API, polling, or other supported data integrations as appropriate to the existing architecture.
Potential interface elements include:

* A live telemetry indicator.
* Last-received timestamps.
* Connection health.
* Live-updating numeric values.
* Smooth chart updates.
* Pump state transitions.
* Active event feeds.
* Newly received alarms.
* Data freshness indicators.
* Historical time-range controls.
* Pause/resume live updates where useful.
* Clear stale-data, reconnecting, and disconnected states.

Do not refresh the entire page for every telemetry update.
Update the affected components and charts efficiently. Preserve the user's selected asset, time range, filters, and investigation context.
Use animations to communicate real changes, not to simulate activity.
Never label hardcoded demonstration values as live telemetry. If a backend or live data source is unavailable, clearly mark the content as demo or simulated data.
7. INDUSTRIAL ANIMATIONS AND 3D
Use purposeful motion to improve operational understanding.
Examples:

* Subtle animated flow indicators along pipes when actual flow direction and state are known.
* Pump rotation or operating-state animation when supported by real equipment status.
* Smooth changes in telemetry values.
* Highlighting a pump associated with a selected alarm.
* Animated transitions between asset hierarchy levels.
* Event timeline updates.
* Interactive equipment selection in a digital twin.
* Carefully restrained transitions between monitoring panels.

For 3D representations, consider equipment inspection, asset selection, spatial relationships, or a plant digital twin.
Use CSS and SVG for lightweight schematic animations. Use a suitable 3D library only when its benefits justify its performance and maintenance cost.
Respect reduced-motion preferences and provide an understandable static alternative.
Do not animate a stopped pump as running, display flow when the flow state is unknown, or use decorative motion to imply a real physical process.
8. ALARMS, EVENTS, AND OPERATIONAL INVESTIGATION
Design alarms as a first-class operational workflow.
Include relevant fields where available:

* Severity.
* Station and equipment.
* Alarm or event description.
* Current value.
* Configured threshold.
* Event timestamp.
* Duration.
* Acknowledgement state.
* Data freshness.
* Investigation or drill-down action.

Distinguish active, acknowledged, cleared, and historical events according to the backend's actual semantics.
Provide sorting, filtering, search, and equipment-level navigation.
Where useful, allow users to open a contextual trend around an alarm to inspect what happened before, during, and after the event.
Do not silently clear, acknowledge, or modify alarms. Require appropriate authorization and confirmation for consequential actions.
9. DATA QUALITY AND ENGINEERING CREDIBILITY
The interface must distinguish between an equipment problem and a telemetry problem.
Handle these conditions explicitly:

* Missing measurements.
* Stale values.
* Sensor faults.
* Communication loss.
* Invalid or implausible measurements.
* Unknown equipment states.
* Unconfigured thresholds.
* Unit or scaling uncertainty.
* Historical gaps.

Display the latest valid sample time and source status when useful.
Never convert missing values to zero, silently substitute demo values, or infer normal operation from the absence of alarms.
Never invent plant performance, safety limits, equipment specifications, or predictive maintenance findings.
Clearly distinguish measured values, calculated indicators, configured limits, and AI-generated insights.
10. REUSABLE DESIGN SYSTEM AND FRONTEND ENGINEERING
Inspect the existing frontend stack and implement within its current architecture.
Reuse the existing component library, charting library, icon package, styling conventions, API services, and state management wherever practical.
Build reusable components for common elements such as:

* Asset status.
* Pump and station summaries.
* Telemetry metric displays.
* Time-series charts.
* Alarm lists.
* Event timelines.
* Connection indicators.
* Data freshness labels.
* Asset selectors.
* Contextual equipment panels.

Keep the implementation modular, maintainable, accessible, and performant.
Preserve existing routes, API contracts, business rules, authentication, permissions, and working functionality.
Do not introduce new dependencies unless they provide a clear benefit.
11. RESPONSIVE CONTROL-ROOM AND ENGINEERING EXPERIENCE
Design for the actual screen sizes used by operators and engineers.
For large desktop and control-room displays, use space effectively to present operational context and trends together.
For laptops and tablets, prioritize the asset, its current state, and the most important investigation workflows.
For narrow screens, simplify multi-column layouts and move secondary information into accessible panels or drill-down views.
Ensure that charts remain legible, tables remain usable, and critical alarms remain visible.
Support keyboard navigation, visible focus, accessible labels, and sufficient contrast.
Avoid excessive animation, expensive 3D rendering, unnecessary re-renders, and excessive chart refresh rates.
12. PROFESSIONAL DESIGN AND VERIFICATION PROCESS
Follow this workflow:
STEP 1 — AUDIT
Inspect the existing screen, code, APIs, telemetry fields, asset relationships, and current design system.
STEP 2 — DEFINE THE SCREEN
Determine the user's primary task and select the most appropriate layout, metrics, charts, equipment views, and interactions.
STEP 3 — IMPLEMENT
Build the actual interface with reusable components, realistic content, and real data integrations where available.
STEP 4 — REFINE
Inspect the rendered screen and correct spacing, alignment, hierarchy, chart readability, visual consistency, and interaction details.
STEP 5 — VERIFY
Run available builds, type checks, linting, and tests. Verify responsive behavior, loading and error states, and functional controls.
Do not stop after producing a plan or a screenshot. Implement the changes in the existing codebase and inspect the actual rendered result whenever browser or preview tools are available.
13. FINAL ACCEPTANCE CRITERIA
The completed screen must:

* Look like a premium industrial monitoring product.
* Have precise alignment, clear hierarchy, and consistent spacing.
* Use domain-appropriate charts rather than decorative visualizations.
* Represent pumps and stations in a technically credible way.
* Clearly distinguish operational states, alarms, and data quality.
* Feel live when connected to real telemetry.
* Use animation and 3D only where useful.
* Display meaningful timestamps, units, thresholds, and context.
* Handle missing, stale, and disconnected data.
* Preserve existing functionality and architecture.
* Be responsive, accessible, and performant.
* Contain no broken controls, misleading metrics, or unfinished visual states.

The final result should be convincing enough for a demonstration to plant engineers, operations managers, and enterprise stakeholders.
Do not optimize for visual effects at the expense of operational clarity. Build a beautiful interface that is also technically trustworthy.
CURRENT SCREEN TO IMPLEMENT
Screen name: [INSERT SCREEN NAME]
Primary user: [OPERATOR / MAINTENANCE ENGINEER / RELIABILITY ENGINEER / MANAGER]
Primary objective: [INSERT THE TASK THE USER NEEDS TO COMPLETE]
Available assets and telemetry: [INSPECT THE CODEBASE AND EXISTING API DATA]
Constraints: [PRESERVE THE CURRENT FRAMEWORK, BACKEND, BUSINESS LOGIC, AND EXISTING DESIGN SYSTEM]
Implement the screen now, verify the result, and summarize the improvements and any remaining dependencies.
