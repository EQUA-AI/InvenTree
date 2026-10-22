# UI round 2 — Hyperagent review repair

Worktree `ui-r2`, branch `r6/ui-r2`, base `c8e86ae46`. Review source:
`scratchpad/gemini-r1.md`, read completely and checked against current source.
The review used older component captures and a diff ending at `c55227c95`;
its source claims are verified individually below. The second Opus review was received during final visual inspection and is tracked below. Earlier qualified Gemini dispositions remain recorded below. This is a bounded UI repair, not deployment or product acceptance.

## Execution ledger

| Step | State | Evidence |
| --- | --- | --- |
| Read guidance, review and inspect current source | Complete | CONTRIBUTING, HANDOFF, masterdesignprompt, designprompt1; source audit |
| Health/shared accessibility repairs | Complete; focused verification passed | Shared ValueTooltip/MissingValue; compact summary and stale-state changes |
| Pump/station mimic repairs | Complete; focused verification passed | Layout, unit, keyboard and KPI changes |
| Machines/location/Parts repairs | Complete; focused verification passed | Table alignment, phone targets, provenance and toolbar changes |
| Phone shell | Complete; final capture checks passed | Selected tab scrolls within its strip; native fields/actions retained |
| Focused browser/unit/types/hooks/translation checks | Complete; focused verification passed | `data/devscratch/ui-r2/checks/` |
| Final 18 captures, each visually inspected | Complete; 18/18 fresh captures passed and each inspected | `data/devscratch/ui-r2/final/` |

Applies the installed `equa-mantine-inventree` and `equa-interface-audit`
skills, using local Mantine 9 types/source and native fixture architecture.
No additional image skill is appropriate for these existing code/SVG screens.

## Finding dispositions

Gemini section 2 blockers/majors are covered below using the report's group and
finding numbers. “Accepted” means verified against source, with the fix applied;
it does not represent deployment acceptance. Qualifications reject the precise
unsupported claim while preserving valid repair work.

### Station and pump (01–06)

| Finding | Disposition after checking source | Implemented repair / files |
| --- | --- | --- |
| A1, station captures 01–03 differ from final source | Accepted as an evidence defect. The current source already omitted `Response time` and closed-accordion summaries; those screenshot captions were not current-source regressions. | This task captures all 18 views from the frozen final source; all 18 final captures passed and each was visually inspected. No historical capture is promoted to current acceptance. |
| A2, split station KPIs and duplicated Station readings | Accepted. Three layout metrics were above the SVG, two totals below, and the station accordion repeated the represented points. | `StationSchematic.tsx` renders layout metrics and totals in one `data-station-metrics` grid. Each present value has pointer, observation/clock or total provenance in its tooltip. `PumphouseMimic.tsx` only renders a station reading group for points not represented in that grid, so additional supplied facts are preserved. |
| A3, phone station header and scroll affordance | Accepted in current source for verbose source/clock rows and absent scroll cue; the report's Response time line had already been removed. | Source/read-time/clock metadata moved to the information tooltip. Station title, Live and Refresh share a compact header; one timestamp remains visible, using the validated display clock and its explicit browser fallback when no plant zone is supplied. Phone viewport has horizontal arrows, a swipe cue and scroll shadows; bay labels retain native readable size. `PumphouseMimic.tsx`, `StationSchematic.tsx`, `StationSchematic.module.css`. |
| A4, weak schematic hierarchy and repeated idle zero power | Accepted. Current bays rendered all usable power values, including 0 MW in idle bays. | Idle zero power renders Idle; nonzero or running source power remains visible. Forebay gets illustrative water fill; static manifolds use muted stroke/fill tokens. Geometry and layout remain schematic, with review information in the header tooltip. `StationSchematic.tsx` / CSS. |
| B1, pump captures 04–06 and duplicate provenance | Accepted as a capture/evidence defect and for duplicate provenance. Rejected as current-source bugs for zero-alarm chips, Read at, closed-accordion summaries and absent numbered circles: those were already removed/gated/present in the supplied source. | Removed desktop provenance output and phone clock caption; full source/time/clock facts remain in the existing schematic information tooltip. This task captures 04–06 from the frozen final source as part of the 18-view run; all three final pump views were captured and inspected. `PumpMimic.tsx`. |
| B2a, raw degC and equal min/max range | Accepted. `valueText` appended raw units and `summarize` always emitted low–high for groups of three or more. | `valueText` and grouped summaries use the existing lossless `unitLabel`; equal endpoints render once. The central formatter now accepts `m^3/s` as the same m³/s typography. `mimic/format.ts`, `MimicReadings.tsx`, `performance/format.ts`; focused unit regressions. |
| B2b, cusec versus m³/s and Idle plus 0.42 rpm | Rejected as evidence of fabricated or inconsistent quantities. Unit pump tags bind source cusec; station totals explicitly declare m^3/s and the backend `services/mimic.py:total_value` converts reviewed contributors into the declared total unit. Pump status and shaft-speed are independent supplied readings; replacing the source 0.42 rpm with zero or changing Idle would invent a fact. | Retain measured magnitudes and source units, label them consistently wherever each quantity appears, and normalize typography only. Synthetic layout fixture explicitly exercises 0 cusec discharge and 0 m³/s station flow. No frontend conversion or inferred state was added. |
| B3, accordion name hides verdict and numbered status dots | Accepted. Current Accordion.Control had `aria-label={section.title}`, hiding the child number and verdict from its accessible name; numbers used the status badge wrapper. | Removed the overriding name; native child text now exposes number, title, Critical/Warning/Normal. Numbers use Mantine Badge. `MimicReadings.tsx`; focused spec checks Normal and Critical accessible names. |
| B4, drawing sizing, muted hierarchy, marker contrast, spatial order | Accepted for the document-offset-based height clamp, flattened tokens, sequential split and marker colours. The report's precise explanation that scrolling alone changes `getBoundingClientRect().top + scrollY` is inaccurate: that sum is the document position. The underlying viewport/document coupling and arbitrary clamp were still unnecessary and were removed. | The owner follow-up supersedes the initial 500px cap: SVG occupies a full-width row and scales with viewport/container size, independent of scroll position; compact spatial callouts sit below it. Marker scaling alone observes the rendered SVG. Callouts partition at native x=550 and sort by native y/x, preserving catalogue numbering. Muted ribs/surfaces use theme tokens, unassessed markers are neutral with text-colour numerals; assessed markers use white Critical numerals and black Warning/Normal numerals in light mode for contrast; dark mode uses dark numerals. `UnitSchematic.tsx`, `UnitMimic.module.css`, `MimicParts.tsx`. Resize-while-scrolled and spatial-order assertions added. |
| B5, min/max sensor bars, duplicate callout prose, dark borders | Accepted. A 0.3°C difference could fill the whole decorative min/max scale; repeated sensor names were visibly truncated. | Callouts show compact per-unit ranges; full sensor names/values remain in the shared tooltip and expanded tables. The Opus follow-up removes all sensor strips, including the initially corrected equal-height condition marks. Actual verdicts and alarm evidence remain. Dark borders use dark-0 rather than dark-2. `MimicParts.tsx`, `UnitMimic.module.css`; unit and browser regressions preserve unavailable/actual-breach facts and require no sensor strips. |
| B6, hardcoded process dl and lost focus on callout selection | Accepted. Flow direction and Communication status were always missing literals; polling metadata duplicated error/disabled banners. Callout selection scrolled without moving focus. | Removed the entire hardcoded process dl. Opening a part waits for the React update, focuses its Accordion.Control without scrolling, then scrolls the target into view respecting reduced motion. `UnitMimic.tsx`; browser assertions check focus and expanded state. |
| Top 1, shared missing-value and tooltip primitive | Accepted. Mimics had their own missing implementation. | All mimic MissingValue consumers now import `components/items/ValueTooltip`; deleted mimic/MissingValue.tsx and its CSS. Present point/callout tooltips use shared ValueTooltip. The accordion tooltip keeps its native control child, preserving its semantic name; there is no role=img tooltip trigger. |
| Top 9, redundant PointTable tab stops | Accepted. Present numeric values and timestamps duplicated adjacent source evidence behind extra button stops. | Numeric values are plain aligned text, translated source-status codes retain a tooltip, timestamps are semantic time elements, and reading-name tooltips include pointer/time/clock/age/unchanged evidence. Missing values retain accessible shared tooltip buttons. `MimicReadings.tsx`. |
| Real schematic ARIA audit | No new role defect found in the active unit drawing. Its role=img wraps nonfocusable SVG geometry/pointer markers; equivalent named HTML callout buttons are siblings outside the SVG, so no focusable child semantics are hidden. The station SVG retains its named keyboard bay controls, verified by direct role queries and keyboard activation. | Preserve the static unit image role and the external semantic controls. Focus transfers to real accordion controls. |
| Empty threshold sections | Accepted as redundant normal-state prose, per Section 3. | Omit station and pump Threshold alarms headings and no-active-alarms sentences for known-empty responses. Active rows/heading, missing alarm scope/details, failed reads and disabled polling remain explicit. `PumphouseMimic.tsx`, `PumpMimic.tsx`. |

### Health and shared semantics (11–13)

| Finding | Disposition and verified evidence | Implementation/files |
| --- | --- | --- |
| D1 / Top 1: focusable `role="img"` tooltip targets | Accepted with qualification. `SignalReading.tsx` did use a focusable `Text role="img"`. Present classified-state labels already contained Normal/Warning/Critical through `conditionText`, so the report's claim that every state word vanished is overstated. The graphic role and duplicate accessible naming were still inappropriate. | One shared `components/items/ValueTooltip.tsx` plus CSS supplies a native button preserving the visible child's accessible name and Mantine tooltip description. `MissingValue` renders only the em dash, with optional field context and the reason as its accessible name. Health consumes these directly; the local `SignalTooltip` implementation is removed. Machines, Parts and mimics use the same exports. |
| D1 / Top 3: oversized summary hides all readings | Accepted. `HealthSummary.tsx` rendered a second column with a duplicate critical-coverage explanation alert. `MachineHealthPanel.tsx` separately rendered the zero-anomaly heading and bordered empty list; a container query kept the sections stacked at common widths. | Critical-coverage explanation is a hover/focus/touch tooltip on the existing coverage text; its designation gaps and exact reasons remain available. Known-empty anomaly lists become the supplied `0 active anomalies` fact. Failed/incomplete anomaly reads retain their explicit visible alerts. Summary spacing is compact, and active/failure cases retain the anomaly section. |
| D2 / Top 3: stale non-offline states become an em dash | Accepted. The old state conditional explicitly combined `signal.state === 'unknown'` with `signal.stale && signal.state !== 'offline'`. | `SignalTable.tsx` renders a `Stale` status badge for stale non-offline signals. Offline still says No current data; genuinely unclassified current signals use the shared missing-value button. Last-known numbers and source freshness metadata remain unchanged. |
| D2: unavailable anomaly counts are an unlabelled floating dash; empty count group | Accepted. The old all-zero counts branch returned null inside an otherwise unconditional flex group; the unavailable branch had no visible field context. | Only positive severity count groups render; unavailable counts have `Active anomalies:` beside the shared dash, never zero. A known-complete zero list renders the zero count. |
| D3: phone Refresh wraps below Current condition | Accepted. The header Group could wrap and nested the refresh action with the review link. | Desktop uses a nonwrapping title/action row. Phone retains accessible Current condition and Signals headings without repeating them visually; Refresh shares the Search/filter row. `HealthSummary.module.css` keeps the state beside the assessment, and zero anomalies plus HH:mm:ss/age share one row. The timestamp tooltip supplies the complete formatted instant and validated clock context, including the browser fallback when no plant zone is supplied. |
| D3: phone Value label/value alignment | Accepted. The phone CSS explicitly justified the value to flex-end although its label was left-aligned. | `SignalTable.module.css` places the name and primary value on one phone row, followed by the state; Details reveals Limits, Observed, Quality and Trend. Desktop numeric readings remain right aligned. |
| Top 10: inconsistent phone breakpoint and density | Accepted for the signal controls and shared shell. SignalTable used 47.99em while the other owned panel modules used 48em. All three filters plus the table/cards toggle stacked ahead of readings. | The owned breakpoint is 48em. Search remains visible; a 44px Signal filters button discloses Component filter, Order by and table/card mode on phone. Desktop controls remain on one aligned row. |
| Consistency: date/time formatter | Accepted. Health summary/common freshness used the browser-only formatter while mimic used the plant-clock formatter. The Health summary API provides no plant zone, so inventing one would be wrong. | Health summary and common FreshnessText/ObservedAt delegate to Timestamp, which uses `formatInstant` with the inherited validated `useDisplayClock`; absence retains the explicit browser-clock fallback in the tooltip. A unit verifies Riyadh plant time with a different browser zone. O27 current presentation and just-now text are preserved. |
| Section 3: critical explanation, clock caption and empty anomaly section are redundant | Accepted. Verified the actual JSX, independently of the old review screenshots. | Explanation and clock context move to accessible tooltips; the empty section is folded into the summary. Current failures, active alarms and actual designated evidence gaps remain explicit or inspectable. |

### Machines, locations and Parts (07–10, 14–18)

| Review finding | Disposition and source evidence | Implemented fix / files |
| --- | --- | --- |
| C1 / Top 4: phone checkbox input was 44px square | Accepted: phone CSS explicitly assigned 44px to inner and input. | `locations.module.css` keeps a 20px native visual input inside a 44px label. `LocationWorkspace.tsx` uses a shared local selection wrapper with `htmlFor` and stable `useId`, so clicking the label edge also toggles the native input. Focused spec measures both dimensions, edge-clicks the label and verifies keyboard Space. |
| C2 / Top 1: priority and condition tooltip wrappers overwrite state labels | Accepted: generic Box wrappers set `aria-label` to reason alone. | `LocationWorkspace.tsx` uses shared `ValueTooltip`, whose native button name comes from visible `High`, `Low`, `Warning`, etc. Tooltip supplies the reason through its description; a present condition without a reason remains a plain badge. Local `MachineMissingValue` implementation removed in favor of shared `MissingValue`. |
| C3 / Top 7: compound Alarms/Work status columns drift and collide | Accepted: widths were 40/18/15/17/10 with Alarms/Work groups right aligned. | Machine column allocation now 34/18/16/18/14. Condition, Alarms, Work and Criticality align at their left edges, including mixed count/priority groups. Numeric-only Parts columns remain right aligned. Phone fact headings and values share their left edge. |
| C3 / section 3.9: failed reads display multi-line prose | Accepted: unavailable cells printed full reason inline. | Visible compact `Read failed` badges distinguish failure from missing data; shared keyboard/touch tooltips retain `Condition could not be checked` and `Open work could not be checked`. Tests verify both visible failure and focused explanations. |
| C4 / Top 7: location tree names truncate and duplicate type/code cues crowd the row | Accepted: `.nodeName` used nowrap/ellipsis while adjacent kind/code fields consumed space. | Removed duplicate kind/code cues from tree rows (retained in Location details), removed 4rem cue clipping, and allowed long selectable names to wrap. `LocationBrowser.tsx`, `locations.module.css`. |
| Overall criterion 4, other alarm counts beside Normal condition | Rejected as a state contradiction. `MachineAlarmBadges.tsx` computes the recorded open count remaining after known critical/warning counts; a current signal condition and retained active alarm occurrences are independent. Removing these counts from Normal rows would conceal real active alarms. | Keep the supplied count under the Alarms column and its neutral information icon. Missing severity retains explicit unavailable wording; no missing severity is inferred as warning. |
| C4: Location details lacks aria-expanded | Rejected as a current-code claim: the source already had `aria-expanded={detailsOpen}`. | Preserved that state and changed the button from compact/subtle to default/xs to align the surrounding controls. |
| C5 / Top 4: Warning descender clipping | Accepted: summary buttons used compact-xs. | `MachinePageSummary.tsx` uses xs buttons while preserving phone 44px targets. |
| C5: output containing buttons violates HTML content model | Rejected as stated: buttons are phrasing content, so their presence alone does not prove an invalid output content model. The user's explicit instruction independently requires removing buttons from output. | Changed the summary wrapper to a regular Group/div as requested. |
| section 3.10: duplicate loaded machine count and two secondary identity lines | Accepted: toolbar count duplicated page-summary total; serial/manufacturer/model and location were separate lines. | Positive counts appear once in `MachinePageSummary`; the zero-result total remains because no page summary exists at zero. Present serial and location share one line; unassigned placement retains an accessible missing dash. Manufacturer/model remain available in the native machine detail route. |
| E1 / Top 1: Parts missing and Fit basis wrappers use role=img | Accepted: `MissingPartFact` and fit wrapper used `role='img'`. | Removed the local missing component entirely; `ComponentFacts.tsx`, `MachineComponentTable.tsx` and `ComponentSlotDetail.tsx` consume shared `MissingValue` / `ValueTooltip`. Fit accessible labels retain `Draft` or `Verified` first. |
| E1 / Top 8: standalone registry row consumes phone height | Accepted: registry link was a separate Group above the table action row. | `InstalledPartsPanel.tsx` passes the registry destination into the native table. `MachineComponentTable.tsx` renders the concise `Equipment registry` navigation link via supported `tableActions`, beside existing table controls. The shorter action fits the phone toolbar. |
| E1 / Top 8: station Equipment phone cell leaves half-row hole | Accepted: full-span CSS covered name, part and source but not machine_name. | Added full-span machine_name cell in `MachineComponentTable.module.css`. After inspection of the fresh native phone render, secondary 3D position, fitted identity and Source moved into a native per-row Details disclosure. Name/catalogue/fit/work/stock remain primary and all secondary facts stay keyboard accessible; desktop columns remain intact. |
| E2 / Top 8: Source label is long and import date month-first | Accepted: label said Equipment register import while navigation says registry, and custom Intl used locale-dependent ordering. | Short `Registry import` in the cell. Keyboard/hover tooltip retains raw source, original imported timestamp through app `formatInstant` and the inherited display-clock label. No invented dates or altered source units. `ComponentFacts.tsx`. |
| E2 / Top 8: Parts column allocations sum to 105% | Accepted: station values in source sum to 105. | Station final allocation 8/16/10/10/16/8/12/10/10 sums to 100 and gives the stock header enough room. Pump redistributes hidden Equipment space into Component and Catalogue part; visible columns normalize for stock permission and phone disclosure mode. Explicit minWidth=0 prevents native default 100px minima from overriding these narrow allocations. Native right textAlign remains on both numeric columns. |
| section 3.11 / Top 8: internal codes repeat under every Component | Accepted. | Removed table secondary code. The existing slot drawer retains the actual code. Focused selectors now find rows by visible component button name; tests prove codes are absent from table and drawer still opens. |
| section 3.11: slot drawer repeats Assignment verified/Slot assignment not verified beside the badge | Accepted. | Fit basis uses one state badge with the explanation in shared ValueTooltip. Missing drawer facts also use shared MissingValue; recorded zero catalogue stock stays numeric 0. `ComponentSlotDetail.tsx`. |
| Top 8: hardcoded fit/link hex colours | Accepted: four literal hex values were present. | Replaced link colors with Mantine blue tokens and fit text with the normal Mantine text token, preserving readable contrast on yellow/green light badge surfaces. Tooltip buttons inherit normal text color instead of being styled as navigation links. |
| Top 10: location total touch targets and placement spacing | Accepted. | Phone total tooltip targets get 44px minima; compact placement text keeps the same physical location and native controls, with small line-height/margin in `MachineLocationCard.tsx` and location CSS. Shared shell/tab repairs are described below. |

### Shared shell / Top 10 closure

| Finding | Disposition and source check | Fix / files |
| --- | --- | --- |
| Top 10: selected phone Health/Parts tab clipped or off-screen | Accepted: horizontal Tabs.List did not bring its active tab into view. | `components/panels/PanelGroup.tsx` centers the active tab by scrolling the strip only; 44px targets, edge fade and thin scrollbar in `PanelGroup.css.ts`. No vertical page jump. |
| Top 10: redundant panel headers and phone spacing | Accepted: native shell chrome plus duplicated Health/Parts headlines, panel padding and placement gap consumed the fold. | `MachineDetail.tsx` disables duplicate Health, Parts, Equipment view and mimic panel headlines; phone PanelGroup padding/gaps and `operations.module.css` spacing are compact. Native title, breadcrumbs, QR, assistant, create/start actions, routes and permission checks remain. |
| Top 9: focus ring for shared phone targets | Accepted: explicit class focus styling was absent. | `operations.module.css` uses Mantine primary colour and radius tokens for a visible focus ring. |
| All Top 10 fixes | Accepted except the qualified source-unit, already-removed-caption and document-position claims above. | Top 1 shared semantics; Top 2 provenance/process removal; Top 3 compact Health/stale; Top 4 checkbox/filter controls; Top 5 pump scale/units/order/contrast; Top 6 station strip/hierarchy/scroll; Top 7 Machines/tree; Top 8 Parts; Top 9 focus/tab count; Top 10 responsive shell. |

Section 3's eleven caption findings are addressed in the same rows: already
removed closed-accordion summaries/zero alarm/read-at text are not reintroduced;
provenance and clock explanations use tooltips; callout summaries use compact
values; the hardcoded process block and empty threshold/anomaly boxes are gone;
failed reads stay visible as Read failed; duplicate counts/identity lines and
slot-code/fit captions are removed or placed in the native detail destination.

Phone primary cards lead with useful facts; existing accessible disclosures retain secondary fields. Health uses its native Details arrow, Parts uses a keyboard-operable Details disclosure. All inherited native actions and the superuser warning remain. No field is inferred or manufactured to improve layout.

### Owner pump screenshot follow-up

The owner supplied a live-app screenshot showing the previous small drawing between wide callout columns. Accepted: the drawing needed to dominate the panel. `UnitSchematic.tsx` and `UnitMimic.module.css` now reserve a full-width SVG row; desktop height uses the viewport and container width, with callouts in compact spatial groups below. Focused assertions measure painted geometry as well as the SVG box, requiring at least half the content width and 60% of desktop viewport height. Phone retains a width-fit drawing and readable markers.

`mimic/format.ts` suppresses a negative sign only when the formatted numeric magnitude is zero; real negative pressure remains negative. `unitLabel` formats degC/percent/mH2O through °C/%/mH₂O. Known `/pd/{pump}/dv` labels use the reviewed Discharge flow label; raw pointers remain available for traceability. No unknown key is given an invented meaning. The checked-in pump SVG's colour fallbacks now use Mantine tokens. Units and summaries have focused regressions.

## Runtime

Only task-owned Linux Chromium container `r6-uir2-browser-3012` on WS3012
and fixture Vite harness15250 are used. All browser APIs are synthetic
intercepts; no Azure or plant connection, database or backend server is used.
The task stops its own container and harness before handoff; the final cleanup result is recorded below.


## Gemini checkpoint verification (before the second review)

Checks use the worktree's pinned packages and the scratch browser configuration
`data/devscratch/ui-r2/browser.config.mts`. It connects only to owned WS3012,
reuses the managed fixture harness15250 and defaults to Asia/Riyadh; the Health trend spec explicitly overrides its browser clock to UTC.
Browser commands run sequentially with unique `AIMMS_SOURCE_REVISION` values so
Vite serves the current transforms. These historical focused checks preceded the Opus repairs. Current-source
results are recorded separately below; the coordinator retains the full Mac
suite replay.

| Check | Actual result | Evidence under `data/devscratch/ui-r2/checks/` |
| --- | --- | --- |
| Mimic layout and actions, both themes, desktop/tablet/phone | 27/27 pass; painted width/height, marker/callout order, resize while scrolled, keyboard focus and normalized units | `owner-mimic.log` |
| Machines/location/Parts focused selection | 23 unique passing cases, 1 intentional desktop skip for a phone-only target test; initial 12 failures all covered by the 14-pass corrective run | `tables.log`, `tables-corrective.log` |
| Health selected condition/native/graph cases | 26 unique passes: 24 corrective plus 2 final alignment replays | `health-corrective.log`, `health-alignment.log` |
| S8 selected states/provenance/O27 cases | 26 unique passes: 24 initial plus 2 exact-tooltip corrective cases | `s8-focused.log`, `s8-last.log` |
| Native schematic viewport matrix | 1/1 pass, resizing through 1100×800, 1440×900, 1920×1080 and 390×844 | `s8-matrix-corrective.log` |
| TypeScript: app and all 8 `tsconfig.s*.json` projects | All 9 pass | `complete-types-results.json`; final test-only S8 replay in `review-types-s8.log` |
| Changed units | 56/56 across 4 files | `complete-units.log` |
| Lingui extract and compile --typescript | Both pass; all 117 locale files restored byte for byte | `review-lingui-results.json`, `review-lingui-extract.log`, `review-lingui-compile.log` |
| Prek on every existing changed/new file | Pass, after applying hook formatting | `prek-source-clean.log` |
| Tracking validator and whitespace | Pass at source checkpoint; repeat after final report | `tracking.log` |
| Historical capture checkpoint | Superseded after the second review; current 18-view evidence is below | Current final PNGs are never taken from this checkpoint |

Tests now assert native tooltip buttons and their visible accessible names,
expanded secondary facts, exact full provenance/plant-clock timestamps and
preserved verdict names. Removed captions are tested as absent; their factual
content is checked in the intended tooltip or detail destination. Existing
state, permission, chart-gap, navigation and disclosure assertions remain.
The new phone alignment check exposed a real 4px Mantine input-wrapper height
mismatch; the wrapper now uses the same 44px height as its input and Refresh.
The first table replay exposed real link contrast inherited from Mantine's
Anchor rendered as a button; theme-aware blue tokens fix it. Tooltip-transition
selector failures were corrected by identifying the intended named tooltip,
without relaxing its factual assertions.

The coordinator's intentional browser shutdown interrupted an early Health
run. Its closed-browser failures are retained as interrupted-run evidence and
are not product failures or passing checks. Corrective logs above cover the
selected cases. No deployment, live data or plant mapping acceptance is claimed.


## Second review — Opus

Read `scratchpad/opus-r1.md` completely after the coordinator supplied it.
It reviews the same older screenshots and source checkpoint, not the current
round-2 source. Every finding is checked again, including the five blockers.
The owner's later direction controls conflicts: O27 retains recorded-month live
presentation; the dominant desktop pump drawing supersedes the suggested 620px
cap; a clearly scrollable phone station is permitted; failed reads retain a
visible label; missing facts alone use the quiet dash.

The first 18-view capture run passed and every image was inspected. It exposed
clipped Catalogue stock headers, corrected before the fresh final run. Those captures and the
pre-Opus source checkpoint are superseded by the final recapture; they are not
reported as evidence for the subsequent source. Supplemental abnormal-state
captures exercise the repaired state, failure, coverage and contrast paths.

The following are source-verified dispositions. Current-source verification and capture results are recorded below; fixture checks do not establish deployment or product acceptance.

### Station and pump findings

| ID | Disposition and source evidence | Final repair / limit |
| --- | --- | --- |
| S1 | Accepted. A usable power reading previously replaced the station bay's state word; alarm circles used the same shape. | Every bay now has its own visible Running/Idle/Fault/Stale/Unknown word, independently of its optional power line. Critical alarms are diamonds; warnings are triangles; both retain the accessible severity and a contrast-aware exclamation. `StationSchematic.tsx`. |
| S2 | Qualified. Missing affordance was valid in the original source, and round 2 already adds arrows, a swipe cue, shadows and keyboard scrolling. Rejected the mandatory tile-grid/no-sideways-scroll solution because the owner explicitly allows a clearly scrollable readable row. | Fixed-size 88-unit bays replace character-count text-width guessing; native labels stay readable. Focus scrolls the selected bay into view and phone tests reach P14. `StationSchematic.tsx` / CSS. |
| S3 | Accepted. Absence of active alarms must not imply complete threshold coverage. | Header exposes the actual station-wide `unconfigured_thresholds` count. `No active alarms` is only stated when the station alarm and every bay alarm are explicitly null and no active supplied anomaly stands. Unknown counts remain a dash with a reason, never zero. `MimicAlarmCoverage.tsx`, `StationSchematic.tsx`. |
| S4 | Accepted for the mismatched panels and fixed dark alarm/focus colours. | Station and pump use the same scoped `--aimms-panel` token. Actual bay/alarm/marker/focus edges use theme-aware semantic tokens with fallbacks; assessed marker numerals use white Critical and black Warning/Normal numerals in light mode; dark mode uses dark-9. Supplemental abnormal-state captures measure actual marker text/edge contrast and are inspected below. `PumphouseMimic.tsx`, `UnitMimic.tsx`, station/unit CSS, `MimicParts.tsx`; the shared `MimicSchematic.tsx` palette uses the same tokens. |
| S5 | Already fixed by round 2. | One five-metric strip, identical card/value styling, totals inside the strip. No second totals component below the drawing. |
| S6 | Accepted. | Station legend keeps state/alarm names and swatches without counts. Every bay keeps its state word; power is only shown for a running bay or a supplied nonzero reading. `PumphouseMimic.tsx`, `StationSchematic.tsx`. |
| S7 | Accepted. The earlier repair moved the source distinction to the value tooltip but still lacked a visible calculated cue. | Derived totals alone show Σ plus accessible Calculated total text; measured totals show no calculated cue. Labels remain plain text. `StationSchematic.tsx`. |
| S8 | Accepted. | Station schematic is now a real `Title order={4}`, matching the station Threshold alarms section. `StationSchematic.tsx`. |
| S9 | Partly stale and partly accepted. The info trigger was already next to the title after round 2, rather than at the far right. The old missing-mapping sentence was unnecessary. | Kept the adjacent trigger; tooltip now states Reference schematic / physical arrangement unverified / awaiting plant review, plus provenance. Named metric Papers are semantic sections rather than labelled generic divs. |
| S10 | Qualified. The water fill is intentionally static illustrative structure, not a measured level. Physical arrangement is explicitly unverified in the accessible info tooltip. | Retain validated schematic geometry and its numeric level metric. Rejected inventing an outlet/flow arrow or an extra illustrative caption: no source fact supplies a flow direction, and the owner forbids unnecessary captions. |
| S11 | Rejected as a current-source finding. | `.bay` already has pointer cursor; hover/focus outlines and `aria-pressed` already exist and remain. Focus/selection tests retain these facts. |
| S12 | Stale screenshot issue, not a current-source bug. | Response time and closed summaries were already removed. Final-source recapture is recorded below. |
| P1 | Accepted. | Pump header now shows its selected-unit readings without limits. This is computed only when every supplied selected point has a boolean `thresholds_configured`; empty/unknown scope returns null. It does not relabel the station-wide aggregate as pump coverage. No-active wording is restricted to a mapped explicitly alarm-free bay with no supplied alarm rows. `MimicAlarmCoverage.tsx`, `PumpMimic.tsx`, `UnitMimic.tsx`. |
| P2 | Accepted. | `UnavailableReading` distinguishes failed quality from absent/unpublished facts. Alarm values retain a supplied last number/unit with the shared Bad quality badge; absent failed readings show Read failed. Unknown limits remain a plain dash with their reason, because missing configuration is not a failed read. Expanded point tables, station/unit metrics and callouts also keep visible failure vocabulary. `MimicValue.tsx`, `AlarmTable.tsx`, `MimicReadings.tsx`, station/unit components, `MimicParts.tsx`. |
| P3 | Accepted; first-pass marker repair retained and strengthened for abnormal states. | Neutral circles have explicit high-contrast outlines and readable text; semantic critical/warning/normal fills adapt by scheme and use contrasting numerals. `MimicParts.tsx`. |
| P4 | Accepted for count-based ordering; already repaired spatially. | Native x/y decides side/order, with fixed catalogue numbers. Owner sizing follow-up puts both compact groups below the dominant full-width drawing. Rejected returning to narrow flanking columns. `UnitSchematic.tsx`, unit CSS. |
| P5 | Accepted and already repaired. | Compact per-unit ranges have no two-line clamp and do not repeat full part names. Full individual sensor facts remain in the tooltip/expanded native table; values wrap naturally rather than truncate mid-value. |
| P6 | Accepted for raw units, equal ranges and repeated labels; already repaired. Rejected invented fixed decimals because the mimic point contract supplies no per-signal precision. | Shared unit typography renders °C and m³/s; equality ranges collapse; source units/magnitudes remain unchanged. `mimic/format.ts` also repairs negative zero, percent and reviewed reading labels. |
| P7 | Accepted. | Deleted the entire sensor-strip implementation, including strips on configured readings. Browser assertions now prove no bars for either configured or unconfigured groups, while retaining actual critical verdict/outline and visible Critical text. `MimicParts.tsx`, CSS, focused specs/units. |
| P8 | Already fixed by round 2. | Permanently missing Flow direction / Communication status and normal Polling row are absent; actual disabled/error alerts remain. |
| P9 | Accepted. | Distinct Pump and Readings legends explain operating fill/hatch separately from numbered reading verdicts: Critical, Warning, Normal, No limits, Unknown and No readings. Unknown reading assessment is a hollow solid marker; absent mapped readings use a dashed ring. `UnitMimic.tsx`, unit CSS, `MimicParts.tsx`. |
| P10 | Partly accepted. Overriding accessible names and status-dot number badges were already fixed. Filter chrome could be smaller. Rejected replacing the working grouped drill-down with a new All readings table: that is an unnecessary workflow redesign, and the native groups preserve part-to-source investigation and alarm verdicts. | Filter now has search icon, placeholder, accessible name and bounded width. Native accordion numbers/visible verdict names, focus transfer, plain numeric cells and shared missing/failure reasons remain. Clock/quality/missing tooltip controls do not add repeated row tab stops. `UnitMimic.tsx`, `MimicReadings.tsx`. |
| P11 | Accepted for duplicate timestamps/provenance, zero severity counts and redundant info icons; most were fixed in round 2 or the consolidated shared badge. | Active summary now prints only nonzero critical/warning counts, with unknown severity kept explicit. Header has one shared timestamp. Retained Pump unit identity because the same header identifies a selected pump inside the station page. Native Mantine inline alignment is not itself a defect. `PumpMimic.tsx`, shared badges, `FreshnessLabel.tsx`. |
| P12 | Accepted. | Pump sizing is CSS-only and preserves the owner's large drawing. The marker-scale measurement effect now depends only on unit and actual ResizeObserver/window resize events, not `generated_at` or each poll. Rejected the suggested 620px cap and mandatory HTML marker overlay because they contradict owner sizing and unnecessarily change validated native coordinates. `UnitSchematic.tsx`; `MimicSchematic.tsx` also removes the legacy document-offset fitting path. |
| P13 | Rejected as evidence of a quantity error. | Source pump flow uses cusec; the backend explicitly converts reviewed station totals into declared m³/s. Source units remain consistently labelled per quantity. The requested cusec-to-ft³/s typography is an optional alias, not evidence of a quantity error; this repair retains the declared source unit consistently. |


### Health findings

| ID | Disposition, evidence and remedy | Files |
| --- | --- | --- |
| H1 | Accepted. Machines and Health could imply different assessment confidence. One canonical badge now distinguishes full Normal (check), exact partial Normal · 2/6 (half-circle), and Normal · coverage unknown. The list API has no evaluated count, so stale/quality counts never become an invented ratio. | health/common.tsx, health/StatusBadge.tsx; locations/LocationWorkspace.tsx |
| H2 | Accepted. The first round removed duplicate coverage alerts; this follow-up puts exact coverage once in the partial badge. Critical-designation bounds remain in the existing text/tooltip. A failed coverage read remains visibly Read failed. | health/HealthSummary.tsx |
| H3 | Rejected as a finding against the reviewed revision. Existing preferJustNow already required a non-future, non-stale observation less than 60 seconds old. Health fixtures fix their clock on 18 Oct; mimic fixtures use 22 Oct. Different fixture dates do not prove false freshness. The new Timestamp integration briefly lost the non-stale guard; the unchanged focused expired-row assertion caught that real regression. Restored the guard and added a unit requiring Stale · 20 s old · freshness budget 10 s rather than just now. Preserve O27. | health/common.tsx, monitoringComponents.test.ts; tests/s3/healthFixtures.ts, pui_health_condition.spec.ts |
| H4 | Accepted; visible clock captions were already removed. One Timestamp now supplies plant-clock formatting and the full instant/clock tooltip. | health/Timestamp.tsx, Timestamp.module.css, common.tsx, mimic/FreshnessLabel.tsx, mimic/observedTime.ts |
| H5 | Accepted and repaired in the first round. Compact summary/cards/toolbars remain; the focused browser test still requires the complete actual value cell above the phone fold. Fresh captures are required after this follow-up. | health/HealthSummary.tsx, MachineHealthPanel.tsx, SignalTable.tsx, SignalTable.module.css |
| H6 | Accepted with qualification: old condition labels already included Normal/Warning/Critical, so not every state word disappeared. Image roles were already removed. Repeated fact targets now leave sequential Tab order; the existing Details action supplies the combined fact tooltip on focus. The test requires only Details and Inspect actions in a row's Tab order. | components/items/ValueTooltip.tsx; health/SignalTable.tsx, SignalReading.tsx, SignalTrend.tsx |
| H7 | Accepted. A known blank-unit value stays visible as 1834 (no unit); explicit dimensionless unit 1 does not repeat. Missing/invalid/bad-quality observations remain distinct. No undocumented fixed measurement precision is invented. | health/readingPresentation.ts, SignalReading.tsx, readingPresentation.test.ts |
| H8 | Accepted. Repeated No limits set chips become quiet plain None, with engineering context on Details. Retain the native comparison column for mixed configurations; a new column-visibility system is unnecessary. | health/SignalTable.tsx |
| H9 | Accepted. Backend MAX_ANOMALY_PAGE is 200 and the read is sliced there. Restore a conditional first-200 notice; the focused 200-returned/201-summary test also requires the incomplete-association warning and rejects a false zero. | health/MachineHealthPanel.tsx; backend/machine_health/api.py read-only evidence; tests/s3/pui_health_condition.spec.ts |
| H10 | Accepted. Active-tab scrolling is repaired and selected Health/Parts tabs are fully visible in the final phone screenshots. | components/panels/PanelGroup.tsx |
| H11 | Accepted. Scoped AIMMS dimmed tokens are corrected. Native theme behavior is retained; actual browser contrast checks are recorded below. | assets/operations.module.css |

### Shared recommendations and qualifications

The shared fact implementation is `components/items/ValueTooltip.tsx`, exporting
native `ValueTooltip`, quiet `MissingValue` and visible `FailedValue`. The review's
sample `role="img"` missing wrapper is explicitly rejected: it repeats the
accessibility defect the owner requires removing. Unknown reasons remain
`Reason not supplied`, never an invented plant or mapping failure.

`health/StatusBadge.tsx` is canonical; the common compatibility wrapper delegates
to it. Native number badges remain numbers, not operational status badges.
`health/Timestamp.tsx` and `mimic/observedTime.ts` supply consistent validated
plant-clock formatting and explicit browser fallback. Real source clock, age,
quality and pointer evidence remain inspectable. O27 is unchanged.

AIMMS semantic/focus/panel tokens are scoped in `operations.module.css`, using
Mantine tokens. No global theme rewrite, extra integration, invented process
arrow, forced measurement precision, or automatic hiding of real facts based on
synthetic sample repetition is introduced. Native detail/header actions and
permissions remain. The required source code stays uncommitted; the final
source/file hash manifest supplies evidence stronger than stamping an obsolete
commit hash on changed-code screenshots.

### Machines, locations and Parts findings

| ID | Disposition and verification against source | Current fix / preservation |
| --- | --- | --- |
| M1 | Accepted. Mantine Text inside the Condition/Alarms tooltip overrode the th font. | `LocationWorkspace.tsx` uses `Text inherit component='span'`; owned `.machineTable th` and `.help` share xs/600. Final visual inspection also caught low-contrast native dimmed headers; owned gray-7/dark-1 tokens and a final capture contrast assertion repair and verify them. |
| M2 | Accepted honesty issue; qualified proposed `Normal · 2/6`: the list API supplies freshness flags but no assessed/mapped threshold counts. Freshness alone cannot establish complete assessment. | Shared HealthStateBadge renders Normal · partial for actual stale/degraded/incomplete source coverage, and Normal · coverage unknown for otherwise fresh Normal when assessment counts are absent. No count is invented. The name-link tooltip preserves actual coverage reasons; keyboard test proves the unknown-coverage explanation. |
| M3 | Accepted failed-read ambiguity; rejected treating successfully unmapped data and offline current-data loss as identical failures. | Shared FailedValue visibly says Read failed for failed Condition/Work reads. Successfully unmapped condition stays a plain dash with its real reason; offline stays No current data. These are different provider states. |
| M4 | Accepted. Summary typography/actions did not communicate condition severity strongly enough and count grammar was verbose. | `MachinePageSummary.tsx` uses canonical StatusBadge with red critical/failure and yellow warning/alarm cues, glyph plus word, neutral zero counts, a named Condition totals fieldset, Lingui plural count, This page only when paginated, and In alarm N. Duplicate positive toolbar count remains removed; zero-result count remains necessary. |
| M5 | Accepted, already corrected by the Gemini pass. | Work count and priority now share one native navigation link. Accessible name contains the actual count and priority; the tooltip says Highest open work priority: High/Low. No replacement generic aria-label. |
| M6 | Accepted. Internal Checkbox CSS overrides existed. | Native public `size='20px'` and `classNames.input`, inside an explicitly associated 44px label. No Mantine private class selector. Spec measures 20px visual, 44px hit area, edge click and Space behavior. |
| M7 | Accepted for actual phone Filters and Refresh sizes. | Native h/size props make Filters and Refresh 44px on phone; table toolbar receives public controlSize=44. Focused phone checks measure those controls and the Work link. |
| M8 | Accepted layout defect; qualified site-removal proposal because one fixture page cannot prove all machines share placement. | Removed blanket minimum sizes on every focusable/link element. Count and priority stay one native link. Phone facts use one 2 × 2 grid with each label above its value; selection keeps a separate owned 44px label. One serial/site line preserves real placement when the list is not location scoped. |
| M9 | Accepted alignment concern, already corrected. Rejected the exact proposed 38/16/14/14/12 percentages because they sum to 94%. | Machine widths remain 34/18/16/18/14=100%; all compound status facts align left. Numeric-only Parts columns align right. |
| M10 | Accepted excessive summary height. | Native status actions form one non-wrapping horizontal scrolling row with no outer button border. Phone hit areas remain 44px. |
| M11 | Accepted off-switch contrast. | Public Switch track class adds light gray-6 / dark dark-1 border. Native Checkbox input border also uses theme tokens via its supported class slot. |
| L1 | Accepted invisible scope explanation. Rejected deriving physical-location totals by summing a filtered/paginated list: these API fields have different legitimate scopes. | A visibly named Location totals section explicitly says directly here / including sublocations and unaffected by list filters when filters apply. Machine count, open-work count and list rows preserve their distinct API values; per-fact tooltips explain the contract. No values are silently reconciled or invented. |
| L2 | Accepted mixed action treatment. | Refresh is a named native ActionIcon; text actions use consistent default/xs buttons and phone touch targets. Native shell actions remain available. |
| L3 | Accepted unnecessary description. | LocationBrowser uses the input placeholder Name or code; description removed, accessible field label retained. |
| L4 | Accepted, already fixed in the Gemini pass. | Location tree names wrap without truncation; duplicate kind/code cues are retained in Location details instead of competing inside the tree row. |
| L5 | Accepted grammatical/localization defect. | Lingui plural renders 1 open work order / N open work orders. |
| T1 | Accepted blocker. Source proved failed 3D/work/stock payloads were converted to the same dash as legitimate absence. | `MachineComponentTable.tsx` and `ComponentSlotDetail.tsx` use shared FailedValue for actual read failures. Known Not in model, no verified fitted identity, logical-group stock and role-limited fitted identity remain plain missing values with accurate reasons. Recorded zero stock remains numeric 0. |
| T2 | Qualified. This all-same/all-dash claim comes from synthetic page data; Source, position, fitted identity and equipment can vary on real or subsequent pages. Automatic data-dependent hiding would make facts disappear and vary table structure across pages. | Preserve native desktop columns and existing visibility controls. Phone primary name/catalogue/fit/work/stock remain visible; actual equipment identity spans the station row; secondary position/fitted/source remain available in native Details and the existing slot drawer. Draft is quiet gray outline. No factual column is hidden just because this fixture repeats it. |
| T3 | Accepted original percentage defect and actual Catalogue stock clipping, but the reviewed 105/102% calculation predates the current 100% allocation. | Station 8/16/10/10/16/8/12/10/10=100; pump 20/10/10/20/8/12/10/10=100; hidden-column redistribution/permission normalization retained. Both numeric headers remain full labels and right aligned. New native-shell desktop spec uses DOM text Ranges to verify every header glyph is inside its th, catching the real Catalogue stock clipping regression. |
| T4 | Accepted English-comparison/line-wrap defect; US date formatter had already been removed by Gemini. | `componentSource()` returns typed register/scada/manual/other/missing kind. Adapter centralizes legacy provider vocabulary; UI selects translations by kind. Registry import and the shorter SCADA mapping stay concise; the tooltip retains the full source. Tooltip retains actual raw source, valid imported instant and timezone via the shared app plant-clock formatter. Unknown custom source text remains actual text. Unit assertions cover each source kind, invalid/missing import time and preserved raw source. |
| T5 | Accepted vertical-border inconsistency, but rejected the claim Machines has no stripes: its native Table is already striped. | Added optional native InvenTreeTableProps.withColumnBorders, default true so other tables preserve behavior. Parts requests false, keeping its native stripes/row boundaries without private-class CSS overrides. |
| T6 | Accepted phone wasted space, addressed in the Gemini pass plus shared shell/tab repairs. | One full-width card divider, one-row toolbar, compact native Details disclosure. Final15 has one complete card ending ≈836px; final18 ≈807px plus the next name beginning. These are observations of the final Opus repair captures, not a claim that two complete cards fit. |
| T7 | Accepted. Private Checkbox/Anchor selectors and three important width overrides were present; literal fit/link colors were already replaced earlier. | All owned production CSS now uses owned classes, public Mantine classNames/size props, and theme tokens. Phone native columns receive width auto/minWidth 0, removing important overrides. Actual Anchor links and button components share owned `.link`; full contrast fix retained. Custom focus rules use the shared aimms-focus token with Mantine fallbacks. |
| T8 | Accepted, already fixed in the Gemini pass. | Short Equipment registry link uses native tableActions alongside existing controls in the same toolbar row. |


### Cross-cutting Q5 disposition

| Q5 item | Verified disposition and implementation |
| --- | --- |
| 1, failed/bad-quality versus missing | Accepted. Shared FailedValue renders Read failed or retained value + Bad quality. Actual mapped absence stays a dash. |
| 2, coverage/scope/cap notes | Accepted. Partial/unknown Normal is distinct; location totals show actual scope; mimic limits counts are at the header; first-200 cap is explicit only when relevant. |
| 3, invented fallback reasons | Accepted. Unknown gaps use Reason not supplied; actual source reasons remain. No omitted point is silently labelled Not bound. |
| 4, competing fact implementations | Accepted. All missing/failure consumers use the shared ValueTooltip exports. Domain adapters identify source state, rather than create another missing UI. |
| 5, ARIA naming | Accepted for generic wrappers/overrides. Native buttons retain visible names, metric Papers and location totals are named semantic sections, and accordion controls expose verdicts. The static unit SVG has no focusable tooltip child. |
| 6, excessive Tab stops | Accepted for repeated factual targets. Details/name-link/source paths retain keyboard explanations; necessary component/catalogue/stock navigation remains. |
| 7, dark contrast | Accepted for current semantic/marker/panel problems. Scoped Mantine tokens and contrasting numeral colours replace fixed dark-unaware values; actual abnormal-state contrast is measured below. Historical JPEG ratios are not asserted as measurements of current code. |
| 8, private CSS/important rules | Accepted. Public size/classNames/table options and owned classes replace Checkbox/Anchor internals and important width overrides. |
| 9, layout refresh measurement | Accepted. CSS sizes the pump; marker scaling responds to actual resize/unit changes. No generated-at/acquisition dependency or document-offset height clamp remains; station bay slots no longer guess character widths. |
| 10, translation | Accepted. Complete Last known/count phrases use Lingui; provenance translations use typed source kinds. Unit identifiers remain actual source identifiers. |
| 11, static output misuse | Accepted where static; static provenance output was removed. Genuine condition/count and manual-refetch announcements remain one hidden status region per surface, based on actual outcomes; no interactive children. |

The two reviews' remaining stylistic prescriptions are qualified above: avoid
new columns/rows solely to explain the drawing, do not erase independent alarm
occurrences beside a Normal current assessment, do not invent exact list
coverage, do not change a real 0.42 rpm or declared cusec source unit, and do not
replace the validated native drill-down with a new investigation workflow.


## Final verification and rendered evidence

All accepted findings are implemented. The five Opus blockers are covered by
visible state words/alarm shapes, visible failure labels, the scrollable phone
bay row, partial/unknown-coverage badges, and the inline limits count. Existing
O27, permission and native navigation contracts remain covered by focused checks.

These are bounded Linux Chromium fixture results, including the exact corrective
replays of failed cases. Full Mac suites remain assigned to the coordinator.

| Check | Final result | Evidence under `data/devscratch/ui-r2/checks/` |
| --- | --- | --- |
| Mimic layout/actions | 27 unique cases pass across the original run and metadata corrective replay; later assessed-state captures verify the final marker palette | `opus-mimics.log`, `opus-mimic-corrective.log`, `final-pump-states.log` |
| Machines/location/Parts | 28 applicable unique cases pass; 2 intentional viewport skips. Later provenance/card replay also passes with SCADA mapping | `opus-tables.log`, `opus-correctives.log`, `final-evidence.log` |
| Health condition/native/trend selection | 32 unique cases pass across bounded replays; 4 additional failure-state cases pass | `opus-health.log`, `opus-health-corrective.log`, `opus-health-raw-corrective-2.log`, `final-health-failure-captures.log` |
| S8 state/provenance/clock/focus/O27 selection | 33 applicable unique cases pass across exact correctives; 1 intentional duplicate matrix skip | `opus-s8.log`, `opus-correctives.log`, `final-states.log` |
| Native schematic matrix | Pass at 1100×800, 1440×900, 1920×1080 and 390×844; retains painted size, overlap, focus, tooltip and contrast assertions | `final-states.log` |
| Supplemental abnormal-state matrix | All 16 cases pass across the corrected fixture run and final pump replay; all 4 separate Health failure cases pass | `final-evidence.log`, `final-pump-states.log`, `final-health-failure-captures.log` |
| Numbered final capture matrix | 18/18 pass after the last application change; every PNG independently opened and inspected | `final-captures.log`, `final-artifacts.json` |
| TypeScript | `tsconfig.json` and all 8 `tsconfig.s*.json` pass; S8 repeated after final capture-test edits | `final-types-results.json`, `final-s8-types.log` |
| Changed units | 93/93 pass across 10 files | `final-units.log` |
| Lingui | extract and compile --typescript pass; all 117 locale files restored byte for byte | `final-lingui-results.json`, `final-lingui-extract.log`, `final-lingui-compile.log` |
| Prek on all existing changed/new files | Pass, including final report/tracking update; hook formatting applied before source freeze | `final-prek-source.log`, `final-prek-handoff.log` |
| Tracking/whitespace/locales | Tracking validator and git diff --check pass; no locale diff | `final-tracking.log`, `final-handoff-validation.json` |

The retained failure logs are deliberately not overwritten with passing totals.
They exposed actual stale-age integration, KPI metadata, badge/marker contrast,
failed-cell wrapping and phone fold defects. Their exact cases pass in the
corrective logs above. Obsolete phone selectors, native inner-header alignment
selectors and an initial freshness-budget wording assertion were corrected to
assert the intended facts. No contrast threshold, meaningful navigation,
known-zero, failure or coverage assertion was relaxed.

The added abnormal fixtures initially had contradictory synthetic summaries and
a temperature assigned to a flow point. They now supply coherent station
Running/3 facts, an actual motor temperature with bad quality, and distinct
Normal/Warning/Critical temperature parts. The first tall Health element crop
also included a sticky-header artifact; final full-page failure captures fix the
evidence without changing the product. Failed row-trend batches return an actual
503 while the independent selected chart request succeeds; this is not presented
as failure of the entire Health source.

The last source checkpoint is `ui-r2-final-reviewed-complete`, covering 1362
frontend source/library/test/config files in `checks/final-source-checkpoint.json`.
Every hash remained unchanged through final captures and visual inspection.
`checks/final-artifacts.json` contains the source-manifest hash and PNG hashes.
Older checkpoints and captures are superseded. No commit was written.

### Final screenshot index

All paths below are under `data/devscratch/ui-r2/final/`. Desktop views are
1440×900; phones are 390×844. Each has a companion JSON with source revision,
viewport, scheme and fold measurements. All 18 were freshly captured from the
last source and individually LOOKED at. Renders use owned Linux Chromium on
WS3012 and fixture-only harness15250, with synthetic intercepted data.

| No. | Screenshot |
| --- | --- |
| 01 | [01-mimic-station-desktop-light.png](../../../../data/devscratch/ui-r2/final/01-mimic-station-desktop-light.png) |
| 02 | [02-mimic-station-desktop-dark.png](../../../../data/devscratch/ui-r2/final/02-mimic-station-desktop-dark.png) |
| 03 | [03-mimic-station-phone-light.png](../../../../data/devscratch/ui-r2/final/03-mimic-station-phone-light.png) |
| 04 | [04-mimic-pump-desktop-light.png](../../../../data/devscratch/ui-r2/final/04-mimic-pump-desktop-light.png) |
| 05 | [05-mimic-pump-desktop-dark.png](../../../../data/devscratch/ui-r2/final/05-mimic-pump-desktop-dark.png) |
| 06 | [06-mimic-pump-phone-light.png](../../../../data/devscratch/ui-r2/final/06-mimic-pump-phone-light.png) |
| 07 | [07-machines-light-desktop.png](../../../../data/devscratch/ui-r2/final/07-machines-light-desktop.png) |
| 08 | [08-machines-dark-desktop.png](../../../../data/devscratch/ui-r2/final/08-machines-dark-desktop.png) |
| 09 | [09-machines-light-phone.png](../../../../data/devscratch/ui-r2/final/09-machines-light-phone.png) |
| 10 | [10-location-light-desktop.png](../../../../data/devscratch/ui-r2/final/10-location-light-desktop.png) |
| 11 | [11-health-light-desktop.png](../../../../data/devscratch/ui-r2/final/11-health-light-desktop.png) |
| 12 | [12-health-dark-desktop.png](../../../../data/devscratch/ui-r2/final/12-health-dark-desktop.png) |
| 13 | [13-health-light-phone.png](../../../../data/devscratch/ui-r2/final/13-health-light-phone.png) |
| 14 | [14-station-parts-light-desktop.png](../../../../data/devscratch/ui-r2/final/14-station-parts-light-desktop.png) |
| 15 | [15-station-parts-light-phone.png](../../../../data/devscratch/ui-r2/final/15-station-parts-light-phone.png) |
| 16 | [16-pump-parts-light-desktop.png](../../../../data/devscratch/ui-r2/final/16-pump-parts-light-desktop.png) |
| 17 | [17-pump-parts-dark-desktop.png](../../../../data/devscratch/ui-r2/final/17-pump-parts-dark-desktop.png) |
| 18 | [18-pump-parts-light-phone.png](../../../../data/devscratch/ui-r2/final/18-pump-parts-light-phone.png) |

### Visual findings from the final images

01–03: one KPI strip; idle bays show Idle without repeated 0 MW. Missing plant
power is a plain dash. Phone bay text is readable with a swipe cue and edge
shadow; all 14 remain reachable by scrolling and keyboard. The phone image does
not pretend that all bays fit simultaneously.

04–06: the full-width drawing dominates. At 1440px, painted geometry is about
860×626px within a 1406×703px drawing region: 61% of content width and 70% of
viewport height. Compact spatial callouts are below it, with complete-page pump
evidence in `data/devscratch/ui-r2/full/`. Phone markers and callouts remain
readable. Missing pressure stays a dash; 0 MW, 0 cusec and actual 0.42 rpm remain.

07–10: aligned readable headers, normal-size checkboxes, visible Read failed,
quiet genuine gaps and distinct coverage-unknown Normal. Seven complete rows fit
in each desktop Machines image; six in Location. Phone shows one complete machine
card and much of the next. Warning · 2 is fully visible; the summary row scrolls
horizontally. The location tree name is whole and scoped totals have clear spacing.

11–13: partial Normal · 2/6 is distinct, the compact summary exposes three complete
desktop signal rows, and the complete first primary phone card plus the next name
fit above the fold. Recorded zero and 1834 (no unit) are visible; phone values,
Search/filter/Refresh and the selected Health tab fit. The native superuser
warning and machine actions remain.

14–18: full Catalogue stock headers, whole concise SCADA mapping/Registry import,
right-aligned numeric columns, compact Read failed and one aligned toolbar.
Phone station/pump Parts each show one complete card plus the next name; secondary
facts remain in Details. These images do not establish two complete phone cards.

All 28 supplemental PNGs under `data/devscratch/ui-r2/states/` were also opened
and inspected: 16 state views, four Health table crops, four dark focus views and
four Health failure views. They show Running/Fault/Stale/Unknown/Idle, distinct
Critical/Warning alarm shapes, all assessed marker colours, retained 95 °C with
Bad quality, genuine missing dashes, real Read failed, partial coverage, recorded
zero and visible focus rings. Rendered marker text checks require at least 4.5:1
and alarm/focus edges 3:1. Native panel text/edge checks and the final Machines
header checks retain those thresholds. This is targeted contrast evidence, not
an independent accessibility certification.

## Handoff and summary

The owned Chromium container and Vite harness are stopped. Both permitted ports,
3012 and 15250, are clear (`checks/runtime-cleanup.log`). No Azure connection,
backend server, other worktree/container/port, Git write, issue or PR was used.
Tracking parent/provider dispositions remain unchanged; these fixture results do
not establish deployment or plant mapping acceptance.

Both reviews are dispositioned, valid findings are repaired, the owner's pump
scale/text feedback is implemented, and all 18 final screenshots are inspected.
Focused checks pass; the coordinator retains full Mac suite replay and human review.
