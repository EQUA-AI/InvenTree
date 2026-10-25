# Performance labels and synthetic alarm overlays — 24 October 2026

Status: implemented on `inventTree-aniket`; localhost availability verified,
not human/product or Azure acceptance. No commit, push, PR or database migration.
Applies the R6 master/design prompts and installed Mantine, industrial telemetry
and browser-testing skills; no Figma or architecture replacement.

## Root causes and changes

- Labels such as `/Bh+7r`, `NuQMyh` and `Cmfsra` were Lingui message IDs, not
  failed signal mappings. The English source catalogue omitted current source
  strings. Isolated extraction identified 2,794 missing messages; these are
  appended without changing any prior English bytes or non-English `.po` files.
  Native compilation restores the labels and English fallback. A regression
  checks representative Performance, equipment, mimic and test-alarm messages.
- Test alarms intentionally do not overwrite measurements. Performance consumed
  only those measurements, so it never learned of a synthetic condition.
  The scoped signals API now supplies typed `test_alarms` annotations in one
  additional batched query. Only explicit synthetic UI tests with a matching
  active binding on that machine and open/acknowledged status are exposed.
  Suppressed, resolved, unlinked and foreign-scope tests are excluded.
- A typed Performance context supplies the exact affected binding to chart
  families and KPI cards. A red **TEST alarm — Critical** notice distinguishes
  the exercise value from the measurement. The affected chart shows a red dashed
  test-value annotation, always included in its axis range, including fullscreen.
  It is labelled as an active exercise, not a historical sample or configured
  threshold. Native configured-limit keys and measured traces remain unchanged.
- Affected family/KPI badges show the test condition instead of a misleading
  green summary. Genuine critical/warning, stale, quality and unavailable-history
  exceptions remain visible independently. Unrelated charts are not made red.
  Refreshed annotations disappear when a test ends; real conditions stay intact.

## Verification and execution ledger

- Frontend: **1,115 tests / 120 files pass**; app, library and S3 TypeScript
  checks and production build pass. Existing large-chunk advisory remains.
- Performance Chromium fixture suite: **10/10 pass**, including open and
  acknowledged test overlays, exact red dashed SVG annotations, unchanged
  measured values, unrelated chart isolation and recovery. Native time controls,
  zoom, failure, threshold-boundary and heatmap checks remain covered.
- SQLite native health/mimic/test-alarm suite: initial and final typed-serializer
  runs each **83/83 pass**. Disposable containers only, no owner DB.
- Live Mac Chromium on5173: desktop and phone verify readable labels and the
  owner's active Pump07 test9. Measured Motor Core RTD4 remains40.7°C/normal;
  the separately labelled exercise is159.5°C/critical with a red dashed chart
  annotation. All signal rows and the active test status are identical before
  and after; temporary login sessions are removed. No test was raised or ended
  by these checks. Native phone full-app confirmation is retained.
- Four live overview/chart captures are inspected. Earlier partial browser
  checks used nonexistent heading/library-wrapper locators or captured history
  while still loading; those receipts are retained, not counted as acceptance.
  The final checks wait for the real history response and assert actual SVG
  lines/labels. This is Chromium evidence, not a Safari-specific execution claim.
- All applicable cached repository hooks and `git diff --check` pass.

Evidence root: `data/devscratch/test-alarm-closeout/` (ignored local artifacts):
`performance-sqlite/`, `performance-sqlite-final/`, `performance-live/`,
and the retained `performance-alarm-*` logs. Source and evidence hashes are
recorded in `source-capture-manifest.json`.

## Boundaries and handoff

No sensor/SCADA writes, fake historical series, altered engineering limits,
alarm cleanup, Work lifecycle changes, SAP writes, external emails or Azure
changes. The owner's new test remains available to close personally. Hard
refresh Safari after loading the updated localhost UI. Existing parent backlog
and contract acceptance statuses stay unchanged; human review and real browser/
provider acceptance remain separate.
