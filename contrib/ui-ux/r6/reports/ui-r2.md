# UI round 2 — Hyperagent review repair

Latest work is Follow-up 7, built on `a2eff8510`; see its finding table below.
The earlier sections record the committed baseline. Final screenshots are
replaced by the latest follow-up captures rather than reused from that baseline.

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
| A4, weak schematic hierarchy and repeated idle zero power | Accepted. Current bays rendered all usable power values, including 0 MW in idle bays. | Idle zero power renders Idle; nonzero or running source power remains visible. Forebay is an unfilled tank outline; static manifolds use muted stroke/fill tokens. Geometry and layout remain schematic, with review information in the header tooltip. `StationSchematic.tsx` / CSS. |
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
| Top 10: inconsistent phone breakpoint and density | Accepted for the signal controls and shared shell. SignalTable used 47.99em while the other owned panel modules used 48em. All three filters plus the table/cards toggle stacked ahead of readings. | The initial owned breakpoint was 48em; Follow-up 3 corrects the phone maximum to 47.99375em. Search remains visible; a 44px Signal filters button discloses Component filter, Order by and table/card mode on phone. Desktop controls remain on one aligned row. |
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
| E2 / Top 8: Parts column allocations sum to 105% | Accepted: station values in source sum to 105. | Station final allocation 8/16/10/10/16/8/12/10/10 sums to 100; the final2 Catalogue stock header still wrapped, addressed in Follow-up 3 by In stock. Pump redistributes hidden Equipment space into Component and Catalogue part; visible columns normalize for stock permission and phone disclosure mode. Explicit minWidth=0 prevents native default 100px minima from overriding these narrow allocations. Native right textAlign remains on both numeric columns. |
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
clipped Catalogue stock headers; final2 still wrapped that header, addressed in Follow-up 3. Those captures and the
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
| S10 | Qualified. The current tank has no water fill or inferred level; its level KPI is supplied independently. Physical arrangement is explicitly unverified in the accessible info tooltip. | Retain validated schematic geometry and its numeric level metric. Rejected inventing an outlet/flow arrow or an extra illustrative caption: no source fact supplies a flow direction, and the owner forbids unnecessary captions. |
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

14–18: Catalogue stock headers remained wrapped, whole concise SCADA mapping/Registry import,
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


## Owner and Fable follow-up on 270bb4be3 (ready for coordinator review)

The new owner screenshots show older Health/tree markup; source verification
confirms round 2 already removed the yellow coverage panel, visible clock caption
and tree type/code labels. Their remaining wording/layout concerns are accepted.
The complete Fable review was read and checked against the committed source.
The dispositions below compare Fable's pre-round-2 review with `270bb4be3`
and this follow-up. No previous test result is promoted to this follow-up.

Current screens: Health (operator/reliability assessment and source coverage),
station/unit mimic (state, limit and acknowledgement investigation), and location
workspace (find a physical scope). Existing providers, native Mantine 9/React,
permissions, O27 presentation and machine header/actions are preserved.

| Follow-up | State |
| --- | --- |
| Source/owner/Fable audit | Complete |
| Health wording and shared tooltip behaviour | Complete; focused checks pass |
| State/limit/acknowledgement mimic presentation | Complete; focused checks pass |
| Responsive location tree/root overview | Complete; focused checks pass |
| Focused checks and fresh affected captures | Complete; final 18-case matrix and 16 owner cases pass; 20 PNGs inspected |


### Owner changes and source boundaries

- Health uses one calm, keyboard-described **Critical signals: not designated**
  item beside the partial Normal badge. Full coverage reasoning remains in its
  hoverable, Escape-dismissable tooltip; time-zone detail stays with the timestamp.
- Running equipment has green fill and a play glyph, Idle gray fill and pause
  glyphs, Fault red fill and an exclamation triangle, Stale hatching, and Unknown
  dashed outlines. Disabled/failed/non-reporting sources never retain green.
  Slow discharge dashes require both Running and a current, good-quality,
  positive discharge reading. Zero/reverse/absent/bad/stale readings do not move.
  This follows the checked-in discharge geometry and does not invent a valve
  position, magnitude scale, tank level or reverse-flow direction.
- Only actual configured Warning/Critical verdicts colour values, callout
  outlines and triangular/diamond markers. Unconfigured measurements stay neutral.
  Separate halos blink at 1 Hz only for explicit open anomaly IDs/statuses;
  explicit acknowledged rows are steady. The text and severity glyph remain
  steady throughout. Reduced motion removes flow and blinking and thickens the
  steady alarm halo. No acknowledgement is inferred from a worst-severity flag.
- Active threshold alarms precede detailed reading groups. All-failed collapsed
  groups say **Read failed**; genuinely unavailable groups use the shared dash.
- Location names occupy a wider desktop tree with aligned guide/chevron/icon
  columns, whole-word wrapping and a bounded scroll viewport. Type/code are in
  shared accessible tooltips and selected details. The single search input has
  an accessible name and placeholder. The root overview aligns name/type/code;
  codes remain secondary and readable. Phone uses a branch drill-down, Back,
  current-location cue and focus transfer when a choice closes the browser.
- Actual inferred provenance says **Inferred SCADA** even when its display alias
  says SCADA mapping. Original provenance and supplied import time remain available.

Principal files: `HealthSummary.tsx`; shared `components/items/ValueTooltip.tsx`;
`health/mimic/{mimicVisuals,UnitSchematic,StationSchematic,MimicParts,MimicReadings,
AlarmTable,UnitMimic,MimicSchematic}` and their owned CSS; `PumpMimic.tsx`;
`PumphouseMimic.tsx`; `locations/{LocationBrowser,LocationWorkspace,locations.module.css}`;
`tables/assets/{componentPresentation,ComponentFacts,MachineComponentTable}`.
The thin native table adapter adds an optional `withRowBorders` property in
`src/frontend/lib/types/Tables.tsx` and `src/frontend/src/components/tables/InvenTreeTable.tsx`;
its default preserves every other caller. These paths are under
`src/frontend/src/pages/assets/` unless their prefix says otherwise. Native
API contracts, permission checks, routes, source units and O27 remain intact.

### Fable findings, verified against source

“Already fixed” means rejected as a remaining defect because the committed
round-2 source already supplies the stated behaviour; it is not a rejection of
what the reviewer saw in the older diff. “Qualified” identifies the valid defect
and the unsupported or conflicting proposed remedy separately. Fable assumed
Mantine 8; the lockfile resolves installed @mantine/core to 9.6.1, whose public `interactive` tooltip
API is used. No Mantine internals or `!important` were added.

| ID | Disposition and reason | Fix / source |
| --- | --- | --- |
| S1 | Qualified: repeated idle zeroes and legend counts already removed. Dropping an independently supplied running-count KPI is unnecessary once the legend has no counts. | `StationSchematic`, `MimicLegend`; retain one KPI strip and source state. |
| S2 | Accepted: plain linework needed hierarchy and independent state cues. | `StationSchematic`, `mimicVisuals`: pipe edge/core, filled motor/pump, centrifugal vanes, play/pause/fault glyphs, hatch/dash. |
| S3 | Accepted: an unscaled static wave suggested a fabricated level. | Remove water/wave path in `StationSchematic`; retain actual level KPI without inventing tank bounds. |
| S4 | Accepted: uncapped growth on very wide screens. | `StationSchematic.module.css` caps drawing at 1440px; preserves native readable label geometry rather than an arbitrary 1240px cap. |
| S5 | Already fixed: equal-height native KPI grid, no separate total-card treatment. | `StationSchematic` / CSS. |
| S6 | Already fixed: visible provenance/clock caption removed; info is consistently accessible. | `PumphouseMimic`, `PumpMimic`, timestamp tooltip. |
| S7 | Already fixed: actual station heading and compact empty-alarm item. | `StationSchematic`, `PumphouseMimic`. |
| S8 | Already fixed: no-active-alarm wording has a compact inline without-limits count. | `MimicAlarmCoverage`; unknown scope never becomes zero. |
| S9 | Already fixed for surfaces/edges; accepted richer state hierarchy in this follow-up. | Owned panel tokens plus `mimicVisuals`, `StationSchematic` CSS. |
| S10 | Qualified: round 2 already adds explicit swipe text, edge shadows and keyboard scroll-to-bay. Owner permits a clear horizontal scroller; forcing an unrelated phone tile grid is unnecessary. | `StationSchematic`: all 14 bays remain reachable; new state cues preserved. |
| S11 | Already fixed: shared left-aligned dash with independent target area; equal KPI height. | `ValueTooltip` CSS, `StationSchematic` CSS. |
| S12 | Reviewer itself marks stale; current detailed station readings are a deliberate disclosure, not a duplicate summary caption. | `PumphouseMimic`, `MimicReadings`. |
| P1 | Main blocker already fixed by round 2: no fold-fit or scrollY sizing, CSS full-width dominant drawing. Reject deleting marker scaling: its observer runs on unit/resize, not each refresh, and protects phone numerals. The old document-position calculation was flawed but `rect.top + scrollY` itself is scroll-invariant. | `UnitSchematic`, `UnitMimic.module.css`; new state cues keep the large drawing. |
| P2 | Contrast/neutral-disc defect already fixed. Accepted explicit triangle/diamond severity shapes in this follow-up. | `MimicParts`, `mimicVisuals`; numbers remain readable in both schemes. |
| P3 | Qualified: value-only summaries, units, equal-range collapse and negative-zero cleanup were already fixed. Accepted remaining name clipping found in rendered phone inspection: part names now wrap completely; phone spatial columns share grid rows so they do not stagger. Reject forcing mixed-unit evidence into one arbitrary line. | `MimicParts`, `MimicReadings`, shared formatting; full label/reasons in tooltip and native reading group. |
| P4 | Already fixed: unscaled mini-bars removed. | `MimicParts`. |
| P5 | Qualified: state legend now describes actual coloured equipment; deleting it would remove the hatch/dash/state key requested by the owner. | `MimicLegend`, `UnitSchematic`, `mimicVisuals`. |
| P6 | Already fixed: hard-coded process dl and polling caption removed. | `UnitMimic`. |
| P7 | Visible duplication already fixed; accepted multiline tooltip whitespace and pointer-hover persistence. | Shared `ValueTooltip` public interactive API and `whiteSpace: pre-line`; no PumpMimic-specific tooltip positioning fix was demonstrated. |
| P8 | Already fixed: status icons convey their state and empty alarms do not create an alarm link. | `PumpMimic`, canonical badges. |
| P9 | Already fixed: spatial sides sorted by native y/x; compact callouts below preserve the owner's dominant drawing. Reject mandatory leaders to below-drawing cards. | `UnitSchematic`. |
| P10 | Qualified: native numbered accordion and accessible badges already fixed. Accepted alarm priority: table now precedes details. A new Drawer plus All readings workflow is unnecessary to repair this view and would replace existing deep-link/group navigation. | `PumpMimic`, `PumphouseMimic`, `UnitMimic.alarmPanel`, `MimicReadings`. |
| P11 | Rejected: hiding genuinely unmapped pressure would conceal a missing key measurement. Station aggregate m³/s and unit cusec are different source units; silently renaming them would fabricate a conversion. Help controls remain native, focusable tooltip buttons for accessible provenance. | `UnitSchematic`, `ValueTooltip`, source-unit formatter. |
| P12 | Already fixed: bounded icon search with accessible name. | `UnitMimic`. |
| P13 | Marker legibility and compact header already fixed; accepted remaining name clipping and staggered phone rows, repaired using full names and shared subgrid rows. Spatial columns preserve drawing correspondence; owner does not require a new numeric one-column workflow. | `UnitSchematic`, `UnitMimic.module.css`; phone overlap/target checks retained. |
| P14 | Accessible-name defect already fixed; accepted visible all-failed group state. Genuinely unpublished/no-reading groups retain a quiet dash, consistent with owner rules. | `MimicReadings`; SSR distinguishes failed vs unpublished. |
| P15 | Accepted invalid block nesting and duplicated tooltip primitive. | `AlarmTable` uses shared `ValueTooltip` and span groups. “Active · acknowledged” remains visibly active; no redundant caption is added. |
| L1 | Already fixed: inherited compact header typography and matching text/numeric alignment. | `LocationWorkspace`, `locations.module.css`. |
| L2 | Already fixed: one shared FailedValue/canonical status badge, retained last value and quiet missing dash. | `LocationWorkspace`, `ValueTooltip`. |
| L3 | Rejected proposal to hide unknown-severity alarms: a supplied “other” alarm remains an active fact. A Normal assessment and an independently active informational alarm can coexist; the API facts must not be merged into an invented verdict. | `MachineAlarmBadges`, condition cell. |
| L4 | Already fixed: one machine count, pluralized summary, no extra count by bulk action. | `MachinePageSummary`, `LocationWorkspace`. |
| L5 | Accepted remaining vertical toolbar misalignment. | `LocationWorkspace`: 36px desktop / 44px phone controls, centered switch, a painted 1px token track border (colour alone did not paint a border) and >=3:1 rendered checks in both states. Keep disabled Move selected discoverable until selection exists. |
| L6 | Already fixed: work priority carries its flag and value in a real tooltip target, separate criticality column. | `LocationWorkspace`, `ValueTooltip`. |
| L7 | Already fixed for repeated site caption/density. Reject clamping away identity solely to force uniform height. | Machine identity layout in `LocationWorkspace`. |
| L8 | Already fixed: partial/unknown-coverage Normal differs visibly from fully covered Normal. | Shared `HealthStateBadge` and canonical `StatusBadge`. |
| L9 | Already fixed: 20px visible checkbox / 44px target, scrolling summary chips, aligned fact grid and 44px controls. | `LocationWorkspace`, owned CSS; captures retain full Warning · 2. |
| W1 | Accepted: remaining visible search label removed, aria-label retained, one placeholder. | `LocationBrowser`. |
| W2 | Already fixed: grouped actions, visible filter-scope totals qualifier and actual totals/list distinction. | `LocationWorkspace`, `countSummary`. |
| W3 | Type/code words already removed; accepted deeper guide/spacing/width and phone hierarchy work. | `LocationBrowser`, `locations.module.css`, `LocationWorkspace`; no native-name truncation. |
| W4 | Qualified: owner explicitly requests a wider desktop panel, implemented as four grid columns. Phone chooser collapses into drill-down. Mandatory desktop collapse would conflict with this scope. | Location workspace/grid and browser. |
| H1 | Accepted calm inline wording. Owner's later instruction supersedes Fable's proposed retained yellow alert. | `HealthSummary`: one critical-designation item, full reasoning in shared tooltip, partial badge retained. |
| H2 | Clock caption/label already removed; timestamp already uses shared formatter with zone in tooltip. Keep readable absolute time alongside freshness, which aids observation comparison. | `MachineHealthPanel`, `Timestamp`. |
| H3 | Already fixed: one canonical badge with explicit partial Normal distinction. | `HealthStateBadge`, `StatusBadge`. |
| H4 | Already fixed: compact inline empty anomalies item. | `HealthSummary`. |
| H5 | Qualified: visible Active anomalies label already exists. `counts == null` means unavailable compatibility data, not evidence of a failed request; backend summary always provides a count map. Inventing Read failed there would be false. Actual signal/summary request failures retain visible failed state. | `HealthSummary`, `MachineHealthPanel`, summary service contract. |
| H6 | Already fixed: phone nesting removed and Refresh stays in compact title row; native tab fully exposed without removing native controls. | `MachineHealthPanel`, monitoring CSS. |
| H7 | Qualified: Stale, source-tag details and role=img defects were already fixed. Accepted remaining None limits copy found in the rendered final table; replaced it with the shared dash and full accessible reason, without adding tab stops. | `SignalTable`, `SignalReading`, `ValueTooltip`; searchable external_key remains available. |
| H8 | Already fixed: text state values and dimensionless numbers retained; unit 1 suppressed. | `readingPresentation` and units. |
| T1 | Already fixed: single Parts toolbar, native registry action retained. | `InstalledPartsPanel`, `MachineComponentTable`. |
| T2 | Rejected data-dependent auto-hiding: current fixture/page uniformity cannot establish uniformity across a paginated population. Hiding explicit Draft/source/equipment facts would obscure evidence; genuine missing cells already use compact dashes. | `MachineComponentTable`, `ComponentFacts`; secondary phone facts are disclosed. |
| T3 | Already fixed: widths total 100%, readable untruncated headers and numeric alignment; a long header may wrap within its reserved column. | `MachineComponentTable`, owned CSS. |
| T4 | Already fixed for grid/typography/model wrapping. Reject clipping full model names to force equal row height. | `MachineComponentTable` CSS; no mid-word model hyphenation. |
| T5 | Date/short register label already fixed; accepted preserving inferred confidence in SCADA label. API does not supply the proposed provenance enum; normalize existing legacy vocabulary once rather than invent a provider field. | `ComponentFacts`, `componentPresentation`, shared plant-clock formatter. |
| T6 | Already fixed: no pagination recolour, hard-coded hex, !important or role=img. Scoped semantic token choices are intentional; a global theme rewrite is not required. | `MachineComponentTable.module.css`, canonical tooltip/badge. |
| T7 | Qualified: 44px actions were already fixed. Accepted remaining station Equipment row before the component name; Equipment now moves into the keyboard-operated Details disclosure on phone. Per-cell hairlines are removed through a small public withRowBorders adapter option; the card separator remains. Scope remains available rather than inferred from a uniform fixture. | `MachineComponentTable` CSS. |

Q3 is covered by P1/P2/P9/P13 and the owner-driven state changes. Q4 is covered
by T2/T5. Q5's additional source-verified fixes: non-null total reasons are now
retained (`StationSchematic`); tooltips are hoverable, multiline and do not bind
sticky touch behaviour to actionable callouts/accordion controls; selected-bay
keyboard focus has a thicker dashed ring distinct from selection; unused legacy
schematic renderer and dead width prop are removed. AlarmTable already retained
last-known bad-quality values in round 2. Missing reason falls back to “Reason not
supplied”, never an invented no-data cause. The native sm breakpoint is 48em (visibility uses its normal 0.1px upper-bound
subtraction); owned compact CSS includes the 48em endpoint. That inclusive
compact sizing is not a second JavaScript layout breakpoint and does not hide
both control sets. No broken boundary rendering was established by the review.
The actual obsolete 1100px sizing branch was already removed in round 2.
No private selector is restored. Shared canonical badges/fact components and the plant-clock formatter
remain the consistency kit; only real actions/help affordances are interactive.

Fable's section 3 and ranked top 10 repeat these IDs. All are dispositioned above:
1 P1/P13; 2 S8/L8/W2/H5/P14; 3 shared ValueTooltip; 4 P2; 5 P6/P7/P10/P5;
6 P3/P4/P9; 7 L1/L2/L5/L6/L9; 8 T1–T6; 9 S1–S5/S10/S11; 10 canonical status,
time, tooltip and scoped token components. O27 remains live as instructed;
its tooltip wrapper provides recorded-source detail and is not removed merely
because the reviewer dislikes a native help control.


### Follow-up verification and evidence

The final frontend checkpoint is the 40-file byte manifest
`data/devscratch/ui-r2/owner/checks/source-manifest-final.json`, SHA-256
`830575ad4719356451068a8900c5b30f915f7cc60539e5d8d3f83ba437dd8fd0`,
base `270bb4be3`. Companion capture JSONs identify
`owner-270bb4be3-830575ad4719`. All rendering uses owned **Linux Chromium**
WS3012 with the synthetic Vite fixture harness15250. No backend, Azure data or
plant connection is represented by these results.

| Check | Actual result / evidence |
| --- | --- |
| Existing mimic behaviour and desktop/tablet/phone geometry | 27/27 pass, `owner/checks/browser-mimics-final.log`; after removing name clamps and aligning phone rows, six affected geometry cases pass again in `browser-layout-final-v2.log`. |
| Focused location, Health coverage/freshness and Parts provenance | 20/22 initially pass, `browser-scoped.log`; both failures asserted obsolete “20 of 20 machines”. Updated assertions require the concise count plus all 20 actual rows and preserved URL/history state. Both corrected cases pass, `browser-location-repair.log`. |
| Final Health missing-limits, Parts desktop headers and both phone asset types | 7 pass / 3 deliberate viewport skips, `browser-health-parts-final.log`. The station phone card leads with the component, Equipment remains in keyboard Details, and every primary cell has zero internal bottom border. |
| Changed-component units | 65/65 in five files, `units-complete.log`: source-driven motion/verdict/acknowledgement guards, inferred provenance, shared tooltip semantics, failed vs unpublished collapsed groups, monitoring markup. |
| TypeScript | Final nine-project result recorded in `types-complete.log`: application tsconfig and every tsconfig.s*.json. |
| Translation | Extract and TypeScript compile succeed; all 117 locale files restored byte-for-byte, zero mismatches, `lingui-complete.log` / `locale-manifest.json`. |
| Changed-file hooks | All changed files pass from the worktree root with the requested PATH, including final report/tracking, `prek-final.log`; tracking validation passes in `tracking-final.log`. |

The new 16-case owner matrix covers both themes on desktop and phone: calm
Health designation reasoning, keyboard descriptions, pointer persistence and
Escape; actual Running/Fault/Stale/Unknown/Idle state; positive versus zero flow;
unconfigured high values remaining neutral; configured warning/critical value
and marker contrast; explicit open versus acknowledged halos; reduced motion;
long-name tree hierarchy and code search, phone drill-down and choice focus;
painted switch boundaries with >=3:1 contrast in both checked states.
Text checks retain >=4.5:1, SVG/control edges >=3:1. These are targeted rendered
measurements, not an independent accessibility certification.

Earlier attempts are retained honestly: the first native mimic invocation used
the wrong harness entry and was interrupted; a stale browser connection required
restarting only the owned container. A later capture helper used a single-element
hidden assertion while two tooltip exit transitions were present; it now waits
for zero tooltips. The explicit hover/focus/Escape assertions remain unchanged.
The switch contrast test initially selected a label, while installed Mantine 9
renders its public track as a span; the selector now matches that native shape.
Those attempts are not counted as passes. Intermediate captures before the final
phone-name/row, switch, Health-limits and station-Parts repairs are superseded.

The final checkpoint matrix passes 18/18 (`browser-numbered-final-v4.log`),
and the complete owner matrix passes 16/16 (`browser-owner-final-v4.log`).
All 20 numbered PNGs were opened and individually inspected after this final run. Static PNGs freeze animation;
the browser assertions above establish motion/reduced-motion and acknowledgement
behaviour. No screenshot alone is used as evidence of blinking or live polling.


### Fresh final screenshot index

Every path below is under `data/devscratch/ui-r2/final/` with a same-name JSON
recording the final source token, viewport and synthetic renderer. The inspected
PNG checksums are in `owner/checks/final-inspection.json`; source/locale/capture
integrity checks pass with zero mismatches in `final-integrity.log`.

| # | PNG | Inspection |
| --- | --- | --- |
| 01 | `data/devscratch/ui-r2/final/01-mimic-station-desktop-light.png` | Opened and inspected |
| 02 | `data/devscratch/ui-r2/final/02-mimic-station-desktop-dark.png` | Opened and inspected |
| 03 | `data/devscratch/ui-r2/final/03-mimic-station-phone-light.png` | Opened and inspected |
| 04 | `data/devscratch/ui-r2/final/04-mimic-pump-desktop-light.png` | Opened and inspected |
| 05 | `data/devscratch/ui-r2/final/05-mimic-pump-desktop-dark.png` | Opened and inspected |
| 06 | `data/devscratch/ui-r2/final/06-mimic-pump-phone-light.png` | Opened and inspected |
| 07 | `data/devscratch/ui-r2/final/07-machines-light-desktop.png` | Opened and inspected |
| 08 | `data/devscratch/ui-r2/final/08-machines-dark-desktop.png` | Opened and inspected |
| 09 | `data/devscratch/ui-r2/final/09-machines-light-phone.png` | Opened and inspected |
| 10 | `data/devscratch/ui-r2/final/10-location-light-desktop.png` | Opened and inspected |
| 11 | `data/devscratch/ui-r2/final/11-health-light-desktop.png` | Opened and inspected |
| 12 | `data/devscratch/ui-r2/final/12-health-dark-desktop.png` | Opened and inspected |
| 13 | `data/devscratch/ui-r2/final/13-health-light-phone.png` | Opened and inspected |
| 14 | `data/devscratch/ui-r2/final/14-station-parts-light-desktop.png` | Opened and inspected |
| 15 | `data/devscratch/ui-r2/final/15-station-parts-light-phone.png` | Opened and inspected |
| 16 | `data/devscratch/ui-r2/final/16-pump-parts-light-desktop.png` | Opened and inspected |
| 17 | `data/devscratch/ui-r2/final/17-pump-parts-dark-desktop.png` | Opened and inspected |
| 18 | `data/devscratch/ui-r2/final/18-pump-parts-light-phone.png` | Opened and inspected |
| 19 | `data/devscratch/ui-r2/final/19-location-dark-desktop.png` | Opened and inspected |
| 20 | `data/devscratch/ui-r2/final/20-location-light-phone.png` | Opened and inspected |


01–03 show green/play Running, red/triangle Fault, hatched Stale, dashed Unknown
and gray/pause Idle in the same native drawing, with semantic warning/critical
markers. The empty tank no longer implies a fabricated water level. Phone uses
the explicit swipe affordance and edge shadow; all 14 bays remain available
through scrolling and keyboard, as verified by the existing geometry spec.

04–06 retain the dominant pump: painted geometry is 860×626px within a
1406×703px drawing region at 1440px (61% of content width, 70% of viewport height).
Actual source values and units remain; configured warnings/critical readings
have distinct outlines and shape cues. Complete callout names wrap; phone
columns share rows. Additional full-page phone and acknowledged-dark pump
captures in `owner/captures/` were also opened and inspected: callouts, active
alarm text and detailed reading groups remain readable below the drawing.

07–09 retain compact aligned columns, the normal-size checkbox with its 44px
phone target, complete Warning · 2, visible failed reads and coverage-unknown
Normal. The switch now has an actual contrasting boundary in both states.
10/19 show the wider, unclipped hierarchy and aligned readable root-list codes;
20 shows the phone drill-down with Back, full current parent and all room names.
11–13 show one calm designation item, partial Normal, no clock caption and quiet
missing-limit dashes; the first actual phone reading remains above the fold.
14–18 show the concise honest provenance and aligned stock/work values. Both
phone Parts cards lead with the component and retain secondary equipment/source
in keyboard Details; internal cell hairlines are gone and a complete first card
fits above the fold.

### Follow-up handoff

All requested owner changes and remaining verified Fable findings are repaired;
unsupported, superseded and conflicting suggestions have explicit reasons above.
Gemini/Opus round-2 changes remain intact. Focused browser checks, all nine TypeScript
projects, 65 units, translation extraction/compile with 117 bytewise restores,
and changed-file hooks pass. Full sprint suites on the Mac and human review
remain with the coordinator. Parent work/contract acceptance dispositions are
unchanged; these are isolated fixture results, not deployment or plant mapping
acceptance. No Git write, PR/issue, Azure connection or data env access was used.
Owned browser and harness cleanup is recorded in `owner/checks/runtime-cleanup.log`.


## Follow-up 2 — 22 October 2026

Completed on `ad22ab1b1`, preserving previous round-2 work and O27. All confirmed
A–G items were checked against source and accepted. The G3 URL was already
canonical; the defect was its link being conditional on visible anomalies.
The link now remains available for mapped pumps with no anomalies.

| Findings | Repair and principal files (under `src/frontend/src/`) |
| --- | --- |
| A1–A2 | Station alarm rows identify P1/P7 in a Pump column; single-unit tables retain their columns. Badge attention replaces the radio-like circle. `health/mimic/AlarmTable.tsx`, `health/PumphouseMimic.tsx`, `mimicVisuals.module.css`. |
| A3–A7 | Raised manifold and separated markers/halos; fault glyph inside motor with impeller retained; double-wall forebay without an invented level; date and “signals without limits” inline; shared darker state fills pass stroke/fill contrast checks. `StationSchematic.tsx`/CSS, `MimicAlarmCoverage.tsx`, `mimicVisuals.ts`. |
| B1–B4 | Desktop side columns, cropped native viewBox, all 13 callouts visible at 1440×900. Painted pump width 814 px of 1406 px content (58%). Annotation positions/backing and centred warning numeral clear bearing geometry and neighbouring marks. Callout names include reading/reason and verdict. Compact alarm badges; phone Live stays in row one and alarm callouts lead. `UnitSchematic.tsx`, `UnitMimic.module.css`, `MimicParts.tsx`, `UnitMimic.tsx`, `PumpMimic.tsx`. |
| C1–C4 | One page clock and O27 age rule; raw value 1834 with unit reason in tooltip; unavailable/empty histories use quiet dashes while failed history remains labelled; phone State keeps its label. `MachineHealthPanel.tsx`, `HealthSummary.tsx`, `SignalTable.tsx`/CSS, `SignalTrend.tsx`, `SignalReading.tsx`, `readingPresentation.ts`. |
| D | Missing/failed values are keyboard targets in Signals/history, Parts (including disclosed phone details), PointTable, AlarmTable, coverage and location rows. Collapsed reading sections expose their same reason through the native accordion control. Present values/timestamps render plain text; the shared tooltip no longer leaves hidden buttons behind. `ValueTooltip.tsx`, `Timestamp.tsx`, `common.tsx`, table consumers and `ComponentFacts.tsx`. |
| E1–E6 | Normal keeps its half icon, with coverage in its tooltip/name; summary pills wrap and use “In alarm · 8”; phone tr boxes remain; tree labels are non-interactive with native arrow/Space expansion and Enter selection, 32 px desktop rows and coarse-pointer targets; chooser has an open chevron and branch links look clickable; scope prose moves to totals tooltip. `LocationWorkspace.tsx`, `LocationBrowser.tsx`, `MachinePageSummary.tsx`, `locations.module.css`, `health/common.tsx`. |
| F1–F3 | Stock shows 6 with “6 in stock” accessible name; Draft/Verified borders meet 3:1; phone facts share a baseline and complete first Parts cards fit above 844 px. `MachineComponentTable.tsx`/CSS. |
| G | Wider-font machine cards fit in both themes after compacting the selected-location summary. Valid speed timestamps are plain text, leaving the intended single trend button. Canonical Health-to-schematic navigation passes desktop/phone. Existing host regression specs were run without editing them. |

Paths abbreviated as `health/` above are under `pages/assets/health/`; mimic files
are under its `mimic/` directory. Native geometry, supplied values, limits,
acknowledgement, roles and reduced-motion behaviour remain authoritative.

### Verification

Evidence: `data/devscratch/ui-r2/followup2/`.

| Check | Result |
| --- | --- |
| Focused Playwright | 40 unique applicable cases pass across targeted runs and corrective replays; the two desktop instances of the phone-only font test skip as designed. `targeted-current.log`, `targeted-final.log`, `final-pump-phone.log`. Covers A–G, native tree keyboard navigation, missing/history reasons, Parts baselines/borders, actual state stroke/fill contrast, owner state/acknowledgement/reduced-motion checks, all four speed KPI cases and both Health links. |
| Final capture spec | All 21 pass, `final2-captures.log`. Overlays/focus cleared explicitly; screenshots and metadata share source manifest `8baa12bf4aa5`. Every PNG opened and inspected individually. |
| TypeScript | `tsconfig.json` and changed test project `tsconfig.s8.json` pass; `types-app.log`, `types-s8.log`. |
| Vitest | 71/71 across six affected units, `units.log`. |
| Lingui | Extract and TypeScript compile pass; 117 locale files restored bytewise, zero mismatches; `lingui-results.json`. |
| Hooks | Changed-file prek (Homebrew PATH) passes, `prek-final.log`; `git diff --check` clean. |

Early runs exposed overlapping enlarged phone annotations and selected-location
font overflow; both were corrected and replayed. The new age assertion compares
identical observation instants and relative ages rather than concatenating hidden
desktop/phone date variants; keyboard checks use actual Tab navigation. The fixture
server was restarted to discard stale transformed modules before verification and
capture. No unrelated stale wording/clock specs were edited. Checks are isolated
Linux Chromium fixtures, not Mac full-suite, deployment or plant acceptance.

### Fresh captures inspected

All paths below are relative to `data/devscratch/ui-r2/final2/`.

| Files | Inspection |
| --- | --- |
| `01-mimic-station-desktop-light.png`<br>`02-mimic-station-desktop-dark.png`<br>`03-mimic-station-phone-light.png` | Station: identities, marker clearance, tank, states and phone scroll cue. |
| `04-mimic-pump-desktop-light.png`<br>`05-mimic-pump-desktop-dark.png`<br>`06-mimic-pump-phone-light.png` | Pump: dominant drawing, all 13 desktop callouts, separated markers and phone alarm priority. |
| `07-machines-light-desktop.png`<br>`08-machines-dark-desktop.png`<br>`09-machines-light-phone.png` | Machines: compact partial Normal, aligned facts, wrapped count pills and complete phone card. |
| `10-location-light-desktop.png`<br>`19-location-dark-desktop.png`<br>`20-location-light-phone.png` | Location: full names including Low Voltage Electrical Room; clean guides, codes and open phone drill-down. |
| `11-health-light-desktop.png`<br>`12-health-dark-desktop.png`<br>`13-health-light-phone.png` | Health: matching ages, quiet unavailable history, raw value without invented unit, labelled phone State. |
| `14-station-parts-light-desktop.png`<br>`15-station-parts-light-phone.png`<br>`16-pump-parts-light-desktop.png`<br>`17-pump-parts-dark-desktop.png`<br>`18-pump-parts-light-phone.png` | Parts: numeric stock, stronger badge borders, aligned phone values and complete first card. |
| `21-location-selected-light-desktop.png` | Selected location: machines shown; totals and scope controls without repeated prose. |

### Changed files

Source (31 files):

- `src/frontend/src/components/items/ValueTooltip.tsx`
- `src/frontend/src/pages/assets/health/HealthSummary.module.css`
- `src/frontend/src/pages/assets/health/HealthSummary.tsx`
- `src/frontend/src/pages/assets/health/MachineHealthPanel.tsx`
- `src/frontend/src/pages/assets/health/PumpMimic.tsx`
- `src/frontend/src/pages/assets/health/PumphouseMimic.tsx`
- `src/frontend/src/pages/assets/health/SignalReading.tsx`
- `src/frontend/src/pages/assets/health/SignalTable.module.css`
- `src/frontend/src/pages/assets/health/SignalTable.tsx`
- `src/frontend/src/pages/assets/health/SignalTrend.tsx`
- `src/frontend/src/pages/assets/health/Timestamp.tsx`
- `src/frontend/src/pages/assets/health/common.tsx`
- `src/frontend/src/pages/assets/health/mimic/AlarmTable.tsx`
- `src/frontend/src/pages/assets/health/mimic/MimicAlarmCoverage.tsx`
- `src/frontend/src/pages/assets/health/mimic/MimicParts.tsx`
- `src/frontend/src/pages/assets/health/mimic/MimicReadings.tsx`
- `src/frontend/src/pages/assets/health/mimic/StationSchematic.module.css`
- `src/frontend/src/pages/assets/health/mimic/StationSchematic.tsx`
- `src/frontend/src/pages/assets/health/mimic/UnitMimic.module.css`
- `src/frontend/src/pages/assets/health/mimic/UnitMimic.tsx`
- `src/frontend/src/pages/assets/health/mimic/UnitSchematic.tsx`
- `src/frontend/src/pages/assets/health/mimic/mimicVisuals.module.css`
- `src/frontend/src/pages/assets/health/mimic/mimicVisuals.ts`
- `src/frontend/src/pages/assets/health/readingPresentation.ts`
- `src/frontend/src/pages/assets/locations/LocationBrowser.tsx`
- `src/frontend/src/pages/assets/locations/LocationWorkspace.tsx`
- `src/frontend/src/pages/assets/locations/MachinePageSummary.tsx`
- `src/frontend/src/pages/assets/locations/locations.module.css`
- `src/frontend/src/tables/assets/ComponentFacts.tsx`
- `src/frontend/src/tables/assets/MachineComponentTable.module.css`
- `src/frontend/src/tables/assets/MachineComponentTable.tsx`

Tests (all five touched files):

- `src/frontend/src/pages/assets/health/Timestamp.test.ts`
- `src/frontend/src/pages/assets/health/readingPresentation.test.ts`
- `src/frontend/tests/s8/pui_ui_owner_followup.spec.ts`
- `src/frontend/tests/s8/pui_ui_round2_captures.spec.ts`
- `src/frontend/tests/s8/pui_ui_followup2.spec.ts`

The owner location spec now proves expansion through native treeitems and the
hidden-code/tooltip structure. Unit expectations assert the new unit tooltip and
non-interactive timestamp. The capture spec adds 19–21 and clears overlays. The
new focused spec verifies behaviour, geometry, contrast and keyboard reach.
`tsconfig.s8.json` includes it. HANDOFF, sprint backlog and contract evidence are
updated without changing parent acceptance dispositions.

Owned browser `r6-uir2-browser-3012` and harness15250 are stopped; cleanup recorded
in `followup2/runtime-cleanup.json`. No Git writes, Azure/data-env reads, other
worktrees, other containers or other ports. Full Mac suites and human review
remain with the coordinator. Nothing from A–G is left unimplemented.

## Follow-up 3

Built on `dedc7a678`. All twelve confirmed findings are accepted and repaired;
these notes supersede conflicting historical implementation claims above.
Native architecture, permissions, source facts and O27 live presentation remain.

| Finding | Source verification and resulting change | Files (under frontend src unless stated) |
| --- | --- | --- |
| 1 / P9 selection | Selection only changed state and the unit followed the alarms. The selected unit now precedes Threshold alarms; its heading receives focus and scrolls to block start, using auto under reduced motion. A small scroll margin prevents fractional clipping on phone. | health/PumphouseMimic.tsx, mimic/UnitMimic.tsx |
| 2 / S8 alarm absence | The three predicates differed; the station predicate allowed unmapped bays. One `knownNoActiveAlarms` requires a nonempty, completely mapped bay list, null station/bay alarms and no open/acknowledged anomaly. All three consumers use it; unit scope conservatively respects the whole supplied station payload. | health/PumpMimic.tsx, mimic/StationSchematic.tsx, UnitMimic.tsx, mimicVisuals.ts |
| 3 / H6 exact boundary | Owned max-width:48em overlapped Mantine min-width:48em. Every owned stylesheet now uses phone maximum 47.99375em; the relevant JS queries and PanelGroup match. Refresh and both filters are verified at 767/768/769 px. | owned asset/table/item CSS, panels/PanelGroup.css.ts and .tsx, mimic/UnitSchematic.tsx |
| 4 / X4 generic scope | The dense phone spacing, 44px tabs, mask and selected-tab scroll affected generic detail panels. New `denseTabs` defaults false and is enabled only by MachineDetail. Generic machine-index panels keep native padding and no mask. | panels/PanelGroup.tsx and .css.ts, assets/MachineDetail.tsx |
| 5 station KPIs | Running count lacked its supplied bay denominator; derived totals used Σ. It now reads 2 of 14 in the fixture. Plain total labels retain Calculated total in the tooltip; unavailable totals remain a dash. | mimic/StationSchematic.tsx |
| 6 station drawing | Fixed-width bays overflowed laptop containers; the legend depended on current state and stale hatching dominated. Slots now adapt to measured container width, with two rows below 1100px container width; observation refreshes do not trigger measuring effects. Stale hatch uses thinner, wider-spaced gray strokes and dimmed source-retained glyphs. All five states and two alarm shapes always appear with equal-weight labels. Phone uses edge fades and an initial horizontal offset, with no swipe caption. | health/PumphouseMimic.tsx, mimic/StationSchematic.tsx and .module.css |
| 7 / X2 motion and acknowledgement | Pulsing alone did not distinguish unacknowledged alarms without motion, and flow could not be paused. Static double rings/outline identify unacknowledged alarms; acknowledged alarms retain a single ring. A quiet per-view pause button controls flow/pulses, exposes aria-pressed and stays paused under OS reduced motion. Marker 5 retains geometry clearance with the added ring. | new mimic/MimicMotion.tsx, mimicVisuals.module.css, MimicParts.tsx, StationSchematic.tsx; health/PumpMimic.tsx and PumphouseMimic.tsx |
| 8 Health | Heading sizes differed, observed ages wrapped, severity count words were raw keys, and coverage/search copy repeated. Titles share h4 scale, Observed shows a nowrap relative age with the full instant/zone in Timestamp's tooltip, severity words go through Lingui, designation prose lives only in the coverage badge tooltip, Search has an aria-label, and Component filter has All components. | health/HealthSummary.tsx, MachineHealthPanel.tsx, SignalTable.tsx, Timestamp.tsx, common.tsx |
| 9 Machines/location | Duplicate headings, permanent Move selected, repeated input labels and an unstyled pressed chip were present. Removed duplicate root/All Machines headings, gated Move to selection, changed input labels to aria-labels, and added a token-based pressed style. Final capture also exposed a stuck phone drill-down tooltip; controlled focus/hover state now dismisses on Escape/blur/navigation, with code/reason still reachable on focus. | locations/LocationWorkspace.tsx, LocationBrowser.tsx, locations.module.css |
| 10 Parts | Native pagination appeared for three results, the stock header wrapped, fit tooltip children relied on Badge defaults, and links used a different blue. Opt-in `hideSinglePagePagination` suppresses the footer only when count <= actual page size, without changing API pagination. Header is In stock; both fit badges explicitly render span; links use the shared primary 7/4 tokens. | tables/assets/MachineComponentTable.tsx and .module.css, ComponentSlotDetail.tsx; components/tables/InvenTreeTable.tsx; lib/types/Tables.tsx |
| 11 consistency | Refresh controls differed, Health/mimic ages had separate duration rules, and count/warning styling diverged. Shared RefreshAction uses a 36px default action (44px phone) through an opt-in native table refresh slot. Visible instants use Timestamp; shared relativeTime supplies live/stale/future/age rules. Count badges use ·. OperationalTokens supplies one warning colour for badge, marker and value; browser checks compare their actual rendered RGBA values in both themes. | new buttons/RefreshAction.tsx and .module.css, health/relativeTime.ts and OperationalTokens.module.css; health/StatusBadge.tsx, common.tsx, mimic/FreshnessLabel.tsx, MimicReadings.tsx, mimicVisuals.module.css; items/MachineAlarmBadges.tsx; native table header/types |
| 12 report | Corrected the installed/lockfile @mantine/core version to 9.6.1, removed the water-fill claim, qualified the earlier stock-header claim, and removed the unsupported PumpMimic-specific tooltip-positioning claim. The tank has walls and no invented level fill. | this report |

Verification is targeted Linux Chromium fixture evidence, not deployment or
product acceptance. Logs are in `data/devscratch/ui-r2/followup3/`:

- **16 unique focused browser cases passed**, across light/dark, after corrective
  replays (`focused.log`, `station-corrective.log`, `colour-motion.log`,
  `location-corrective.log`). They cover 1280/1366/1024 station fit, phone/desktop
  P9 focus/order, animation pause/reduced motion and static acknowledgement,
  identical warning colours, 767/768/769 Health controls, coverage tooltip,
  generic/dense tabs, machine selection/pressed style, Parts 3/30-result
  pagination, complete phone card, and phone location reason dismissal.
- **78/78 unit tests passed** in eight targeted files (`units-final.log`).
  `tsc --noEmit` passed for `tsconfig.json` and touched `tsconfig.s8.json`.
- Lingui extract and TypeScript compile passed. All **117 locale files** were
  restored byte-for-byte (`lingui-restore.json`); no locale diff remains.
- `PATH=/opt/homebrew/bin:$PATH prek run --files ...` passed for changed files
  after formatting (`prek.log`). Tracking validation also passed: 58 work items,
  95 screen dispositions and 26 contracts (`tracking.log`).
- An extra unchanged `ValueTooltip.test.ts` run found **one pre-existing stale
  assertion**: `focusable=false` is expected to produce a button/tabindex -1,
  whereas the baseline source intentionally renders a plain span. Its other
  three tests passed. Neither that test nor its source was changed in this pass;
  the changed CSS only aligns the phone breakpoint. Recorded in
  `units-related.log`; unrelated stale wording/clock Playwright specs are untouched.
- Initial browser failures are retained honestly: a new selector matched both
  totals (corrected to assert each); phone focused headings were fractionally
  clipped (fixed with scroll margin, strict ratio assertion retained); capture
  20 exposed the stuck tooltip (fixed in source and regression-tested). No
  assertion was weakened to produce a final image.

Fresh captures: `data/devscratch/ui-r2/final3/01-…png` through `23-…png`, using
unchanged 01–21 names plus `22-health-light-tablet-768x1024.png` and
`23-mimic-station-light-desktop-1366x768.png`. The final 23-case matrix passed (`followup3/captures.log`); every viewport PNG
and all three full-height pump PNGs were opened and visually inspected
(`followup3/visual-review.json`). Three full-height pump PNGs are under `full/`.
The source digest and exact 43-source-file inventory are in
`followup3/source-manifest.json`; each viewport's JSON records that revision.

Source files changed: 43, covering the shared RefreshAction/table opt-ins,
PanelGroup opt-in, owned breakpoint CSS, Health/Timestamp/relative time/status
presentation, mimic selection/layout/motion/absence, location/Machines controls,
and Parts. Test files changed (six, separately from source):

- `src/frontend/tests/s8/pui_ui_followup3.spec.ts` (new behavior regressions).
- `src/frontend/tests/s8/pui_ui_round2_captures.spec.ts` (adds 22/23).
- `src/frontend/src/pages/assets/health/mimic/mimicVisuals.test.ts`.
- `src/frontend/src/pages/assets/health/relativeTime.test.ts` (new shared rule).
- `src/frontend/src/pages/assets/health/Timestamp.test.ts`.
- `src/frontend/src/pages/assets/health/monitoringComponents.test.ts` (asserts
  shared hour formatting and the new coverage-tooltip structure).

`tsconfig.s8.json` includes the new focused spec. HANDOFF, backlog and contract
evidence retain their parent acceptance dispositions. No Git writes, Azure or
other worktree/container/port access; full host suites and human review remain
with the coordinator.

Final visual review: 01–03 and 23 show clear alarm rings, all fourteen bays at
laptop/desktop widths, a calmer stale glyph and a stable legend. 04–06 preserve
the large painted pump (814px / 1406px content at 1440px, about 58%) and all 13
desktop callouts; phone starts its callouts with the two alarms. 07–10/19–21 show
complete first phone machine content, unclipped count chips, clean tree names,
one root title and a tooltip-free phone drill-down. 11–13/22 show compact Health,
matching title scales, labelled missing states, shared ages and all 768px
controls. 14–18 show In stock on one line, baseline-aligned phone facts and
complete first Parts cards. Full pump images also retain the legends, alarm
actions and reading groups without overlap. Tablet Health retains native
horizontal table scrolling; this is not a claim that every column fits 768px.

`final-audit.json` confirms all 23 capture revisions match final source digest
`f3efc8d37f3c`, source unchanged after capture, and locales still bytewise restored.
Owned browser container (auto-removed after stop) and harness are stopped; neither
3012 nor 15250 has a listener (`runtime-cleanup.json`). Findings 1–12 have no
remaining implementation work; full Mac suites, the separate stale-spec update
and human review remain with the coordinator.

## Follow-up 4

Built on `59e6f6eaa`. Read all 92 audit findings and verified against current source; prior repairs were retained. This is source/fixture evidence only, not deployed or human product acceptance. No database data, owner localhost, Azure connection, other worktree or other container was accessed.

Parts uses a shared truthful assignment basis and additive typed provenance projection; Details, Health, Performance, equipment, maintenance and fault presentation now omit the listed developer prose. Missing and failed states remain distinct. Unconfirmed thermal pairs are hidden; supplied negative readings remain unchanged. The retained O27 recorded-as-live policy is unchanged.

The two shared InvenTree changes are pure improvements: DetailsTable wraps on word boundaries and accepts an optional className (phone stacking is AIMMS-only); AttachmentTable has human column titles, wrapping comments/names and Timestamp. Asset-machine barcode wording is scoped to that model. MobileAppView wording changes only the existing gate, not access. Machine APIs add read-only presentation fields; no schema migration or data update is introduced.

| Finding | Disposition | Source check / result |
| --- | --- | --- |
| 1 | Skipped | Stored test/demo machines require owner data changes; no records read or changed. |
| 2 | Done; data skipped | LocationWorkspace root overview omits codes; stored site names/codes remain owner decisions. |
| 3 | Skipped | Stored serials require owner-supplied identities; Details now handles absent serials truthfully. |
| 4 | Already done | LocationWorkspace already passes compactCoverage; Normal retains the partial icon and coverage tooltip. |
| 5 | Done | MachinePageSummary renders 25 of 111 without the This page prefix. |
| 6 | Already done | Selected-location totals explanation already lives in its tooltip. |
| 7 | Done | LocationWorkspace uses placement paths relative to the selected ancestor, collapses the chooser, and preserves a Choose location action/focus return. |
| 8 | Skipped | Placement changes are stored data and need the owner. |
| 9 | Skipped | Superuser banner is account configuration; retained, including in superuser fixtures. |
| 10 | Skipped | Navigation rulesets are operator configuration; no global role/nav changes. |
| 11 | Done | MachineIndex gates Equipment Registry by its existing work_order view permission and uses a secondary button. |
| 12 | Done | MachineDetail removes the duplicate header placement line; Details location card remains. |
| 13 | Done | MachineDetail removes Legacy location label. |
| 14 | Done | MachineDetail renders In service / Out of service from active, or a missing reason. |
| 15 | Done | MachineDetail supplies Manufacturer, Model and Serial icons explicitly. |
| 16 | Done | Missing Manufacturer/Model/Serial/Name/Description use shared keyboard-accessible missing values. |
| 17 | Done | DetailsTable uses word-boundary wrapping instead of lineBreak:anywhere. |
| 18 | Done | MachineDetails.module.css stacks AIMMS labels and values at full phone width, preserving tr boxes. |
| 19 | Skipped | Stored descriptions require owner edits; no fabricated replacement descriptions. |
| 20 | Skipped | Stored manufacturer/model/duty descriptions require owner confirmation. |
| 21 | Already done | StationSchematic adaptive slots already fit all 14 bays at desktop; final4 includes 1366x768. |
| 22 | Done | MimicAlarmCoverage keeps honest No active alarms and moves unconfigured-limit counts to its info tooltip. |
| 23 | Done; labels already done | Total labels already lacked Sigma; StationSchematic now explains missing contributor counts. |
| 24 | Done | Pumphouse layout requests cusec. Checked-in dictionary review approvals report cusec (Cedar Creek 14 flow approvals; other station packs also cusec); migration 0015 defines cubic-foot/second. Existing backend Pint conversion remains authoritative; no relabelling of raw magnitudes. |
| 25 | Done | StationSchematic and PumpMimic use Layout pending plant approval / Schematic layout in one source/layout tooltip. |
| 26 | Done | MimicReadings collapsed station summary shows the number of readings. |
| 27 | Done | Forebay level uses two decimal places in readingPresentation, mimic format and Performance. |
| 28 | Skipped | Source record name remains unchanged and is honestly available in source tooltips/status. |
| 29 | Done; data skipped | signalLabel and reviewed parameter patterns supply Pumps running, Discharge flow and Active/Input power labels for known pc/dv/pmw/sl keys. Stored renaming remains owner work. |
| 30 | Done; bare value already done | readingPresentation omits unit-missing explanations for known dimensionless power factor/counts and explicitly dimensionless units. |
| 31 | Skipped | Vibration dictionary units remain unchanged; unconfirmed units stay neutral with a reason. |
| 32 | Done | readingPresentation maps declared I/R to Idle/Running and motor 0/1 to Off/On; unknown codes stay Unknown; raw code is tooltip-only. |
| 33 | Done | HealthSummary requests compact Normal; coverage counts and partial/full distinction remain in its tooltip/icon. |
| 34 | Done; prose already removed | Current condition keeps badge and observation; coverage explanation stays in the condition tooltip. |
| 35 | Done | SignalTable component options/details show names; internal codes remain filter identities/search metadata. |
| 36 | Already done | SignalTable observed cells already use relative-only Timestamp with the complete instant/zone in the tooltip. |
| 37 | Done | Phone SignalTable hides an unconfigured unknown State cell on closed cards; Details retains the labelled reason. |
| 38 | Done | HealthHistoryProvenance is a single history-information tooltip; no expandable developer read report. |
| 39 | Done | SignalTrendChart removes selected-window/storage/projection paragraphs; chart, gaps and failures remain. |
| 40 | Done | SignalTrendChart drops sample/extent chips and uses Investigate this period. |
| 41 | Done | Blank units have no axis title; known status axes label Idle/Running or Off/On. Supplied exceptional values remain represented. |
| 42 | Done | RetainedHealthEvidence is Fault snapshots without preservation prose. |
| 43 | Done | MonitoringContinuityNotice no longer renders the developer monitoring paragraph on AIMMS tabs. |
| 44 | Done | HealthSourceStatus uses Status/Reporting, Stale after in minutes, Timestamp dates and quiet missing-error dashes. |
| 45 | Done | Performance uses compact HistoryProvenance info; full investigation-specific provenance remains available in investigation views. |
| 46 | Done | Compact Performance history omits last poll attempt; current readings and source failures retain their own states. |
| 47 | Done | Empty-history tooltip deduplicates Pump identities from known pump keys instead of repeating parameter names; no zero-equivalence paragraph. |
| 48 | Done | TimeRangeControl shows live/read status and Updated time; sampling facts move to the tooltip. WindowStatus keeps updating/failure behavior without clock prose. |
| 49 | Done | PerformancePanel and StationPerformance put Investigate this period in the toolbar. |
| 50 | Done | StationPerformance/KpiStrip render running count of total bays and omit dimensionless unit/window filler. |
| 51 | Done | StationPerformance requires usable history from every bay for totals; absent contributors yield a dash/reason; incompatible units have a separate reason. |
| 52 | Done | axisPresentation/TrendChart floor known nonnegative axes only when supplied data are nonnegative, with integer count ticks. Actual negative readings remain untouched. |
| 53 | Done | StationPerformance, OperationSection and RelationChart remove the listed chart-explaining captions. |
| 54 | Done | Performance workspace/clock/KPI paragraphs removed. MachineDetail passes API-projected plant_timezone through DisplayClockContext to all tabs. |
| 55 | Done | Performance exceptions use No exceptions and an info tooltip for assessment coverage; failures/poor-quality/alarms remain visible. |
| 56 | Done | KpiStrip shows bare vibration with Unit not confirmed tooltip and no unit text for power factor. |
| 57 | Done | SensorGroupCard uses a Unit not confirmed badge/tooltip instead of raw-unit paragraphs. |
| 58 | Done | SensorGroupCard keeps Highest now and limit state, omitting mean/spread/window-peak chips. |
| 59 | Done | ThresholdKey lists actual configured warning/critical bounds and units without engineering prose. |
| 60 | Done | SensorGroupCard reserves plot margins and 34px sensor rows; shortened sensor ticks have full SVG title tooltips. |
| 61 | Done | SensorHeatmap drops the orphan mean-cell caption. |
| 62 | Done | ThermalSections hides unconfirmed water/air temperature differences. Current dictionary projection supplies no explicit pair relationship; inlet/outlet channels remain separate. |
| 63 | Done | OperationSection titles the valve-only panel Valve position when no pressure series exists. |
| 64 | Done | ChannelNavigator removes its channel-mapping paragraph. |
| 65 | Skipped | Signed source readings remain unchanged; deadbands require owner/source engineering decisions. |
| 66 | Done | EquipmentViewPanel defaults to schematic; explicit view=model preserves access to the conceptual vertical reference model. |
| 67 | Done | EquipmentScene provides one Reference model badge with a tooltip; repeated disclaimers removed. |
| 68 | Done | Equipment/PositionInspector read-time captions removed; inherited machine plant clock remains available for actual timestamps. |
| 69 | Done | Equipment/PositionInspector remove listed prose; model rotation/zoom help lives in the reference badge tooltip; component/work controls remain. |
| 70 | Done | referenceListOrder sorts by marker number, with a unit assertion for 0 through 8. |
| 71 | Done | Reference-position and Ask about this position controls render only in model view. |
| 72 | Done | UnitMimicHeader uses the supplied machine-name leaf, falling back to Pump rather than inventing a machine name. |
| 73 | Done | MimicParts groups by quantity and unit; unconfirmed vibration groups identify the quantity and keep the unit reason in the tooltip. |
| 74 | Done | UnitMimic uses Other readings. |
| 75 | Done | UnitMimic renders one compact legend with state and reading cues. |
| 76 | Done | MachineComponentTable/ComponentFacts/componentPresentation use Verified only for verified status, otherwise truthful provenance-based labels; Source column merged. |
| 77 | Done | Read-only provenance_kind API projection and provenance_display fallback avoid raw hashes; basis tooltip includes supplied import date/plain reason. |
| 78 | Done | Reference design / Inferred from tags / Tag review / Pending check are shared by table, drawer and reference inspector. |
| 79 | Done | ComponentSlotDetail removes code/logical-group/provenance paragraphs; codes stay in name tooltips. Review enums become plain language; empty fit uses No fit recorded. |
| 80 | Done; data skipped | InstalledPartsPanel titles recorded quantities Reference parts list; stored notes remain unchanged for the owner. |
| 81 | Done | MachineComponentTable hides Fitted item when the loaded page contains no current fit; reference position is pump-only; phone Details preserves secondary facts. |
| 82 | Done | MachinePartTable uses owned phone cards with wrapping notes and labelled facts. |
| 83 | Skipped | Catalogue Part names require owner normalisation; no stored catalogue data edited. |
| 84 | Done | MachineMaintenancePanel removes listed outcome/open/history explanations; empty state is No open work. |
| 85 | Done | MaintenanceRecordTable uses a dash with No linked work order only for API link state none; withheld/older unknown projections say Work order link not available. Additive serializer projection preserves permission boundaries. |
| 86 | Done | MaintenanceRecordTable missing component/type/outcome/downtime/follow-up/technician facts have keyboard reasons; actual zero downtime remains zero. |
| 87 | Done | Maintenance dates use nowrap Timestamp. Civil dates preserve the stored calendar day across plant zones. |
| 88 | Skipped | Maintenance-record provenance requires owner review; no history records changed. |
| 89 | Done | FaultOccurrences drops read/projection/monitoring prose. Event codes/IDs are in a compact tooltip; visible titles describe events; severity columns keep words intact. |
| 90 | Done | FaultHistoryPanel shows Last maintenance using Timestamp; cadence/declared-code paragraphs removed; known fault codes have plain descriptions. |
| 91 | Done | FaultHistoryPanel drops recorded-aggregates/approved-scopes/verified-closeout source captions. |
| 92 | Done a/b/d; c skipped | AttachmentTable titles Attachment/Comment/Uploaded/Size, wraps names/comments, uses Timestamp and missing reasons. Mobile gate is neutral; asset-machine barcode menu says Machine QR label / Link tag. Stored attachment names/comments remain owner data. |

Verification passed on final source digest `1c1c716ede7e`:

- `pui_ui_followup4.spec.ts`: 9/9 targeted fixture cases. Covers keyboard missing/link reasons, Details identity, truthful Parts bases, schematic default/reference toggle, compact Performance/thermal-pair absence, non-overlapping sensor limit labels, fault presentation, attachment wrapping and complete-contributor station totals.
- Unmodified `pui_ui_round2_captures.spec.ts`: 23/23 passed; final4 metadata carries the final source digest. Both phone Parts first cards remain above the fold; 768px filters/Refresh and all 14 station bays at 1366px remain present.
- Vitest: 112/112 across 14 targeted unit files (Performance units plus changed provenance/reading/date/mimic/reference modules). Pure backend provenance unittest: 2/2, with no Django/DB initialization. Changed backend files also parse; Ruff passes. Backend API/database integration tests were deliberately not run under the no-database-write constraint.
- TypeScript: `tsconfig.json` and all eight `tsconfig.s*.json` pass; S8 rechecked after the final sensor-label adjustment. Lingui extract and compile pass; all 117 locale files restored bytewise. Changed-file `prek` and `git diff --check` pass. Logs: `data/devscratch/ui-r2/followup4/`.
- Renders use Linux Chromium in the owned WS3012 container and fixture-only Vite15250 harness. All 31 viewport PNGs and three full pump PNGs were opened and inspected. Final visual inspection fixed basis truncation, severity word breaks, an orphan coverage icon and overlapping sensor-limit labels. Full host suites, real deployment checks and human review remain with the coordinator.

Captures: `data/devscratch/ui-r2/final4/01-…23-*.png` retain the requested names. Additional captures are `24-details-desktop-light.png`, `25-details-phone-light.png`, `26-performance-desktop-light.png`, `27-maintenance-desktop-light.png`, `28-fault-history-desktop-light.png`, `29-attachments-desktop-light.png`, `30-equipment-view-desktop-light.png` and `31-performance-sensors-desktop-light.png`; pump full pages are in `final4/full/`. SHA256 review inventory: `followup4/capture-review.json`.

All stored-data findings remain owner work: 1, 2 (renaming/archive), 3, 8, 19, 20, 28 (source name), 29 (stored signal names), 31, 65, 80 (notes), 83, 88 and 92c. Configuration 9/10 is also retained. No frontend fix was substituted for an invented measurement, fitted identity or verification status.

Source/config files changed (73):

- `src/backend/InvenTree/assets/equipment_positions.py`
- `src/backend/InvenTree/assets/provenance.py`
- `src/backend/InvenTree/assets/registry_api.py`
- `src/backend/InvenTree/assets/serializers.py`
- `src/backend/InvenTree/machine_health/layouts/pumphouse.layout.json`
- `src/frontend/lib/types/Assets.tsx`
- `src/frontend/src/components/details/Details.tsx`
- `src/frontend/src/components/items/ActionDropdown.tsx`
- `src/frontend/src/pages/assets/FaultHistoryPanel.tsx`
- `src/frontend/src/pages/assets/FittedIdentity.tsx`
- `src/frontend/src/pages/assets/InstalledPartsPanel.tsx`
- `src/frontend/src/pages/assets/MachineDetail.tsx`
- `src/frontend/src/pages/assets/MachineDetails.module.css`
- `src/frontend/src/pages/assets/MachineIndex.tsx`
- `src/frontend/src/pages/assets/equipment/EquipmentScene.tsx`
- `src/frontend/src/pages/assets/equipment/EquipmentViewPanel.tsx`
- `src/frontend/src/pages/assets/equipment/PositionInspector.tsx`
- `src/frontend/src/pages/assets/equipment/machinePositions.ts`
- `src/frontend/src/pages/assets/equipment/referenceParts.ts`
- `src/frontend/src/pages/assets/faults/FaultOccurrences.tsx`
- `src/frontend/src/pages/assets/health/HealthHistoryProvenance.tsx`
- `src/frontend/src/pages/assets/health/HealthSourceStatus.tsx`
- `src/frontend/src/pages/assets/health/HealthSummary.tsx`
- `src/frontend/src/pages/assets/health/MonitoringContinuity.tsx`
- `src/frontend/src/pages/assets/health/PumpMimic.tsx`
- `src/frontend/src/pages/assets/health/RetainedHealthEvidence.tsx`
- `src/frontend/src/pages/assets/health/SignalEvidence.tsx`
- `src/frontend/src/pages/assets/health/SignalReading.tsx`
- `src/frontend/src/pages/assets/health/SignalTable.module.css`
- `src/frontend/src/pages/assets/health/SignalTable.tsx`
- `src/frontend/src/pages/assets/health/SignalTrendChart.tsx`
- `src/frontend/src/pages/assets/health/SourcePollStatus.tsx`
- `src/frontend/src/pages/assets/health/Timestamp.tsx`
- `src/frontend/src/pages/assets/health/mimic/MimicAlarmCoverage.tsx`
- `src/frontend/src/pages/assets/health/mimic/MimicParts.tsx`
- `src/frontend/src/pages/assets/health/mimic/MimicReadings.tsx`
- `src/frontend/src/pages/assets/health/mimic/StationSchematic.tsx`
- `src/frontend/src/pages/assets/health/mimic/UnitMimic.tsx`
- `src/frontend/src/pages/assets/health/mimic/format.ts`
- `src/frontend/src/pages/assets/health/readingPresentation.ts`
- `src/frontend/src/pages/assets/investigation/HistoryProvenance.tsx`
- `src/frontend/src/pages/assets/locations/LocationWorkspace.tsx`
- `src/frontend/src/pages/assets/locations/MachinePageSummary.tsx`
- `src/frontend/src/pages/assets/locations/locations.module.css`
- `src/frontend/src/pages/assets/maintenance/MachineMaintenancePanel.tsx`
- `src/frontend/src/pages/assets/performance/ChannelNavigator.tsx`
- `src/frontend/src/pages/assets/performance/KpiStrip.tsx`
- `src/frontend/src/pages/assets/performance/PerformancePanel.tsx`
- `src/frontend/src/pages/assets/performance/RelationChart.tsx`
- `src/frontend/src/pages/assets/performance/SensorGroupCard.tsx`
- `src/frontend/src/pages/assets/performance/SensorHeatmap.tsx`
- `src/frontend/src/pages/assets/performance/StationPerformance.tsx`
- `src/frontend/src/pages/assets/performance/ThresholdKey.tsx`
- `src/frontend/src/pages/assets/performance/TimeRangeControl.tsx`
- `src/frontend/src/pages/assets/performance/TrendChart.tsx`
- `src/frontend/src/pages/assets/performance/WindowStatus.tsx`
- `src/frontend/src/pages/assets/performance/axisPresentation.ts`
- `src/frontend/src/pages/assets/performance/format.ts`
- `src/frontend/src/pages/assets/performance/kpis.ts`
- `src/frontend/src/pages/assets/performance/parameters.ts`
- `src/frontend/src/pages/assets/performance/sections/OperationSection.tsx`
- `src/frontend/src/pages/assets/performance/sections/ThermalSections.tsx`
- `src/frontend/src/tables/assets/ComponentFacts.tsx`
- `src/frontend/src/tables/assets/ComponentSlotDetail.tsx`
- `src/frontend/src/tables/assets/MachineComponentTable.tsx`
- `src/frontend/src/tables/assets/MachinePartTable.tsx`
- `src/frontend/src/tables/assets/MaintenanceRecordTable.tsx`
- `src/frontend/src/tables/assets/ReferenceParts.module.css`
- `src/frontend/src/tables/assets/componentPresentation.ts`
- `src/frontend/src/tables/general/AttachmentTable.module.css`
- `src/frontend/src/tables/general/AttachmentTable.tsx`
- `src/frontend/src/views/MobileAppView.tsx`
- `src/frontend/tsconfig.s8.json`

Test files changed (8):

- `src/backend/InvenTree/assets/tests/test_provenance_presentation.py`
- `src/frontend/src/pages/assets/equipment/referenceParts.test.ts`
- `src/frontend/src/pages/assets/health/Timestamp.test.ts`
- `src/frontend/src/pages/assets/health/readingPresentation.test.ts`
- `src/frontend/src/pages/assets/performance/axisPresentation.test.ts`
- `src/frontend/src/pages/assets/performance/monitoringHelpers.test.ts`
- `src/frontend/src/tables/assets/componentPresentation.test.ts`
- `src/frontend/tests/s8/pui_ui_followup4.spec.ts`

No old wording/clock Playwright spec was edited. The numbered capture spec is unchanged. Unit expectations changed only for intentional new contracts: blank axis units, numeric component order and safe provenance. No git write commands or database mutations were run.

Owned `r6-uir2-browser-3012` and Vite15250 stopped; both listener checks are clear.

## Follow-up 5

Built on `f5455e279`; source/test digest `45533030b695` (full manifest: `data/devscratch/ui-r2/followup5/source.json`).

- Station and pump Performance share a full-width auto-fit grid with 220px minimum cards and natural content heights. Values retain actual units; count reads `1 of 14`. Window ranges and limit details move into label tooltips. Stale is a short badge with keyboard-accessible detail; timestamp/age stays on a separate single line. Future clock offsets remain in the tooltip, with visible age clamped to zero. O27 recorded-live handling is retained.
- One live switch now also reports Paused, Stale or Read failed; no duplicate Live badge when paired with it.
- All Machines, Unassigned and selected-location lists request active machines by default. Show inactive includes both statuses. Roots, branches and location search request `active_only=true`; Show archived locations includes archived branches. Both flags persist in URL/detail-return state. Query keys, cached tree signatures, paging and selected rows follow the scope. Leaving archive inclusion clears the selected branch to avoid a hidden selection.
- Location machine-list and detail APIs now accept optional `active` before pagination/counts. Selected-location machine/open-work totals use the same active population as its list. Omission preserves existing API behavior. Existing archive-descendant exclusion is reused.
- Blank Manufacturer, Model and Serial already render dashes with accessible reasons, including Serial not recorded. Verified all three using fixtures and keyboard focus; no Details source change needed. Owner data was neither read nor changed.

| Verification on final source | Result |
| --- | --- |
| `pui_ui_followup5.spec.ts` | 11/11: compact light/dark/phone KPIs, stale/future timestamps, count units, single Live switch, failed read, active counts, archived root/search/tree/drill-down and cached scope toggles, blank identity reasons |
| `pui_ui_followup4.spec.ts` | 9/9; only capture-directory selection changed |
| Unchanged `pui_ui_round2_captures.spec.ts` | 23/23 |
| Vitest | 78/78 across machineListState, locationTree, monitoringHelpers and KpiStrip |
| TypeScript | All 9 projects pass (`tsconfig.json` and every existing `tsconfig.s*.json`); app/S8 rechecked after final edits |
| Lingui | Extract/compile pass; all 117 locale files restored bytewise |
| Python | Changed API parses; Ruff passes through prek. Database integration not run |
| Hooks | Changed-file prek and whitespace checks pass |

Fresh captures: `data/devscratch/ui-r2/final5/01-*.png` through `31-*.png`, plus `32-station-performance-phone-light.png`, `33-station-performance-desktop-light.png`, `34-station-performance-desktop-dark.png`, `35-station-performance-stale-desktop-light.png` and three full-page pump views in `final5/full/`. All **38 PNGs** inspected individually, including Health at 768px and station at 1366px. New Performance views show full-width compact cards, single-line time/age and a short Stale badge without stretched empty height. Review hashes: `data/devscratch/ui-r2/followup5/capture-review.json`. Linux Chromium used only fixture-intercepted pages on WS3012/harness15250. Fixture names/harness annotations are not production copy. Logs in `data/devscratch/ui-r2/followup5/`: `browser-final.log`, `tabs-final.log`, `captures-final.log`, `units-final.log`, `types-final.log`, `lingui-final.log`, `prek-final.log`.

Source/configuration files changed (9):

- `src/backend/InvenTree/assets/location_api.py`
- `src/frontend/src/pages/assets/locations/LocationBrowser.tsx`
- `src/frontend/src/pages/assets/locations/LocationWorkspace.tsx`
- `src/frontend/src/pages/assets/locations/machineListState.ts`
- `src/frontend/src/pages/assets/performance/KpiStrip.module.css`
- `src/frontend/src/pages/assets/performance/KpiStrip.tsx`
- `src/frontend/src/pages/assets/performance/StationPerformance.tsx`
- `src/frontend/src/pages/assets/performance/TimeRangeControl.tsx`
- `src/frontend/tsconfig.s8.json`

Test/fixture files changed (6):

- `src/frontend/src/pages/assets/locations/machineListState.test.ts`
- `src/frontend/src/pages/assets/performance/KpiStrip.test.ts`
- `src/frontend/tests/s1/machineExplorerFixtures.ts`
- `src/frontend/tests/s3/stationFixtures.ts`
- `src/frontend/tests/s8/pui_ui_followup4.spec.ts`
- `src/frontend/tests/s8/pui_ui_followup5.spec.ts`

No outdated wording/clock spec edited; numbered capture spec unchanged. No database or git writes, owner localhost captures or real data inspection. Backend database integration, full Mac suites and deployed acceptance remain with the coordinator. Owned `r6-uir2-browser-3012` and Vite15250 stopped. Parent acceptance unchanged.

## Follow-up 6

Built on `1d3c7b513`; all four test-lane findings confirmed against source/evidence and repaired. Frozen source/test digest `d81a18b68e8e` (`data/devscratch/ui-r2/followup6/source.json`).

| Finding | Fix and evidence |
| --- | --- |
| 1. Parts failure badge wraps at 1280×720 | FailedValue keeps its phrase on one line without truncation. Open work receives 14% instead of 10%, balanced by narrower component/catalogue columns. Pump and station native-shell checks prove one text line, badge containment, full header text and right-aligned numeric columns. |
| 2. Hovered phone component link contrast | Machines, Health and Parts share primary-8 light / primary-3 dark link tokens. Rendered 14px hovered component links measure **5.095:1** on gray-1 and **4.894:1** on the dark hover surface; catalogue links also pass 4.5:1. No hex colours introduced. |
| 3. Sensor threshold/category collision | Existing configured threshold labels move to a wrapping key above the scatter plot; vertical limit lines remain. Geometry checks prove the key ends before the plot begins, all three sensor labels remain visible and both configured lines remain, at 390px and 1440px in both themes. |
| 4. Intermittently hidden comparison dropdown | The comparison Accordion's 200ms opening transition temporarily clipped/scrolled its panel, then reset the scroll position and moved the focused trigger below the viewport. That Accordion now opens without a height transition. A small shared hook keeps mode/channels/machine/saved-view inputs comfortably in view on open or channel selection, using one cancellable animation frame and instant scrolling without moving focus. Mantine's portal and detached-trigger protection remain. No refresh-driven measuring or observer added. |

Verification: **12 unique focused browser cases, 68 passing executions**: 36/36 (three complete repeats), 20/20 (five extra repeats of four phone comparison cases), 12/12 final capture run. Each phone comparison case passed nine times, covering channel selection, compatible/incompatible grouping, saved-view restore, mode changes and permitted-machine selection; dark machine comparison explicitly uses reduced motion. **13/13 units**, all **9 TypeScript projects** plus final S8 recheck, Lingui extract/compile with **117 files restored bytewise**, changed-file prek and whitespace checks pass. Logs: `data/devscratch/ui-r2/followup6/{browser-repeat,comparisons-repeat,browser-final,units-final,types-final,types-s8-freeze,lingui,prek-final}.log`. Initial local test-authoring issues (missing parts-list fixture, SVG title matching, duplicate test registration) were corrected before the successful runs; no assertion was removed to accommodate a product failure.

The final **18 PNGs** in `data/devscratch/ui-r2/followup6/captures/` were individually inspected, including hovered phone cards, native-shell pump/station tables, sensor plots and open comparison menus. Hashes: `followup6/capture-review.json`; measured hover colours/ratios: `captures/parts-hover-{light,dark}.json`. Renders came from Linux Chromium in owned `r6-uir2-browser-3012` with fixture-intercepted harness15250; no owner localhost or real data inspected. Full suites/deployed acceptance remain with the coordinator.

Source/config changed (11): `components/items/ValueTooltip.tsx`; `pages/assets/{monitoring,operations}.module.css`; `pages/assets/performance/{ComparisonWorkbench,DisplayPreferences,PerformancePanel,SensorGroupCard}.tsx`; new `pages/assets/performance/useComparisonDropdown.ts`; `tables/assets/MachineComponentTable.{tsx,module.css}`; `tsconfig.s8.json` (paths under `src/frontend/src/`, except the config under `src/frontend/`).

Test files touched (2):

- `src/frontend/tests/s8/pui_ui_followup6.spec.ts` — new focused regressions using current fixture controls; original S2/S3 specs and their outdated helpers remain untouched for the test lanes.
- `src/frontend/src/components/items/ValueTooltip.test.ts` — failure phrase/nowrap assertions; corrected the existing obsolete focusable=false button expectation to prove plain span content with no button or tab stop. Missing/failed/zero assertions remain.

Owned browser container and Vite stopped; ports3012/15250 clear. No database, dependency or git writes. Parent acceptance unchanged.

## Follow-up 7

Built on `a2eff8510`. Fable's round-3 findings were checked against current source. O27 remains: the recorded month is presented as live, while relative ages now say just now only through 10 seconds and otherwise show the actual age. Source disclosure is in the Live control's tooltip and accessible name. This is fixture/source verification; deployment and product acceptance remain with the coordinator.

| Fable fix | Disposition | Source change / evidence |
| --- | --- | --- |
| 1 / P1 | Done | UnitMimic uses its host's inline-size container: three columns only at 1100px; two-column callouts below that, with the drawing above. Desktop card spacing also keeps all 13 callouts above the 1440×900 Equipment fold without shrinking the drawing. Equipment view is covered at 1440 and 1280 in both themes. |
| 2 / F1, P5 | Done | Performance header uses failed-read state or the worst tile freshness, including Live · N stale. Stale tooltips retain source flag, observed instant and read instant. Shared relativeAge uses the 10-second boundary; recorded-source disclosure is restored to Performance and mimic Live tooltips/names. |
| 3 / F2–F3 | Done | Sensor colours use configured limit states; unconfigured readings remain neutral. KPI columns are capped at four with equal row heights; endpoint delta includes amount and actual time span, with no unlabeled arrow. |
| 4 / H2 | Done | ValueTooltip's plain span includes its accessible fact in VisuallyHidden. Relative-only Timestamp/FreshnessText is focusable by default. Dense signal and maintenance rows expose missing reasons through one row information control and non-focusable dashes. |
| 5 / E1–E2, E4 | Done | Fault banner removed; Occurrences and Maintenance history use h4. RefreshAction joins the Occurrences controls; counts become badges. Observed-span explanation, resolution note and the Resolved-does-not-always-mean-cleared caveat are in accessible tooltips. Work lifecycle is beside its link; state badges share 24px height and never wrap. |
| 6 / E3 | Done fallback; dictionary enrichment skipped | Supplied row descriptions or an optional supplied dictionary take precedence; unknown codes stay visible, including non-Cedar-Creek codes. Removed the CC-PUMP regex. There is no fault-description dictionary to project: ApprovedRepairScope.failure_codes is string JSON (`repair/models.py`), the rollup counts those strings (`assets/ai_read.py`), and machine-profile fault codes are validated string lists. Registry DictionaryPoint describes source paths, not fault codes. No null-only API field or invented description added. |
| 7 / W1–W2 | Done | Uniformly absent columns collapse once per loaded maintenance page; reasons remain in the native chooser and columns can be revealed. One information control per row exposes missing facts; zero downtime and explicit false Follow-up remain 0 min and No. One RefreshAction refreshes records and counts; Not verified is a quiet outline badge. |
| 8 / V1–V2 | Done | No-param Equipment view defaults to the schematic when one exists, and falls back to the reference model when none exists. New captures use the default URL; explicit reference-model access remains. |
| 9 / F4, F6, L3, S6 | Done | Per-bay charts share one pump-key colour map. Exceptions use one filtering badge. Selected-location totals appear once with grouped default actions; station header information is merged and pause sits beside Refresh. |
| 10 / T2, S3–S4 | Done | Register import replaces Reference design, including the review-note fallback; tooltip explicitly says not field-verified. Missing derived totals visibly identify non-reporting contributors when the API supplies contributor pointers; an empty contributor list does not justify inventing a count. knownNoActiveAlarms requires a reporting source without an error. Three DB-backed tests pass with migration 0015’s real cusec definition: direct/SI conversion, mixed-unit sums and missing-contributor withholding. |

| Remaining per-screen finding | Disposition / reason |
| --- | --- |
| S1–S2, P3, L1, H1, T1 | Already done in the committed baseline; preserved. |
| S5, P2, P4 | Done: balanced phone station KPI spans; green Normal reading marker uses an outlined ring/check; unit legend groups Pump and Readings with a separator. |
| L2 | Done: desktop summary chips share the toolbar row; phone summary retains wrapping. |
| L4 | Skipped: the location API supplies no per-site alarm rollup; counts are not invented. |
| H3 | Skipped: splitting value/unit cells is a broader table contract change; existing values/units remain honest and readable. |
| H4 | Done: each configured limit stays on one line; tablet horizontal scrolling has an edge fade. |
| T3 | Skipped: station Parts includes child equipment, so the Equipment column is not constant in that scope. |
| D1 | Done compact typography; keyboard focus retained so missing identity reasons remain reachable. A deliberate keyboard focus ring is not removed from product styling. |
| F5 | Skipped: current KPI readings and the selected historical trend have different origins; a historical endpoint must not replace a current reading. |
| F7 / leftover breakpoint | Done: phone range control has horizontal scroll/edge cue; TimeRangeControl uses 47.99375em. |
| A1 | Skipped: there is no attachment row-detail surface to hold clipped comments. Full comments/names remain available instead of hiding information. |

Verification:

| Check | Actual result |
| --- | --- |
| Targeted Vitest | 136/136 across 17 files pass (112 changed-module cases and 24 retained trend-sample/supplier cases) |
| Focused Playwright | 25/25 new focused cases pass; 4/4 Equipment cases rechecked after the final card-height adjustment; unchanged 23/23 capture cases and 1/1 Attachments case pass |
| Types | All 9 TypeScript projects pass; app/S8 rechecked after final edits |
| Backend cusec total | 3/3 Django TestCase cases pass in an isolated SQLite test database after all 982 migrations; system checks pass |
| Lingui | Extract and compile pass; all 117 locale files restored bytewise |
| Hooks | All changed files pass prek and whitespace checks |
| Captures | 49 PNGs in `data/devscratch/ui-r2/final7/` individually inspected, including 01–39, additional themes/default Equipment widths, reference-model fallback, register basis and three full pump views |

Final browser runs: 49 unique cases and 53 passing executions (25 focused + 4 Equipment repeats + 23 numbered captures + 1 unchanged Attachments). Logs in `data/devscratch/ui-r2/followup7/`: `browser-final.log`, `equipment-final.log`, `captures-final.log`, `attachment-final.log`, `units-final.log`, `types-final.json`, `types-freeze.json`, `types-s8-last.log`, `lingui-result.json`, `backend-linux-test.log`, `prek-final.log`. Initial test-authoring corrections did not remove product assertions; the tablet overflow and final contrast/card-height findings were fixed before final checks.

CMMS captures: `27-maintenance-desktop-light.png`, `28-fault-history-desktop-light.png`, `36-maintenance-desktop-dark.png`, `37-fault-history-desktop-dark.png`, `38-fault-history-phone-light.png`, `39-fault-history-phone-dark.png`. They show the collapsed maintenance columns, real zero/No facts, single Refresh, equal Fault history sections, data-supplied descriptions/raw-code fallback and contained phone badges. Equipment default-URL and mixed-freshness Performance captures are produced by the new main spec. Linux Chromium uses only fixture-intercepted WS3012/harness15250 pages. Frozen digest `18499d49eafd`; hashes and individual review inventory are in `data/devscratch/ui-r2/followup7/{source,capture-review}.json`. Owned container and harness stopped; ports3012/15250 clear. No owner localhost or real records were opened.

Source/config files changed (43), relative to `src/frontend/`:

- `src/components/items/`: `ValueTooltip.tsx`, `ValueTooltip.module.css`
- `src/pages/assets/`: `FaultHistoryPanel.tsx`, `MachineDetails.module.css`
- `src/pages/assets/equipment/`: `EquipmentViewPanel.tsx`
- `src/pages/assets/faults/`: `FaultOccurrences.tsx`, `faultPresentation.ts`
- `src/pages/assets/health/`: `SignalReading.tsx`, `SignalTable.module.css`, `SignalTable.tsx`, `SignalTrend.tsx`, `Timestamp.tsx`, `common.tsx`, `relativeTime.ts`, `trendPresentation.ts`
- `src/pages/assets/health/mimic/`: `MimicLiveBadge.tsx`, `MimicParts.tsx`, `StationSchematic.module.css`, `StationSchematic.tsx`, `UnitMimic.module.css`, `UnitMimic.tsx`, `UnitSchematic.tsx`, `mimicVisuals.ts`
- `src/pages/assets/locations/`: `LocationWorkspace.tsx`, `MachinePageSummary.tsx`, `locations.module.css`
- `src/pages/assets/maintenance/`: `MachineMaintenancePanel.tsx`, `maintenancePresentation.ts`
- `src/pages/assets/performance/`: `KpiStrip.module.css`, `KpiStrip.tsx`, `PerformancePanel.tsx`, `SensorGroupCard.tsx`, `SensorHeatmap.tsx`, `StationPerformance.tsx`, `TimeRangeControl.module.css`, `TimeRangeControl.tsx`, `TrendChart.tsx`, `liveState.ts`, `pumpColors.ts`, `sparkline.ts`
- `src/tables/assets/`: `MaintenanceRecordTable.tsx`, `componentPresentation.ts`
- Configuration: `tsconfig.s8.json`

Tests touched (18):

- `src/backend/InvenTree/machine_health/tests/test_cusec_total.py`
- `src/frontend/src/components/items/ValueTooltip.test.ts`
- `src/frontend/src/pages/assets/faults/faultPresentation.test.ts`
- `src/frontend/src/pages/assets/health/Timestamp.test.ts`
- `src/frontend/src/pages/assets/health/mimic/mimicVisuals.test.ts`
- `src/frontend/src/pages/assets/health/monitoringComponents.test.ts`
- `src/frontend/src/pages/assets/health/relativeTime.test.ts`
- `src/frontend/src/pages/assets/health/trendPresentation.test.ts`
- `src/frontend/src/pages/assets/maintenance/maintenancePresentation.test.ts`
- `src/frontend/src/pages/assets/performance/KpiStrip.test.ts`
- `src/frontend/src/pages/assets/performance/SensorHeatmap.test.ts`
- `src/frontend/src/pages/assets/performance/TimeRangeControl.test.ts`
- `src/frontend/src/pages/assets/performance/liveState.test.ts`
- `src/frontend/src/pages/assets/performance/monitoringHelpers.test.ts`
- `src/frontend/src/pages/assets/performance/pumpColors.test.ts`
- `src/frontend/src/tables/assets/componentPresentation.test.ts`
- `src/frontend/tests/s8/pui_ui_followup7.spec.ts`
- `src/frontend/tests/s8/pui_ui_followup7_cmms.spec.ts`

Only the two new focused Playwright specs cover this pass; no other lane's S1/S8 or outdated wording/clock specs were edited. No git writes or owner data changes. Database creation/migrations were confined to an isolated test database inside the owned browser container; no stored plant records were accessed. Parent acceptance is unchanged.

The shared ValueTooltip change is a general accessibility improvement: plain text facts expose their explanation to assistive technology, and positioned targets contain hidden content inside table scrollers. Other layout changes stay within AIMMS pages. Final contrast review caught and repaired the new Normal marker numeral (now normal text; ring/check remain green) and Not verified badge text; rendered text/UI checks cover both themes. Future observations retain their real clock offset rather than being clamped to zero age.

## Follow-up 8

Built on `713b22b65`. All three confirmed test-lane bugs were verified against current source and fixed; no finding skipped.

| Finding | Fix and final evidence |
| --- | --- |
| 1. Reference-answer return drops model view | `focusReturnPath` preserves query state from the exact same-machine native Equipment route, including explicit model/schematic and repeated/encoded parameters. The confirmed position overrides launch positions. Other routes and external destinations retain the canonical fallback. Four unchanged strict desktop/phone, light/dark cases prove the return is `/machines/machine/18/equipment/?position=bearing&view=model`. |
| 2. Wider-font phone Machines card below fold | Phone selected-location controls share one row; chooser text wraps within its button, and scope/summary spacing is compact. Optional write actions retain separate rows. No facts or controls hidden, no smaller type; 44px targets and desktop styling preserved. Both themes pass with Arial, 1.65 line height and 0.2px letter spacing: first card bottom **820.08px**, previously **900.08px**, within **844px**. |
| 3. Focused history tooltip clips | UnitMimic uses shared `factTooltipProps` for multiline wrapping, 360px maximum width and hover/focus/touch access. Both strict viewport-matrix cases pass. Focused phone tooltip is **360px** wide, right edge **368px**, document/viewport **390px**; complete explanation and Escape dismissal remain asserted. |

Verification: **8/8 focused Playwright cases**, **107/107 units** across three files, all **9 TypeScript projects**, Lingui extract/compile with all **117 locale files restored bytewise**, changed-file prek and whitespace checks pass. The two phone cases also passed an earlier smoke run. Final source digest: `355cc847847d`.

The exact lane specs at `abd133010` were copied byte-for-byte into an isolated scratch mirror that uses this checkout's current source. The originals, copied assertions and all tracked Playwright specs remain unchanged: `tests/s1/pui_reference_focus.spec.ts` (four exact-return cases), `tests/s8/pui_design.spec.ts` (two wider-font cases), `tests/s8/pui_state_captures.spec.ts` (two shifted-history cases covering 1100/1440/1920/390px). Checksums: `data/devscratch/ui-r2/followup8/lane-specs.json`.

Evidence in `data/devscratch/ui-r2/followup8/`: `{reference,phone,history}-final.log`, corresponding `*-results/` screenshots/geometry, `units.log`, `types.json`, `lingui-result.json`, `prek-final.log` and `source.json`. Reviewed **16 final PNGs**: four reference-answer views, both stressed-font phone views, both focused history tooltips and eight schematic viewport views. Individual paths/hashes are in `capture-review.json`. Fixture-only Linux Chromium on owned WS3012/harness15250; no owner localhost or real records opened. Owned runtime stopped and both ports clear; deployed acceptance/full suites remain with the coordinator.

Source files changed (4):

- `src/frontend/src/components/aichat/workFocus.ts`
- `src/frontend/src/pages/assets/health/mimic/UnitMimic.tsx`
- `src/frontend/src/pages/assets/locations/LocationWorkspace.tsx`
- `src/frontend/src/pages/assets/locations/locations.module.css`

Test file changed (1): `src/frontend/src/components/aichat/referenceFocus.test.ts` — model/schematic and extra-query preservation, confirmed-position precedence and route-boundary coverage. No Playwright spec edited. No database, dependency or Git writes. Parent acceptance unchanged.

## Follow-up 9

Built on `1baa15d4d`. All five findings were verified against source and the archived fixture evidence; none skipped. The three host-only findings are implemented and checked on Linux, with Mac confirmation remaining with the coordinator.

| Finding | Fix / evidence |
| --- | --- |
| 1. Phone Filters resets on Show inactive | The scoped machine list mounts independently of the location-detail count query, keyed by location identity. Successful scope refreshes preserve the disclosure, checkbox DOM node, focus and IDs. Filters has a stable aria-controls target. Four supplemental light/dark, Machines/By-location cases exercise four toggles each; selected-location cases delay detail reads and verify denied detail removes rows/totals. Both unchanged active-scope cases pass. |
| 2. Equipment callouts below Mac fold | Desktop callouts use smaller padding/gaps/markers while retaining at least 44px targets and the original dominant drawing width. All 13 remain visible at 1440×900; the wider-text Linux probe ends at **817.95px**, leaving **82.05px**. Four unchanged 1440/1280 theme cases pass, preserving the narrower-container stacked layout. |
| 3. Default phone first-card fold | Full-word condition chips have less horizontal padding, keeping spare width for font differences; original 8px action gaps remain. Phone cards have tighter padding/fact spacing, with all facts and controls retained. Linux default first-card bottom **804.08px**, wider text **805.67px**, leaving **38–40px** in 844px. Default viewport matrices, unchanged wider-font cases and condition-action/44px checks pass in both themes. |
| 4. Reference model badge blocks motor hotspot | The accessible badge occupies a normal-flow row before the scene, outside its interactive area. Both phone themes prove the row ends before the canvas begins and select the motor through its actual hotspot after selecting the casing. The unchanged S2 one-read case also passes. |
| 5. Completed badge breaks mid-word | Shared AIMMS status/lifecycle badges default to nowrap and intrinsic minimum width. Descriptive evidence explicitly retains wrapping. Fault History reserves space for Owning work, Component and Evidence; Event/finding takes the remaining width. Wider-text probes at 1280×720 prove one text line, the entire badge inside its cell and clipping ancestors, and header text inside each column. The unchanged S7 continuity case passes. |

Verification: **26 unique focused browser cases pass** (14 unchanged lane cases plus 12 supplemental checks). The three affected Fault checks were repeated after final column refinement. **84/84 units**, all **9 TypeScript projects**, final app/S7/S8 type rechecks, Lingui extract/compile with **117 files restored bytewise**, changed-file prek and whitespace checks pass. No tracked test files changed. Exact read-only lane specs are byte-identical to `87ef2537c` (`r6/ui-r2-testfix`, merged lanes): `s8/pui_ui_followup5.spec.ts`, `s8/pui_ui_followup7.spec.ts`, `s8/pui_state_captures.spec.ts`, `s8/pui_design.spec.ts`, `s8/pui_design_review_fixes.spec.ts`, `s2/pui_equipment_inspector.spec.ts`, `s7/pui_monitoring_continuity.spec.ts` (all under `src/frontend/tests/`). Supplemental assertions live only in `data/devscratch/ui-r2/followup9/probes.spec.ts`; no lane assertion changed.

All **16 final per-screen PNGs** in `data/devscratch/ui-r2/followup9/captures/` were individually inspected. Review caught and corrected a too-wide interim Fault table and then its cramped adjacent columns before the final captures. An initial scratch whitespace matcher was corrected, and a stricter badge probe exposed stale modules in the HMR-disabled harness; the harness was restarted and the relevant final runs used fresh source. These preliminary results are retained separately and are not counted as final passes. Linux Chromium used only owned WS3012/harness15250 and intercepted fixtures; no owner localhost, real records or database accessed. Mac-only layout reproduction is **not** claimed.

Evidence in `data/devscratch/ui-r2/followup9/`: `*-final.log`, `probe-final.log`, `lifecycle-balanced{,-lane}.log`, `types{,-balanced}.json`, `units.log`, `lingui-result.json`, `prek-final.log`, `lane-specs.json`, `capture-review.json`, `source.json`. Final digest `d6474d4a30ae`. Owned runtime stopped and ports clear; no dependency or Git writes. Full host suites, deployment and parent acceptance remain with the coordinator.

Source files changed (15), under `src/frontend/src/`:

- `pages/assets/equipment/EquipmentScene.tsx`; `pages/assets/faults/FaultOccurrences.tsx`
- `pages/assets/health/{StatusBadge,common,AnomalyList,AnomalyTimeShift,PreliminaryResults,PumpMimic}.tsx`; `pages/assets/health/mimic/UnitMimic.module.css`
- `pages/assets/locations/{LocationWorkspace,MachinePageSummary}.tsx`; `pages/assets/locations/locations.module.css`
- `pages/assets/performance/{PerformancePanel,SensorGroupCard}.tsx`; `pages/assets/performance/sections/ThermalSections.tsx`

The additional descriptive-badge call sites preserve their existing wrapping when semantic badges become atomic; no new prose, values or dependencies. Tests changed: **none tracked**; seven lane specs copied/read unchanged, one supplemental scratch spec authored for these behaviours only.

## Follow-up 10

Built on `79c9540fa`. The Mac log shows Winding 2 becoming unstable and then invisible during an ordinary click. Source allowed a queued scroll after opening while Mantine's detached-reference handling could hide the still-open dropdown. Replaced that frame callback with immediate reveal and an edit-triggered layout check before paint; the focused-input guard prevents scrolling after blur, and ordinary refreshes do not trigger measurements. Search changes cover typing after Escape without a new focus event. Native comparison channels, mode, machine and saved-view controls share fixed, portalled positioning, live flip/shift, a 240px options viewport and no detached-reference hiding. Native search, option clicks, focus and Escape remain.

Verification: the **exact unchanged light case passed 25 consecutive runs with zero retries**. Five additional dark repeats, four initial light/dark editor and machine cases, and two forced-scroll/wider-font/Escape-reopen probes also pass: **36 passing executions**. Five retained comparison units, all nine TypeScript projects, Lingui extract/compile with **117 bytewise locale restores**, changed-file prek and whitespace checks pass. Six final open-menu PNGs were individually inspected in `data/devscratch/ui-r2/followup10/captures/`. Supplemental geometry checks measure actual label glyphs and popup bounds; initial stretched-block assertions are retained separately as probe-authoring results, not product failures. No original assertion changed.

Source changed (3): `src/frontend/src/pages/assets/performance/{useComparisonDropdown.ts,ComparisonWorkbench.tsx,DisplayPreferences.tsx}`. No tracked tests changed: `src/frontend/tests/s8/pui_ui_followup6.spec.ts` was copied byte-for-byte from `79c9540fa`; `spec.json` records its hash. Supplemental checks are only in `data/devscratch/ui-r2/followup10/probe.spec.ts`. No strings, dependencies, stored data or Git writes.

Logs and hashes in `data/devscratch/ui-r2/followup10/`: `{smoke,repeat-light,repeat-dark,probe,units,prek-final}.log`, `types.json`, `lingui-result.json`, `source.json`, `capture-review.json`. Final digest `ba958119f0a7`. Linux Chromium used only owned WS3012/harness15250 with intercepted fixtures; runtime stopped and both ports clear. **Mac confirmation remains with the coordinator**; no Mac reproduction or deployed acceptance claimed.

## Follow-up 11

Built on `e1d89dd61`. The archived Mac trace confirms a **Filters height-transition scroll trap**: the same disclosure/input IDs and page-2 URL remain, while the temporary `overflow: hidden` wrapper acquires `scrollTop=100`. Completing its 200ms transition removes that internal scroll and hides Criticality again. No route restoration or list remount caused this failure.

`src/frontend/src/pages/assets/locations/LocationWorkspace.tsx` now uses native Collapse `transitionDuration={0}` and `keepMountedMode='display-none'`. Filters opens at full height immediately; closing hides its controls while preserving their DOM nodes, IDs and values. No global scroll/focus workaround or shared-component change.

Verification: **25 consecutive unchanged phone-case passes, zero retries; complete explorer spec 30/30** across desktop/phone; two light/dark geometry probes and one smoke pass (**58 executions**). Across 320–343ms, Criticality stays at **399.78–443.78px** in an 844px viewport, window scroll remains **484px**, and ancestor scroll stays zero. Probes also prove server-side Critical filtering resets offset to zero, returns 22 fixture machines, and survives close/reopen on the same input node. All **34 retained location-state units**, **nine TypeScript projects**, Lingui extract/compile with **117 bytewise locale restores**, changed-file prek and whitespace checks pass.

No tracked tests changed. The complete `tests/s1/pui_machine_explorer.spec.ts` and helpers were copied read-only from `r6/ui-r2-testfix` at `f9bda9fb4`; the spec remains byte-identical (`spec.json`). Supplemental checks are only in `data/devscratch/ui-r2/followup11/probe.spec.ts`. Six final menu/filtered/reopened PNGs in `data/devscratch/ui-r2/followup11/captures/` individually inspected; paths/hashes in `capture-review.json`.

Evidence: `data/devscratch/ui-r2/followup11/{host-trace-summary,source,spec,types,lingui-result}.json`, `{smoke,repeated,explorer,probe,units,prek-final}.log`, matching browser results and per-frame geometry JSON. Frozen source digest `c22493dd50a6`. Linux Chromium used only owned WS3012/harness15250 and synthetic intercepted fixtures; runtime stopped and ports clear. No owner records, dependencies, strings, stored data or Git writes. **Mac host confirmation remains with the coordinator**; parent/deployed acceptance unchanged.


## Follow-up 12

Built on `5a1753b00`. By location now opens with the same server-paged machine table used for a selected location: all active machines across non-archived placements, plus unassigned machines. Search, criticality, alarm filters, paging and summary counts use that scope. Archive/inactive filters remain explicit, and selecting a location still includes sublocations by default.

The existing machine API already expresses the all-location scope; it gains an opt-in archive/ancestor filter. The location list gains an opt-in grouped count projection, independent of its page and the machine table page. The left pane retains the tree and top-level site navigation with full API machine counts; the unassigned action appears only for a positive API count. Root machine rows show compact placement names with the complete path available on hover and keyboard focus. Phone opens with the machine cards visible and the chooser closed. A globally empty scope says “No active machines”; a failed read stays visibly distinct.

Source files changed (4):

- `src/backend/InvenTree/assets/location_api.py`
- `src/frontend/src/pages/assets/locations/LocationWorkspace.tsx`
- `src/frontend/src/pages/assets/locations/locationTree.ts`
- `src/frontend/src/pages/assets/locations/locationTypes.ts`

Test files changed (8):

- `src/backend/InvenTree/assets/tests/test_location_root_population.py` (new)
- `src/frontend/src/pages/assets/locations/locationTree.test.ts`
- `src/frontend/tests/pages/pui_asset_locations.spec.ts`
- `src/frontend/tests/s1/machineExplorerFixtures.ts`
- `src/frontend/tests/s1/pui_machine_explorer.spec.ts`
- `src/frontend/tests/s8/ownerFixtures.ts`
- `src/frontend/tests/s8/pui_ui_owner_followup.spec.ts`
- `src/frontend/tests/s8/pui_ui_round2_captures.spec.ts`

Verification: **20/20 focused browser executions pass**: eight new root desktop/phone cases, three retained S0 location cases, four S8 location-browser theme/device cases, two retained archive-scope cases and three final captures. Root cases cover full counts beyond the loaded page, server paging/filtering, exact return links, focused full-path tooltips, unassigned inclusion/zero hiding, archive/inactive scope changes, selected descendants and empty/failed/retry states. S0's root fixture contains both complete site cohorts (41 machines), with API counts independent of the 25-row page. No unrelated outdated wording/clock specs changed.

Units: **70/70** across locationTree, machineListState and pageFacts. The app and all eight sprint TypeScript projects pass, along with an isolated S0 spec typecheck; app/S1/S8/S0 rechecks cover the final source. An extra legacy `tsconfig.playwright.json` check still reports three errors in untouched `playwright/g2-rv0809.vite.ts`, `playwright/voice-rv15.vite.ts` and `playwright/voice-deferred-focus.tsx` (two unbuilt version-info declarations and a missing demo-metrics-lingui side-effect import); no unrelated harness files changed. Lingui extract and compile pass with all **117 locale files restored bytewise**.

Backend: **31/31 real API tests pass**, including four new root-population cases, existing PhysicalLocation and criticality coverage. The stock offline SQLite profile first produced 20 passes and 11 inherited checkpoint-schema errors. The passing disposable network-disabled runner uses the retained original migrations with only `legacy_boundary` excluded from scratch settings, so SQLite receives the real ORM columns; no schema supplements or permission mocks were used. System checks pass and all retained migrations are applied (the runner also reports pre-existing model/migration drift outside this change). Evidence: `data/devscratch/ui-r2/followup12/backend-orm-final/`; the initial profile result remains in `followup12/backend/`. No product migration, model or stored plant data changed.

Final source digest: `ac05f4e63a5a`. Evidence under `data/devscratch/ui-r2/followup12/` includes `source.json`, `units.log`, `types.json`, `types-final.json`, `lingui-result-final.json`, matching browser logs/results and changed-file prek logs. Initial cold browser/harness setup timeouts and test-authoring runs are preserved separately; they are not counted as final passes. Final tests register response waits before actions and retain their normal DOM assertion budgets. Browser verification uses intercepted synthetic fixtures only, owned WS3012/harness15250; no owner localhost, real records, Azure, dependencies or Git writes. Deployment, Mac host and parent acceptance remain with the coordinator.

Fresh final captures, all personally inspected, in `data/devscratch/ui-r2/final12/`:

- `10-location-light-desktop.png`
- `19-location-dark-desktop.png`
- `20-location-light-phone.png`

Each metadata file identifies final source `5a1753b00+f12-ac05f4e63a5a`; `followup12/capture-review.json` records PNG hashes and review. Desktop shows the initial root table alongside the tree/site counts. Phone starts with the chooser closed and the complete first card above the 844px fold (around 720px), with all facts and compact placement visible. No overlaps or horizontal clipping found. The fixture retains seven synthetic navigation sites and 86 machines; it does not read or assume the owner's current site records.

Browser evidence: `root-final7.log`, `s0-final.log`, `s8-owner-final.log`, `s8-archived-final.log`, `captures-final.log`, their result directories and capture metadata. Owned browser container and harness stopped; ports 3012/15250 clear. Final changed-file prek, whitespace checks and source-hash verification pass. Remaining limitations are the documented legacy extra TypeScript config and stock SQLite checkpoint profile; app/sprint checks and the real-migration API runner pass. No requested product change skipped; no deployment or Mac acceptance claimed.
