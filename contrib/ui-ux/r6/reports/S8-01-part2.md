# S8-01 part 2 — Health and Performance, Hyperagent round 1

Status: **host replay repairs verified, follow-up phone project green and owned runtime stopped; parent design pass remains partial**. Current repair results are recorded in [Host replay fixes](#host-replay-fixes), including the [host font-metric follow-up](#second-host-replay--stale-card-framing). Starting checkpoint `bfa6e8638` on `r6/design-pass-2`. Owner decision O16; O22 scores supplied by coordinator: Opus 44/100, Gemini 74/100. No new score, deployment or product acceptance is claimed.

Health now leads with the qualified server assessment, active anomalies, visible desktop limits and observation age. Performance leads with exceptions and current KPIs, uses neutral data hues, names missing/off-scale evidence and separates incompatible comparison scales. Both use native Mantine, ThemeContext, Layout and MachineDetail/PanelGroup. The dispositions below describe the inherited implementation; the host replay section records verification of the bounded repairs.

Prior-pass evidence: `data/devscratch/r6-s8/S8-01-part2/pass2/`. The previous committed O12 report is preserved there as `O12-checkpoint-report.md`; prior canonical captures are immutable. First-repair renders are isolated under `data/devscratch/r6-s8/S8-01-part2/host-replay/final-evidence/`; their capture batch retains the `s8-01-part2-pass2` suffix. The second host replay uses the separate `host-replay/host-stale-viewport/` evidence root.

Original pass: no backend change, database connection, Azure write, dependency installation, download, git write or PR. The host replay section below records later ledger updates. Historical runtime recorded by that pass: Linux Chromium container `r6-design2-browser-15234`, harness 15224, browser websocket 15234. Its lifecycle is outside this repair run; current task-owned runtime and cleanup are recorded below.

The native host tab clipping, panel-title and Physical location contrast are outside the authorized change (§5). S0/S3 synthetic fixture strips are harness chrome. MachineDetail's only change is H5 action visibility, using existing permission gates.

## Hyperagent round 1 dispositions

All items use the reconciled specification in `pass2/fix-spec.md`. The code is ready for the coordinator's O22 rescore; the supplied 44/74 scores are historical, not passing scores.

| Item | Disposition and implementation | Verification |
|---|---|---|
| C1 | Required semantic tone selects the correct icon; neutral has none. Only filled/outline variants; autoContrast, caller sizing and shaded/CSS colors preserved. Critical red.9 filled; Warning yellow.6 filled; partial Normal neutral. Removed Badge aliases; alarm visual calculated once. | Tone/prop units; both-scheme badge/text sweep; Critical evidence. |
| C2 | Neutral indigo/blue/cyan/violet/grape series; teal only valve. Winding/core/cooling/bearing/status/frequency assigned explicitly. Sequential heatmap with No reading; statistics neutral. | Computed curve/fill/heatmap palette scan on all frames. |
| C3 | Shared clock rejects invalid/zero instants, makes unavailable zone explicit, annotates differing instant offsets. Body font/tabular numerals; useNow for render ages. Linked evidence and modal use shared formatting. | Multi-zone/DST/invalid units, both process TZ runs; one-clock assertion. |
| C4 | Lossless unitLabel map with raw-code abbr title; absent unit visibly “unit not recorded”; numbers-only ticks and one axis unit. Searches match raw and displayed units without changing compatibility identity. | Mapping/search units; raw-code rendered-text sweep and vibration assertions. |
| C5 | No current data replaces Offline. Shared observation time/relative age/freshness budget; unavailable time explicit. Stale values have Last known, weight 400, no TrendMark or state badge. Missing observations distinct. Future instants never become zero age. | Freshness branches/future units; stale/no-data frames and DOM assertions. |
| C6 | Visible KPI observation text; keyboard TrendMark tooltip. One stable status output per panel. Local error/attention styles, selected row/option contrast, track/thumb boundaries. Explicit native table roles retained. Removed dangling controls relation and named pagination controls. | Scoped axe, text/non-text sweeps, selection/switch assertions, eight focus captures per scheme. |
| C7 | Provenance compact summary plus Show read details Spoiler. Source, retention, empty/failed/partial reads, applicability and gap caveats remain visible. Live controls/status share a row; continuity moved into sources, retained box correctly titled. | Trend within 200px; maximum 70ch metadata/alert text; visible caveat assertions. |
| C8 | Stable identity/bound keys, plural macros, Health useId, useNow, axis-specific bands/reference lines, comparisonGroups once, MetaText/MonitoringLink wrappers. No scope-level Mantine overrides. | All nine type projects, units, host prek, source checks. |
| H1 | Qualified headline and cause from server-evaluated signals/anomalies; partial Normal neutral. Nonzero alarm counts only. Critical missing names/reasons linked beside assessment; complete coverage plain text. | headlineFacts branch units; partial/complete/breach/unknown frames; phone fold assertion. |
| H2 | Header right actions grouped; summary then Active anomalies, Signals, Trend, Past alarms, retained evidence, sources. Container ≥1400 puts condition and anomalies side by side. | Heading order, 0/1 alarm Refresh x equality, alarms within viewport. |
| H3 | Seven-column ≤960px dense desktop table; severity/name ordering, visible limits/quality/age. Phone-only cards/toggle; source/anomalies in labelled row Collapse. Selected row aria-current, primary background/edge; sticky first column on narrower desktops. | Six rows ≤360px; no table overflow ≥1440; first breach row; phone cards and details. |
| H4 | Shared limitLines and ThresholdKey used by Health trend; per-axis bands and explicit Include limits in scale. | Off-scale/drawn unit split; actual painted boundary geometry and breach trend captures. |
| H5 | Viewer actions hidden using existing add/change/execute gates, explicit view-only sentence; editors retain actions. MachineDetail header uses matching gates. Server permissions unchanged. | Positive viewer note plus zero actions, positive editor actions; full permission matrix. |
| H6 | Signed shift badge plus source-recorded time beside each displayed anomaly time, both lists. Chart caption direction-aware including unknown amount. | ±3h and unknown shift units; positive/negative framed alarm captures. |
| H7 | Shared SourcePollStatus reports actual last poll success/failure/disabled and time/code/age; missing attempt/result explicit. No green connection verdict. | Source branch units; Health text sweep excludes Connected. |
| H8 | Condition qualifier stays with headline at 390; native wide grid uses available panel width without host changes. | 390 fold and 1920 layout measurements/captures. |
| H9 | Case span control fit-content; Expand/Inspect/Parameter/Range grouped, sentence-case sample/extent facts separate. | Button/badge assertions and Health desktop/phone captures. |
| H10 | Shared monitoringTerms; signal/channel distinction explained. Poor-quality count filters named channels. Successful empty windows say No samples in window; failed reads remain explicit; missing batch rows say Not returned (host replay contract restoration below). | Missing-window/quality tests; forbidden wording sweep. |
| P1 | Breach chip uses worst actual server state and names configured crossed limit; unclassified state with limits remains unassessed. Statistics neutral, confirmed within-limits outline green. | Warning/Critical/unknown-evaluation fixtures and state assertions. |
| P2 | Off-scale/drawn key, conservative distance and approval/applicability caveat. Include limits folds boundaries into explicit domain before niceTicks. KPI footers explain limits, including when a different named family channel supplies the state. | Threshold split units; off/on breach captures; actual painted line; mixed-family regression. |
| P3 | Timestamp-based sparklines split nulls and silences, including Other. Missing expected series named in legend and provenance. | Gap subpath units; native history and missing-series tests/captures. |
| P4 | KPI age uses its observation, never historical sample age. Last-known/stale/no-arrow styling; contradictory current-age server flags qualified. Coherent 60min-old fixture. | Freshness units and stale tile DOM/capture. |
| P5 | Header/range/live → Exceptions → KPIs → contextual comparison/navigation → sections. Single count-labelled section Select; phone navigator collapsed. Open comparisons move above navigator; mode/channels side by side, shared before personal views. | First KPI phone fold, refusal/shared heading desktop fold, URL and search/scale tests. |
| P6 | Refusal names both channel groups, units and roles and which identity differs; explicit separate synchronized-chart outcome. | Incompatible fixtures and framed refusal text. |
| P7 | Temperature dot plot with fitted niceTicks domain. Power/PF separate stacked synchronized charts. Highest winding line heavier/end-labelled; neutral palette. | No temperature bars; separate-axis/domain tests, chart captures and palette scan. |
| P8 | Live primary outline/broadcast; stale clock; paused neutral outline. Compact actual status facts. | Both-theme Live outline/text contrast, pause/live tests. |
| P9 | Native container KPI grid, bottom-aligned footers. Eight across at the actual 1440 panel; four at 1100. | Exact counts and footer spread ≤2px in capture JSON. |
| P10 | Other table paged at 50, visible range/total, page resets when channel identity changes. Re-selecting Full overview expands collapsed groups. | 500-channel row bound/pagination/search and repeated section selection regression. Native cap-height trimming disabled only on wrapped result labels; clipping assertions retained. |
| P11 | Phone ≥40px controls and segmented labels, medium Live switch, native date controls with names. | Phone control bounding boxes, custom range and touch JSON. |
| F1 | Coherent 18 Oct 2026 NOW, production-dictionary cusec for the same discharge tag in both harnesses (no conversion). Native ThemeContext/Layout/MachineDetail for both. Coherent stale timestamps; viewer/editor, ±shift, critical-review and breach fixtures. | Strict unmocked-request/runtime guards; native shell identity; full fixture matrices. |
| T1 | Added pure clock/unit/freshness/headline/threshold/sparkline/shift/tone tests; S8 measured acceptance, state, contrast, palette, accessibility and focus coverage; existing S3/legacy assertions updated for the specified honest wording/structure. No reduced contrast or permission assertions. | Final command/results table and artifacts below. |

Implementation details resolved against actual types: history batches have no `window_start` field, so stable response-window/binding/key identities replace array-index keys. The shared limit helper lives in `performance/limitLines.ts` to avoid a circular chart import. Eight KPIs require a 1140px container breakpoint because the real 1440px native panel is about 1159px wide; the requested 1180px breakpoint would miss its own acceptance criterion. Off-scale distance says “window values are at least … below” because the chart contains a historical window, not only current observations. No absent physical threshold approval, historical applicability, compatibility condition or retention fact was invented.

## Review findings and dispositions

The six earlier findings were confirmed by an agent under O12 delegation. This is an implementation response to that review, not independent human review. The committed O12 checkpoint report and original evidence are retained in `pass2/O12-checkpoint-report.md` and `review/`.

| Earlier finding | Current disposition | Regression |
|---|---|---|
| Shifted active/past anomaly times incorrectly appeared to be the browser clock | Both lists show signed shift and source-recorded time beside each shifted instant; the header qualifies unshifted times. H6 supersedes the earlier plain “shown later” note. | Positive/negative/unknown shift units and framed S8 alarm states. |
| Anchored action-name regex matched no row-specific controls | Word-boundary prefix retained; editor tests require all four enabled actions. H5 now intentionally hides viewer actions, with positive view-only text and an exact zero count. No disabled loop over an empty locator. | Full S8 permissions and viewer/editor states. |
| Missing vibration tile lost its reason and section misdescribed unreviewed mappings | Tile and section explicitly require a confirmed role and unit; units absent from a binding remain unavailable. | Legacy Performance suite and S3 unit/role cases. |
| Green filled Live text fell below contrast target | P8 supersedes the temporary fill fix: neutral primary outline with broadcast icon. Filled server states use C1 autoContrast. | Fresh-live light/dark and all-text sweep ≥4.5:1. |
| Scope CSS recolored disabled gray controls and Alert titles | No scope variables or generic anchor/subtle-button color overrides. Explicit MonitoringLink/link classes exclude disabled controls; attention/error titles have local styles. | Both-theme text/edge sweeps and host prek. |
| Expanded chart and analysis/custom range used inconsistent clocks | Modal declares its clock; analysis/custom extent and linked evidence use shared displayInstant with invalid/offset handling. | S3 modal/analysis cases, multi-zone clock units, one panel-clock check. |

## Prior-pass verification checkpoint

The interrupted prior pass recorded **733/733 units in 73 files**, independently under `TZ=Asia/Kolkata` and `TZ=America/St_Johns`; all nine TypeScript projects; legacy Performance **8/8**; and Lingui extract/compile with **40/40 `.po` files restored bytewise**. Its full S3/S8 and final scoring capture inspection were unfinished at handoff. These are historical claims from that pass, not current repair acceptance; the independently rerun checks and their actual scope follow.

## Host replay fixes

21 October 2026; first host-repair checkpoint. The subsequent host font-metric
follow-up is recorded below. Screen: native pump Health and Performance.
Primary users: operators and maintenance engineers. Objective: inspect active
and historical alarms, explicit missing history, recurrence accounts and large
channel tables. Existing signal/anomaly/history APIs and native React/Mantine
architecture remain authoritative. Applied `masterdesignprompt.md` and
`designprompt1.md`; no new external design integration is needed for these
bounded repairs. No Figma.

Coordinator logs supplied 9 S8, 2 S0 and 4 S7 failures. Evidence for this repair
is separate from the prior pass: `data/devscratch/r6-s8/S8-01-part2/host-replay/`.
Renders use Linux Chromium in the task-owned browserless container
`r6-dp2fix-browser-3002`, websocket 3002, isolated fixture harness 15224.
Full browser results are recorded below; this is not a macOS GPU replay or deployment,
independent human review, plant-data verification or product acceptance.

### Causes and repairs

| Host failure | Cause | Repair |
|---|---|---|
| S8: 8 active/acknowledged history cases | Fixture deliberately supplies the same open/acknowledged record to active anomalies and the historical read. The status is needed in each separate region; it is not repeated within one card. The assertion searched the whole panel. | Assert the exact status inside `alarm-history` using the same locator captured for history evidence. Keep the active-condition/source assertions and every status branch. |
| S0: 2 partial batch cases | The missing-row label changed to “History unavailable”, losing the explicit not-returned contract. | Restore translated “Not returned” for an absent batch row, retain the detailed reason/unknown-history tooltip and unchanged no-row-fetch assertions. Restore the corresponding two S3 expectations. Keep unavailable-source and successful empty-window states distinct. Remove “Not returned” from S8's forbidden-word sweep because that prohibition conflicts with the restored S0 contract; the exact S0 assertions remain mandatory. |
| S7: 4 create_linked recurrence cases | The disclosure was renamed “Original completed account”; the test still searched for “Original accepted snapshot”. | Scope to the closeout account's exact current summary label. Still prove one explicit linked creation, original recurrence IDs, unchanged closure and original result after disclosure. |
| S8: phone 500-channel case | Count and pagination lived inside the wide table's horizontal scroller. ArrowRight moved them, leaving the count fractionally clipped (host intersection ratio 0.996). | Put only the table inside native `Table.ScrollContainer`; count and wrapping pagination remain outside. Preserve keyboard scrolling, 50-row count and full viewport ratio 1. Frame captures below measured sticky chrome instead of a hardcoded 210px. |

Additional replay finding: the broad Linux replays exposed intermittent Health
and Performance focus-capture failures. The old helper sent Shift+Tab and Tab
as immediate keydown/keyup pairs; the saved failures stopped on the preceding
control. Diagnostic key/focus events showed unprevented events and the intended
predecessor-to-target path when navigation completed. The first-checkpoint helper observes
the focus move before releasing each key, then verifies focus remains after
release. It also verifies initial focus and backward departure. The source-scroll
check uses the same helper. Focus, permission, contrast, no-row-fetch, recurrence and ratio-1 assertions
remain; the conflicting text ban is explicitly corrected above. No fixed
delays, skips, retries or timeout increases were added. Failed timing approaches remain in scratch logs;
only the completed first-checkpoint all-suite replay below counts as browser verification for that helper version.

The first browser attempts stalled during cold navigation and server startup,
with a negative-timeout warning in the task-owned server. A direct in-container
Chromium data-page probe passed; restarting only this container restored normal
progress. Failed logs are retained, separate from green evidence. Focused S8
repair replay: **12/12**. Earlier broad replays were stopped to verify the focus finding before restarting all suites; they are not completed full replays. The final full S8/S0/S7 results follow.

Runner note: after interruption, the task-owned Vite child on 15224 (PID 17707)
remained running. The sandbox denied its stop signal with “Operation not
permitted”. The scratch runner explicitly reused that identified task-owned
server; no shared/unknown server was reused. Native lifecycle cleanup attempts
did not reach their guarded shutdown hook: the last probe proved the server
could read the temporary config bytes, but no config-evaluation or
`configureServer` marker appeared. Each attempt restored `vite.s0.config.ts`
byte for byte (SHA-256 `97d51edee9ff885fd6d71b86217f5f5cfdf6694512932c1b29809e601e05e1ea`).
The browser container was stopped and its absence confirmed. Host session or
stop confirmation was requested for this exact Vite PID at that checkpoint.
On the next resumption the PID and its listener were absent, as recorded in
the runtime history below; this prior cleanup blocker is resolved.

Resumed verification applied the installed EQUA browser-testing and
Mantine/InvenTree engineering skills. A read-only second-agent review found no
permission, architecture, assertion-strength or native behavior findings in the
bounded repairs; this is not independent human acceptance.

After the shared Docker VM ran down to about 227 MiB available memory,
Chromium reported `Target crashed` during phone viewer capture. The coordinator
freed about 4 GiB and the task-owned browser was restarted. The fresh phone
viewer case passed. No assertion or timeout was changed for this runtime issue.

That fresh phone replay also exposed a framing defect near the document bottom:
`scrollIntoView` was clamped, then the helper blindly subtracted the full sticky
header height and pushed an already-low history-failure alert below the viewport.
The helper now adjusts from the target's actual viewport top, retaining the exact
ratio-1 assertion. The final full S8 replay below passed with this correction;
prior partial/failed runs are retained and do not constitute a pass.

### Files touched by the host repair

Production:

- `src/frontend/src/pages/assets/health/SignalTrend.tsx`
- `src/frontend/src/pages/assets/performance/sections/OtherSection.tsx`

Contracts and browser evidence:

- `src/frontend/tests/s3/pui_health_trends.spec.ts`
- `src/frontend/tests/s7/pui_monitoring_continuity.spec.ts`
- `src/frontend/tests/s8/pui_monitoring_states.spec.ts`
- `src/frontend/tests/s8/pui_monitoring_acceptance.spec.ts`
- `src/frontend/tests/s8/hyperagentEvidence.ts`
- `src/frontend/tests/s8/monitoringEvidence.ts`

Tracking:

- `contrib/ui-ux/r6/HANDOFF.md`
- `contrib/ui-ux/r6/s8.md`
- `contrib/ui-ux/r6/contracts.csv`
- This report.

The inherited C1–C8/H1–H10/P1–P11 implementation remains uncommitted. The repair
added no dependencies, backend change, persisted migration, Azure access, Git
write, issue or PR. Browser configuration, runners, failed attempts and lifecycle
scripts are task-owned ignored artifacts under `host-replay/`.

### Reproducible verification commands

From `src/frontend`, the task's scratch config extends the native sprint config,
uses only harness 15224 and browser websocket 3002, and reuses the identified
owned Vite server. It sets no retry or timeout overrides. The final runner is
`data/devscratch/r6-s8/S8-01-part2/host-replay/complete-replays.py` (run from the
worktree root). It executes each sprint with:

```sh
AIMMS_HARNESS_PORT=15224 AIMMS_BASE_SPRINT=s8 AIMMS_REPLAY_RUN=s8-complete-final \
  AIMMS_MONITORING_EVIDENCE_ROOT=../../data/devscratch/r6-s8/S8-01-part2/host-replay/final-evidence \
  node node_modules/playwright/cli.js test \
  --config ../../data/devscratch/r6-s8/S8-01-part2/host-replay/browser.config.mts
```

Use `s0`/`s0-full` and `s7`/`s7-full` for the other complete suites. The final
runner selects its separate `host-replay/final-evidence/` capture root. The two
restored S3 contracts run in both native projects with `AIMMS_BASE_SPRINT=s3`,
`tests/s3/pui_health_trends.spec.ts`, and
`--grep 'recorded zero, empty read|source outages, timeouts'`.

Additional commands:

```sh
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s0.json
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s7.json
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s3.json
npm run test:unit
npm run extract
npm run compile
```

Extraction/compilation used a bytewise backup and `finally` restore of all 117
locale files; no Git restore command or static/backend build was used. From the
worktree root: `PATH=/opt/homebrew/bin:$PATH prek run --files <changed-files>`
(the exact 91-path list is `host-replay/changed-files-final.txt`) and
`python3 contrib/ui-ux/r6/validate_tracking.py`. Results and cleanup status follow.

### First host-repair browser checkpoint

Every run below completed with exit code 0 at the first host-repair checkpoint.
Each used native project definitions and the supplied Linux Chromium setup.
The later capture-helper change is verified separately below. No browser tests
were skipped and no test timeouts or retries were increased.

| Replay | Result | Definitive log under `host-replay/` |
|---|---|---|
| Full S8 | **222/222 passed, 58.5 minutes**; 111 desktop and 111 phone cases, including light/dark state, focus, contrast and permission matrices | `s8-complete-final.log` |
| Full S0 | **95/95 passed, 14.9 minutes**; both exact two-label missing-batch checks and unchanged zero-row-read assertions | `s0-full.log` |
| Full S7 | **172/172 passed, 33.8 minutes**; all four linked-recurrence cases retain original IDs, closure and result | `s7-full.log` |
| Targeted S3 trends | **4/4 passed, 1.2 minutes**; the two restored contracts in desktop and phone projects | `s3-trends.log` |

`complete-replay-results.json` records all four successful process exits. Full
S3 was not replayed by this repair task; the later coordinator host result is
recorded below. The broader O22 rescore and parent S8/R6 acceptance remain
separate coordinator work. The latest capture-helper change has no post-fix
macOS GPU replay in this report.

Representative final renders were inspected, alongside the automated matrix:

- `final-evidence/browser/captures/health/s8-01-part2-pass2/health-past-open/390-light.png` and `health-past-acknowledged/1440-dark.png`: returned history statuses remain in their own region, separate from active anomalies.
- `final-evidence/browser/captures/performance/s8-01-part2-pass2/performance-500-channels/390-dark.png` and `1440-light.png`: count and wrapping pagination stay visible while only the table scrolls horizontally.
- `final-evidence/browser/captures/performance/s8-01-part2-pass2/performance-history-read-failed/390-light.png`: the complete failure alert and Retry history control fit below measured sticky chrome.
- `s0-full/s0-pui_supplied_trends-pen-17467-r-start-per-row-trend-reads-s0-desktop/test-finished-1.png`: both missing rows explicitly show “Not returned”.
- `s3-trends/s3-pui_health_trends-recor-9a37f-a-missing-row-stay-distinct-s3-phone/table-states.png`: zero, successful empty history, unavailable history and a missing returned row remain distinct on phone.
- `s7-full/s7-pui_monitoring_continui-14022-e-linked-is-explicit-light--s7-desktop/test-finished-1.png` and the corresponding `s7-phone` capture: the original completed account is expanded and its original result remains readable after linked creation.

### First host-repair local checks

| Command | Result | Log under `host-replay/` |
|---|---|---|
| `node node_modules/typescript/bin/tsc --noEmit -p <config>` for `tsconfig.json`, `tsconfig.s0.json`, `tsconfig.s7.json`, `tsconfig.s8.json`, plus `tsconfig.s3.json` | **All five passed** | `types-complete.log` |
| `npm run test:unit` | **733 tests passed in 73 files** | `vitest.log` |
| `npm run extract` and `npm run compile` | **Both passed; all 117 locale files restored and verified bytewise** | `lingui.log` |
| `PATH=/opt/homebrew/bin:$PATH prek run --files <all changed files>` from the worktree root | **Passed on all 91 changed files**; hooks without applicable files retain their normal no-file status | `prek-complete.log`, exact paths in `changed-files-final.txt` |
| `python3 contrib/ui-ux/r6/validate_tracking.py` | **PASS: 58 work items, 95 screen dispositions, 26 contracts; dependencies and provider paths valid** | `tracking-complete.log` |
| `git diff --check` and `git diff -- src/frontend/vite.s0.config.ts` | **Clean; no persistent Vite config change** | `diff-complete.log` |

### First host-repair runtime cleanup

`docker stop r6-dp2fix-browser-3002` succeeded; an exact-name Docker query
confirmed the task-owned container was removed. No other container was touched.
The three guarded native Vite cleanup attempts are retained under
`vite-cleanup-*/`; each failed to install its shutdown hook and restored the
original config bytes. These failed cleanup attempts are not recorded as
successful server shutdowns. The last result is
`vite-cleanup-e1ab87ff63231c6b3c5690d2/result.json`.

At that checkpoint, task-owned Vite PID **17707** on **127.0.0.1:15224** still
needed host termination because sandbox signals were denied. On resuming the
second host replay, the port was free and the known PID had no `lsof` entries;
that prior cleanup blocker is resolved by observation. The new run uses a
managed harness with its native shutdown endpoint installed at startup.

### Second host replay — stale-card framing

Coordinator macOS GPU Chromium results: **S0 95/95**, **S7 172/172**, **full S3
280/280**, and **S8 220 passed / 2 failed**. These are coordinator-reported
host results, not additional Linux runs by this task. Both remaining failures
were `Performance stale evidence` in the phone project (light/dark), at the
unchanged `toBeInViewport({ ratio: 1 })` check for `[data-kpi="winding"]`.
The supplied `dp2h-s8.log` is copied bytewise to
`host-stale-viewport/coordinator-dp2h-s8.log`; its source and digest are recorded
in `coordinator-results.json` beside it.

The host trace measured the wrapped sticky header bottom at **392.671875px**
in a **390×844** viewport. It left **451.328125px** below the header. The
retained Linux capture's unchanged winding card was **443.4375px** tall. A
fixed **16px** capture gutter consumed more than the available spare height:
only **435.328125px** remained for the card. The host screenshot shows vertical
clipping, with the card's right edge contained. This is a capture-positioning
defect; the card fits in the free viewport without that fixed gap. The trace
also records its initial ratio changing from **0.982** to **0.928**, while the
old helper waited for query/layout settling only after the viewport assertion.

An isolated two-theme geometry probe changes only the native header minimum
height to **393px**. With normal Linux card/viewport metrics it records a
**443.4375px** card in **451px** free space, proving it fits while the fixed
16px gap cannot. The old helper failed both strict checks (**0.98132485**);
those deliberate reproduction failures are retained in `probe-before.log`
and `before/`, not reported as passing tests.

`tests/s8/hyperagentEvidence.ts` now waits for native query idle, font readiness
and two animation frames both before the initial scroll and after it exposes
rows. It measures the settled target and header, then reserves a gutter of
`max(0, min(16, floor((availableHeight - targetHeight) / 2)))`. Instant scrolling
uses the target's actual top, preserving the document-bottom clamp correction.
The ratio remains **1**; additional assertions require the full feature to fit
the available height, clear the header and end within the viewport. Captured
JSON records the framing dimensions and chosen gutter. No production layout,
card content, thresholds, localization or test budget changed.

The corrected probe passed **2/2** and the native stale phone cases passed
**2/2**. Its inspected light capture shows a **3px** gutter, target top
**395.84375px** and bottom **839.28125px**, completely below the 393px header
and above the 844px viewport bottom. Default and S8 TypeScript checks passed.
The entire phone state spec passed **60/60** in **6.0 minutes** with the same
capture-helper digest. An earlier attempt stopped at **48 passed / 12 failed**
because the separate evidence directory lacked the cached `axe.min.js` asset;
all 12 failures were the missing-file error. That attempt is retained under
`states-phone-missing-axe*`. After copying the existing cached asset bytewise
(no download or assertion change), the full state spec passed.

The first full phone project with this capture helper finished **110 passed /
1 failed** (`full-phone-focus-before*`). The failure was the
Health light keyboard round trip for “Details for Discharge flow”: initial
focus and backward departure passed; the forward keydown did not return focus,
and the helper kept Tab held throughout the assertion wait. The trace does
not establish the browser's internal cause. A passive four-case diagnostic
passed, but successful Health navigation replaced its original event buffer;
it did not reproduce or explain the failure. A subsequent repeated diagnostic
had two overlapping launches and is excluded from all acceptance totals
(`focus-diagnostic-2-overlap.json`). The zero-test grep setup error is retained
separately. None of these attempts substitutes for the final native replay.

Completing the keys alone was insufficient: the isolated native acceptance
attempt passed both Health cases but failed both Performance cases
(`focus-phone-input-before*`). Isolated event diagnostics reproduced both
Performance failures. The one-case insertion probe recorded **0 fetching
queries throughout**, no disclosure control at backward Tab (**3587ms**),
Mantine inserting “Show read details” at **3749ms**, then forward Tab at
**3905ms** correctly focusing that newly inserted control. Trusted key events
were unprevented; the target remained connected and the document stayed
focused. Mantine's installed Spoiler starts with a zero measured height and
adds its control after ResizeObserver schedules an animation-frame update.
The test started walking the tab order before that measurement completed.
These failed diagnostic checks remain under `focus-diagnostic-current/` and
`focus-diagnostic-insertion/`; they are reproduction evidence, not passes.
The original intermittent Health failure was not independently reproduced.

`tests/s8/pui_monitoring_acceptance.spec.ts` now waits for fixture query idle,
font readiness and two animation frames before each round trip. Performance
also positively requires its measured “Show read details” disclosure to be
visible before starting the first KPI focus check. Complete native Shift+Tab
and Tab key presses retain the negated focus assertion, require a connected
active control distinct from the target, body and document root while the
document has focus, and await the next animation frame between directions.
The strict return-to-target assertion remains. No focus is forced after the
initial starting point, and no timeout, retry or skip was added. This fixes
the observed changing-tab-order precondition and strengthens actual backward
departure; it does not repair production controls or assert a generic Chromium
input defect. The final native acceptance spec passed **4/4** in **30.4s**.
The complete phone project passed **111/111 in 8.1 minutes** with this exact source version.

Rendered follow-up evidence was inspected: the native light stale tile in
`full-phone-evidence/browser/captures/performance/s8-01-part2-pass2/performance-stale/390-light.png`
and dark 393px-header probe in
`after-evidence/browser/captures/performance/s8-01-part2-pass2/host-stale-viewport/390-dark.png`
contain the full card below the sticky header. Final focused captures
`focus-phone-evidence/browser/captures/focus/s8-01-part2-pass2/details-light.png`
and `trend-mark-dark.png` show visible keyboard outlines on the actual controls.

Files changed in this follow-up: the two test helpers above, this report,
`HANDOFF.md` and `s8.md`. Production and localized strings did not change, so
first-checkpoint unit/localization results remain historical and no fresh
Lingui run is claimed for this test-only follow-up. Final project results, file
checks and completed owned runtime shutdown follow.

#### Follow-up verification and shutdown

All results below completed with exit 0. The runner `host-stale-viewport/run-final-phone.py`
uses the native S8 config through `host-replay/browser.config.mts`, harness
15224 and browser websocket 3002, with the supplied Linux Chromium image.
From `src/frontend`, set `AIMMS_HARNESS_PORT=15224 AIMMS_BASE_SPRINT=s8`,
`AIMMS_REPLAY_RUN=host-stale-viewport/<run>` and
`AIMMS_MONITORING_EVIDENCE_ROOT=<absolute host-stale-viewport>/<run>-evidence`.
Run `node node_modules/playwright/cli.js test --config
../../data/devscratch/r6-s8/S8-01-part2/host-replay/browser.config.mts`
with `--project=s8-phone` and the following selectors. Exact commands and
current test-file digests are in `replay-results.json` and `focus-phone-result.json`.

| Check / selector | Actual result | Log in `host-stale-viewport/` |
|---|---|---|
| Isolated 393px-header geometry probe (separate `browser.config.mts`, both themes) | **2 passed (23.4s)** | `probe-after.log` |
| `tests/s8/pui_monitoring_states.spec.ts --grep 'Performance stale evidence'` | **2 passed (24.1s)** | `focused-phone.log` |
| Entire `tests/s8/pui_monitoring_states.spec.ts` | **60 passed (6.0m)** | `states-phone.log` |
| Entire `tests/s8/pui_monitoring_acceptance.spec.ts` | **4 passed (30.4s)** | `focus-phone.log` |
| Full native S8 phone project | **111 passed (8.1m)**; no skipped cases | `full-phone.log` |
| `node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json` and `-p tsconfig.s8.json` | **Both passed on the final test sources** | `types-final.log` |
| Worktree-root `PATH=/opt/homebrew/bin:$PATH prek run --files <91 changed paths>` | **Passed**; exact list is `host-replay/changed-files-final.txt` | `followup-prek-all.log` |
| `python3 contrib/ui-ux/r6/validate_tracking.py` | **Passed: 58 work items, 95 screen dispositions, 26 contracts** | `followup-tracking.log` |
| `git diff --check`; native Vite config comparison | **Clean; native config unchanged** | `followup-diff.log`, `followup-config.log` |

The completed full phone replay includes both themes' stale cards, historical
alarm states, 500-channel pagination, history failures, focus, contrast and
permission checks. Ratio **1** and the stronger header/bottom bounds remain.
The final capture helper is unchanged from the passing whole state-spec run;
the full project additionally verifies the final focus helper. These are
Linux fixture results plus the controlled host-height probe. No post-fix
macOS GPU run, deployment, new O22 score or parent acceptance is claimed.

The managed harness closed through its startup-authenticated native lifecycle
endpoint. Its PID **65795** and listener **15224** are absent, the native config
remains byte-identical, and the exact owned browser container was stopped and
removed; listener **3002** is absent. `shutdown.json` records the checked
identities, native-close marker, actual stop and final port observations. No
other runtime or protected port was touched. The report/ledger updates passed
final scoped hook/tracking/diff checks; `manifest.json` binds those results,
the definitive logs, source digests and shutdown evidence. First-checkpoint
manifests remain historical and retain their original cleanup status.
