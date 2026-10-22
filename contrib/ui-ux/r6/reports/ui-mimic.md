# Station and pump-unit mimic repair

Status: implemented; coordinator Mac browser replay pending.
Worktree: `r6/ui-mimic`, starting revision `8a8335959`.

The final owner instruction ended sandbox browser work and assigned full-suite
execution to the coordinator on the Mac. No new long browser run was started
after that instruction. The results below distinguish completed fixture checks,
earlier source checkpoints and outstanding final-source browser verification.

## Issues and fixes

| Issue | Fix |
|---|---|
| 1. Station overlaps, clipped status and small pumps | Forebay, station status and running count occupy an independent responsive grid. The larger pump row uses consistent strokes and readable name/state labels. Narrow screens scroll the contained bay row; keyboard focus reveals the complete selected bay, including P14. Status values size naturally. |
| 2. Dominant review banner | Replaced the yellow banner with a compact information control beside the schematic title. Its hover/focus tooltip retains the plant-review fact. |
| 3. Missing-value and threshold noise | Equal-height KPI cards align labels and values. Missing facts show only an em dash, with the cause in an accessible hover/focus tooltip. Removed derived-total chips, unavailable counters, repeated threshold notes, closed-section reading summaries and the alarm-table acknowledgement caption. Source pointers, raw status codes, age and unchanged intervals remain inspectable. Failed reads, bad quality and active alarms remain explicit. |
| 4. Small pump drawing and crowded callouts | Enlarged and centred the native SVG between two independent callout columns on desktop; phone/tablet place the drawing above the columns. Labels allow two lines and expose their complete text in tooltips. Native numbered-marker coordinates and activation are preserved. Key readings occupy their own grid above the pipes. Missing pressure, direction, communication and timestamps use tooltip-only reasons. The legend wraps cleanly; redundant helper text, duplicate flow and zero-alarm chips are removed. |

The owner override also moved phone provenance into the existing schematic
information control while retaining the visible clock identity. Its semantic
status exposes complete source/read/clock metadata to assistive technology.
The API, registered part mappings, permissions, actual alarm semantics and O27
recorded-telemetry presentation remain authoritative. No backend files changed.

## Screenshots and visual inspection

All paths below are relative to `data/devscratch/ui-mimic/`. Captures use native
React/Mantine components with an explicitly synthetic fourteen-bay/P7 response
and the checked-in native SVG geometry. Linux Chromium ran in task-owned Docker
`r6-ui-mimic-browser-3010`, WS 3010, fixture harness 15245. They are not live
plant, real-login, deployment, macOS-browser or product acceptance evidence.

| Screen / viewport / theme | Before | After component capture |
|---|---|---|
| Station 1440×900 light | `before/station-desktop-light.png` | `after/station-desktop-light.png` |
| Station 1440×900 dark | `before/station-desktop-dark.png` | `after/station-desktop-dark.png` |
| Station 390×844 light | `before/station-phone-light.png` | `after/station-phone-light.png` |
| Station 390×844 dark | `before/station-phone-dark.png` | `after/station-phone-dark.png` |
| P7 1440×900 light | `before/pump-desktop-light.png` | `after/pump-desktop-light.png` |
| P7 1440×900 dark | `before/pump-desktop-dark.png` | `after/pump-desktop-dark.png` |
| P7 390×844 light | `before/pump-phone-light.png` | `after/pump-phone-light.png` |
| P7 390×844 dark | `before/pump-phone-dark.png` | `after/pump-phone-dark.png` |

All eight before and twelve after component images (including 1024×768 tablet)
were opened and inspected. Metrics sit beside after images as `*-metrics.json`.
These component captures precede the final unit fold/header refinements; they
must not be represented as final-source captures. The later native application
captures below verify those refinements in both themes at desktop and phone
sizes, plus 1100 and 1920 widths:

- Light: `results-s8-fold-header-final/s8-pui_state_captures-base-11d9f--light-full-viewport-matrix-s8-desktop/equipment-schematic/base-schematic/{1440,390}-light.png`.
- Dark: `results-s8-fold-header-final/s8-pui_state_captures-base-de9c9-c-dark-full-viewport-matrix-s8-desktop/equipment-schematic/base-schematic/{1440,390}-dark.png`.
- The same directories contain `*-full.png` and measured geometry/contrast JSON.

The native application captures were inspected independently. They exposed and
resolved a desktop SVG extending below the viewport, then a phone header that
pushed the key readings below the fold. Final measured SVG bottom: 884px at
1440×900. Phone key readings bottom: 831.36px within 390×844. No blocker or major
visual finding remained in that review. The last tooltip/clock semantics and
polling-error fallback changes postdate these images; refreshed final-source
captures are left to the coordinator as directed.

## Verification ledger

| Check | Actual result and evidence |
|---|---|
| TypeScript | Final-source `tsconfig.json` plus every `tsconfig.s*.json`: all nine configurations pass; `checks/types-complete.log`. |
| Changed-file prek | All applicable hooks pass on all 27 changed/new files: `checks/prek-complete.log`; exact file list `checks/prek-files.txt`. Initial run reformatted one file; clean rerun passes. Hooks without matching file types report their normal no-files state. |
| Relevant Vitest | 20/20 across formatter, observation clock and freshness units; `checks/vitest-complete.log`. |
| Lingui | Final extraction and TypeScript compilation pass. All 117 locale files restored bytewise and SHA-256 compared; `checks/lingui-complete/{extract,compile}.log` and `restore.json`. |
| Full S2 plus corrective replay | Original full selection: 80 passed, seven failed, three inherited project-inapplicable duplicate cases. Exact seven failures replayed successfully: all 87 applicable cases verified across those runs. `s2-full.log`, `s2-replay.log`, `results-s2*.json`. |
| Full mimic checkpoint | 27/27 passed, zero skipped at the earlier source checkpoint: `mimic-final.log`, `results-mimic-final.json`. This predates the final AlarmTable inspection controls and clock/polling fallback changes; final-source full mimic replay remains for the coordinator. |
| New layout coverage | Twelve permanent tests cover station/P7 × desktop/tablet/phone × light/dark, included in the 27-case mimic checkpoint. They measure collisions, value clipping, KPI baselines, keyboard scrolling, native marker placement, numeral legibility, complete focus tooltips, honest missing values and 44px phone controls. |
| Focused S8 structure | Full targeted selection: 54 passed, two phone clock-status failures. Exact corrective replay: 2/2 passed. All 56 selected cases verified across these runs; `s8-structural-final.log`, `s8-clock-replay.log`. |
| Native S8 viewport matrix | 2/2 passed, covering both themes at 1100/1440/1920/390 widths; `s8-fold-header-final.log`. |
| Final polling-error checks | 4/4 passed in desktop/phone and both themes; exact missing-timestamp dash, accessible reason and keyboard tooltip assertions retained. `s8-polling-final.log`. |
| Full S3 / full S8 final-source replay | Pending coordinator Mac execution, per final user instruction. `results-s3.json` and `results-s8.json` are discovery-only listings, not executed-suite results. |
| Tracking / whitespace | Tracking passes for 58 work items, 95 screen dispositions and 26 contracts; `checks/tracking.log`. Final `git diff --check` passes. |
| Backend runner | Not applicable: no backend file changed. |

S2's four WebGL startup failures reached real readiness 3–424ms after the
original five-second deadline under Linux SwiftShader. A scratch-only 15-second
assertion budget retained the same renderer, lifecycle, readiness and pixel
checks; no product renderer change was made. Two inherited exact Celsius
oracles now expect the unchanged native formatter's `95 °C` and `95.12 °C`,
retaining precision and uncertain-quality checks. Initial failure artifacts
remain alongside corrective runs.

Other test updates follow the new semantic HTML and tooltip presentation:
missing facts assert an em dash plus their exact accessible reason and real
keyboard tooltip; action tests distinguish exact mutation actions from the new
read-only inspectors; dismissed rows assert absence of every mutation action
while read-only inspection remains available and submits nothing. No assertion
was removed to hide a failure and no skip was introduced.

## Handoff and boundaries

Applied `masterdesignprompt.md`, `designprompt1.md`, O23–O27 and the installed
EQUA industrial telemetry, Mantine/InvenTree, browser-testing, visual-QA and
design-critique guidance. Independent review corrected tablet focus clipping,
phone KPI baselines, marker activation/contrast, touch targets, bad-quality
visibility, duplicated captions and native-screen fold regressions.

The execution ledger and D20 contract evidence distinguish this bounded repair
from deployment or acceptance. The sprint backlog was audited without changing
P08 Ready for review or P31/P35 In progress. No Git write, PR, issue, Azure access,
external publication or changes to other worktrees/containers/ports occurred.

Coordinator next: review the diff and run the full affected S2/S3/S8 desktop and
phone suites plus mimic specs on the Mac; inspect fresh final-source screenshots.
Runtime cleanup verified: the owned `r6-ui-mimic-browser-3010` container is
absent, and neither 3010 nor 15245 has a listening process. Exact checks are in
`checks/cleanup.log`. No other container or port was touched.
