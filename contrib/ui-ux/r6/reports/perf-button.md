# Integrated Performance read disclosure repair

21 October 2026. Bounded follow-up in `r6/perf-button`, starting at `3df4aeb32`.
This addresses HANDOFF integrated checkpoint item 1. Development and fixture
review by Codex; no independent human review, deployment or product acceptance.

## Cause and product change

The two merged design passes left complementary disclosures in `LiveIndicator`
and `HistoryProvenance`. `PumpPerformance` passes the same `seriesQuery` response
to both: the first disclosure contained expected sample interval and last-poll
attempt; the second contained window, limits and sample counts. These are
metadata for one selected-history read. Station Performance shared the same
duplicate composition.

The existing history disclosure now contains all those facts. `LiveIndicator`
keeps its freshness, polling, complete/sampled and display-shift summaries,
without a duplicate disclosure. The moved poll timestamp uses the existing
validated display clock alongside the window timestamps. Comparison reads keep
their own provenance, derived from their respective responses.

The S8 acceptance spec retains the exact `Show read details` locator and every
existing scale, contrast, navigation and keyboard check. It additionally checks
one disclosure, absence of that disclosure from the live-status area, visible
cadence/poll/window/budget facts after expansion, and return to one collapsed
control. No locator uses `.first()` to bypass ambiguity.

## Design and verification scope

Applied `masterdesignprompt.md` and `designprompt1.md` to the native pump/station
Performance screen. The operator's task is to distinguish current observations
from selected-history evidence and inspect the source read's cadence, poll time,
window and budget. The implementation reuses React, TypeScript and Mantine's
existing disclosure and presentation tokens, with no dependencies, routes,
permissions, API or Machine Details/header changes.

All renders use **Linux Chromium in the owned Docker browser server** on
WS `127.0.0.1:3007/r6-perfbtn`, forwarded to the fixture harness on **15238**.
These are mocked native surfaces, not authenticated deployed data or macOS GPU
acceptance. No Azure access or Git write command was used.

Evidence root: `data/devscratch/r6-s8/perf-button/`. The browser configuration
refuses a harness port other than 15238. Browser projects run serially with one
worker and no retries. Exact commands and results are being finalized after
the full S8 desktop/phone and S3 replays.

From `src/frontend`, using the installed tools:

```sh
AIMMS_HARNESS_PORT=15238 node node_modules/playwright/cli.js test \
  -c ../../data/devscratch/r6-s8/perf-button/browser.config.mts \
  tests/s8/pui_monitoring_acceptance.spec.ts \
  --project=s8-desktop --project=s8-phone
AIMMS_HARNESS_PORT=15238 node node_modules/playwright/cli.js test \
  -c ../../data/devscratch/r6-s8/perf-button/browser.config.mts \
  --project=s8-desktop --project=s8-phone
AIMMS_HARNESS_PORT=15238 AIMMS_BASE_SPRINT=s3 \
  node node_modules/playwright/cli.js test \
  -c ../../data/devscratch/r6-s8/perf-button/browser.config.mts
```

The actual runs also set a distinct monitoring evidence root and browser output
directory beneath the evidence root. The existing cached `axe.min.js` was copied
from the earlier part 2 evidence, with SHA-256
`a0afc408abecbe06ff7675033e7ea2c4046618162167aaa4ce1030b10963dece`;
there was no download or dependency installation.

## Engineering checks

- Targeted acceptance spec: **8/8 passed**, desktop/phone in both themes.
- Full S8 requested projects: **444 passed / 88 existing skips / zero failures**
  (41.6 minutes). Desktop: 264 passed / 2 skipped; phone: 180 passed / 86 skipped.
  `s8-work` was outside this task's requested browser projects.
- Disclosure capture probe: **2/2 passed**, covering 1100, 1440, 1920 and 390 px
  in both themes. Inspected all eight expanded-detail captures and four full
  native Performance matrix samples. The combined metadata remains readable,
  with zero horizontal overflow and the existing 40px phone control height.
  The first element capture intersected the native sticky header; final capture
  framing measures that header and requires full card containment. Initial
  captures remain in `captures-initial/`; final images/facts are in `captures/`
  and `checks/visual-review.json`. No header product change was needed.
- SSR component units: **16/16 passed**, including unique metadata, supplied and
  missing intervals, absent/invalid polls and UTC/Riyadh date-boundary behavior.
- TypeScript: app, library and all eight sprint projects pass (**10 projects**).
  The eleventh, standalone `tsconfig.node.json`, retains the pre-existing TS5107
  TypeScript 6 legacy Node resolution deprecation recorded in HANDOFF. Its
  unchanged inputs also retain the supplemental dependency-resolution errors
  exposed by ignoring only that deprecation. No override is reported as a pass.
- Lingui extract and compile pass. All **117 locale files** and their path set
  were restored bytewise in `finally`; before/after manifests match and no
  generated locale files remain. Logs/manifests are under `checks/`.
- Changed-source prek checks pass. Final documentation hooks and tracking checks
  will be recorded after the result ledger is complete.

The initial focused run exposed an overbroad newly added assertion counting the
native Refresh button as a disclosure. It was corrected to the exact disclosure
name. The failed run and artifacts remain in `focused-initial.log` and
`initial-results/`; the final focused run passes in `focused.log`.

## Tracking and runtime

P31/P35 remain In progress, P32 Ready for review, P34/P36 Blocked. The S8-04
board row now matches the existing P35 backlog status; the backlog itself was
audited without changing parent acceptance states. D21 evidence will record the
bounded result. Other sprint boards and full integrated S0–S8 replay remain
separate work.

Owned browser/container and harness cleanup will be recorded after the last
browser check. No other container or server is touched.
