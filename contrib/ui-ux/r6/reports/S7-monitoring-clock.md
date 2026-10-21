# Final merged-tree Monitoring continuity clock repair

21 October 2026; starting tree `937e4f477` on `r6/design-pass-2`.
Bounded native disclosure repair under P50/D03. Primary users: maintenance
engineers and operators inspecting configured monitoring and retained deviations
across completion. Applied `masterdesignprompt.md`, `designprompt1.md`, the
installed EQUA Mantine/InvenTree and browser-testing skills. No Figma.

## Exact merge provenance

The coordinator's final macOS replay reported S0 110/110 and S7 168 passed /
4 failed. All four failures stopped on the comma assertion before reaching the
following timezone assertion. The original log is retained at
`data/devscratch/r6-s7/dp2final-monitoring-clock/coordinator-final-s7.log`.

The disclosure in the merged tree is byte-for-byte integrated parent
`cef890495` (`HEAD^2`, blob `99f923182`), rather than Health/Performance parent
`1fda3ca3e` (`HEAD^1`). Machine-screen commit `eb1b20720` changed its helper:

- Previously: dayjs `DD MMM YYYY, HH:mm:ss`, with the browser's IANA timezone
  appended to every timestamp. The Health/Performance parent retained this
  helper when its branch passed S7 before integration.
- Merged: `formatInstant(value, displayClock())` from
  `health/mimic/observedTime.ts`. This produces the intended shared operational
  `D MMM YYYY HH:mm:ss` spelling, using 24-hour time and seconds. Calling
  `displayClock()` without a declaration selects browser time. The disclosure
  discarded the helper's label entirely, so `Asia/Riyadh` also disappeared.
- The Health/Performance `assets/displayClock.ts` helper is separate and is
  **not called by this disclosure**. Its `displayInstant` uses the same date
  spelling, but this failure did not arise from selecting that implementation.

The machine helper explicitly documents one 24-hour date format for operational
facts, and its unit test requires the spelling without the comma. Both design
passes use that spelling. Retaining it is the intended product behavior; losing
the clock source/label is the defect, so only the obsolete date expectation is
replaced while timezone and factual-time proof are strengthened.

The old S7 fixture supplied no plant timezone. Its `Asia/Riyadh` assertion
therefore proved only the host browser zone. The real supported declaration is
`OccurrencePage.plant_timezone`, returned by
`assets/machine_history.py` from `settings.AIMMS_PLANT_TIMEZONE`. Health summary
has no timezone field. Both reads concern the same machine; the occurrence
declaration is the existing global plant configuration.

## Product behavior and regression proof

`CurrentMonitoring` now resolves one validated clock from a successful occurrence
read and uses it for latest observation, source success/error, occurrence
first/last observation and retained capture time. A single visible `Times` label
names the plant zone and offset. Missing, invalid, pending or failed declarations
use the named browser clock and explicitly qualify the fallback. A failed
refresh cannot reuse a cached plant declaration. Invalid or absent timestamps
remain `Time unavailable`; no original evidence timestamp or dashboard shift is
changed, and no requests or authority are added.

The date assertion intentionally adopts the shared format. The original
`Asia/Riyadh` assertion remains, strengthened with the exact plant-clock label.
The spec explicitly supplies `Asia/Riyadh` and runs its browser in UTC, then
checks latest/source-success `11:10:00`, first-observed `11:05:00`, last-observed
`11:10:00` and capture `11:06:00` on 20 October. Existing provenance, retained
quality/staleness, exact occurrence/Work links, unchanged completed account and
zero-command checks remain. New cases cover missing and invalid declarations
and a denied occurrence refresh while Health remains readable; those facts
convert to UTC and cannot retain a stale Riyadh label.

## Runtime boundary and evidence

No file was edited until the requested 30-second lsof polling established that
both coordinator ports 15222 and 15230 had no listener at **12:27:03 UTC**.
The gate record is `data/devscratch/r6-s7/dp2final-monitoring-clock/port-gate.md`.
No coordinator server or container was stopped or reused.

All task renders use **Linux Chromium** in the owned Docker container
`r6-dp2final-browser-3006`, websocket **3006**, with the isolated fixture harness
on **15237**, as directed by `codex-browser-dp2final.md`. The reproducible scratch
config, retained first attempt, final focused/full logs, compiler and unit logs,
captures and cleanup evidence are under
`data/devscratch/r6-s7/dp2final-monitoring-clock/`.

## Verification

| Check | Actual result and evidence |
|---|---|
| First focused spec | 42 passed / 6 failed. Only the three new fallback cases in both projects failed: expected offset text `UTC` instead of the existing native browser label `UTC+00:00`. Corrected those expectations; original failures already passed. Retained `focused.log` and failure artifacts. |
| Final focused spec | **48/48 passed**, desktop/phone, light/dark and explicit fallback cases; `focused-final.log`. |
| Initial full S7 attempt | Interrupted (exit 130): 63 passed / 2 failed / 1 interrupted / 112 unrun. The scratch wrapper omitted the provided example's Riyadh browser default. Linux browser defaulted to Pacific time; packet receipt confirmation and command assertions passed but its existing `12:17` date assertion saw `02:17 -07:00` in both themes. Corrected only the scratch wrapper to `timezoneId: Asia/Riyadh`, keeping the monitoring spec's explicit UTC override. Original log/artifacts retained under `full-s7-initial-browser-zone`. |
| Full S7 | **178/178 passed**, 20.5 minutes, exit 0; `full-s7.log`. No retries, skips, timeout changes or source changes between focused and full runs. |
| App and sprint types | All **nine** pass: default, S0, S1, S2, S3, S4, S6, S7, S8; `typecheck-results.json`. S7 was rerun after the final string correction and passes in `types-tsconfig.s7-final.log`. |
| Additional configs | `tsconfig.lib.json` passes. Unchanged standalone `tsconfig.node.json` fails TS5107 on its legacy Node resolution. Rerunning with the app's existing `--ignoreDeprecations 6.0` policy exposes 25 dependency/module-resolution diagnostics. This project contains only unchanged `version-info.ts` and `vite.config.ts`; no unrelated config/dependency change was made. Both attempts retained in `types-tsconfig.node*.log` and result JSONs. |
| Relevant units | **11/11, 2 files**: machine `observedTime` and Health/Performance `displayClock`; `vitest-clocks.log`. Vitest uses its native API with `envFile:false`; exact argv and result are retained. |
| Hooks | All six changed files pass `PATH=/opt/homebrew/bin:$PATH prek run --files`; `prek-final.log`. Initial Biome formatting of the new tests and every interrupted/failed attempt remain retained. |
| Tracking and whitespace | 58 work items / 95 screen dispositions / 26 contracts; `git diff --check` passes. Backlog statuses audited without changes. |
| Captures | Five originals inspected: four final focused captures (desktop light plant/current facts; phone dark retained deviation/capture facts; desktop refused-refresh fallback; phone invalid-zone fallback), plus the final full-run phone light plant/current facts. Paths and SHA256 are in `capture-index.md`; all render in Linux Chromium. |
| Owned runtime | Browser stop exits 0 in `browser-stop.log`. At **13:05:36 UTC**, ports **15237** and **3006** have no listener and the named container no longer exists; `cleanup.json`. The first check ran before the asynchronous Docker stop finished; it is retained as `cleanup-during-stop.json`, then the completed stop and clean final check were verified. |

Browser commands, from `src/frontend`:

```sh
AIMMS_HARNESS_PORT=15237 AIMMS_EVIDENCE_RUN=focused-final npx --no-install playwright test --config ../../data/devscratch/r6-s7/dp2final-monitoring-clock/browser.config.mts tests/s7/pui_monitoring_continuity.spec.ts
AIMMS_HARNESS_PORT=15237 AIMMS_EVIDENCE_RUN=full-s7 npx --no-install playwright test --config ../../data/devscratch/r6-s7/dp2final-monitoring-clock/browser.config.mts
```

These are isolated native fixture checks, not new coordinator GPU, live plant,
deployment, independent human-review, O22 score or product acceptance. Backlog
parent statuses and all provider authority remain unchanged. No Git write,
issue/PR or Azure access.
