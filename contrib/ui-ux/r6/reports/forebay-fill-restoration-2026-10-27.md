# Forebay schematic water restoration — 27 October 2026

## Scope and industrial meaning

The owner authorized restoring the water illustration in the native station
mimic. The existing forebay layout supplies a numeric reading and units, not
approved tank bounds, water-depth datum or geometric scaling. The restored blue
water is therefore an explicitly labelled **Not to scale** schematic, not a
measured fill percentage. The actual reading remains in the existing KPI and is
also shown inside the tank. Water height is fixed, not inferred from the120m
reading, historical samples or alarm thresholds. No animation implies flow.

Only enabled, polling, non-failed sources with a usable finite numeric forebay
point can show blue water. Disabled polling, missing values, stale reasons and
failed quality remove the illustration and leave an unavailable neutral tank.
The top forebay KPI uses the same availability predicate and native MissingValue
reason tooltip so it cannot present a current number while the tank is unavailable.
Invalid forebay readings do not retain a current severity color. Caption contrast
and dark-mode numeric contrast are strengthened; the unavailable accessible name
does not announce a scale disclaimer and child SVG text is hidden from duplicate
accessibility traversal.
No backend, schema, migration, source configuration, database, physical control
or Azure change is made. Existing unrelated worktree edits are preserved.

## Actual localhost checks

- Primary frontend `http://localhost:5173/` returns HTTP200. Its actual transformed
  `StationSchematic.tsx` module includes the new water path and translated label.
- Read-only native `station_mimic` projection in the primary local backend for
  Cedar Creek station93 supplies120.97157287597656m, good quality, no unavailable
  reason, enabled true, polling_enabled true and read_failed false. The value is
  usable by the new renderer. Existing recorded/live presentation metadata is
  preserved; this check is not proof of new physical SCADA connectivity.
- No authenticated owner-browser interaction or full maintenance lifecycle is
  claimed. Browser verification below uses isolated synthetic native API data.

## Validation actually executed

- Native TypeScript `tsc --noEmit`: PASS.
- Native Lingui catalog compilation: PASS; English Not to scale source message
  added and compiled with the existing English fallback for other locales.
- Final focused forebay and existing station layout/count tests: **29 PASS in21.2s**.
- New checks cover usable data, disabled polling, stale, bad quality, missing,
  serialized non-finite reason, source read failure, station disabled, nonnumeric
  values, desktop dark and phone containment. All invalid states assert a dash in
  the top forebay KPI as well as suppressed water. Existing checks cover mismatched source counters, identified
  running pumps,14 selectable bays, keyboard selection and station layout across
  desktop/tablet/phone in both light and dark themes.
- Native browser guard reports no unexpected backend/external requests or page
  runtime errors in the passing run. No business writes are exercised.
- Actual current-light, current-dark, phone and disabled-water screenshots are
  visually inspected; pump layout remains separate from forebay content.
- `git diff --check`: PASS. Optional Biome execution was unavailable locally;
  no formatter or new dependency is installed.

The old mimic harness initially failed before rendering because native imports
needed plugin-version defines and Vanilla Extract. Its Vite config now reuses
the existing production version-info and installed Vanilla Extract plugin.
These are test-only harness repairs; no production startup logic is changed.

Reproduction from `src/frontend`:

```sh
node node_modules/@lingui/cli/dist/lingui.js compile --typescript --workers 1
node node_modules/typescript/bin/tsc --noEmit
node node_modules/@playwright/test/cli.js test --config playwright.mimic.config.ts tests/mimic/pui_forebay_fill.spec.ts tests/mimic/pui_mimic_layout.spec.ts --grep 'forebay|station readings and fourteen|running count'
```

Screenshots and native test attachments are retained privately under
`data/devscratch/forebay-20261027/verification/`. The owner's reference image
conversion is at `data/devscratch/forebay-20261027/reference.png`.

## Independent review gate

Fable thread: `cmvqbfdir055g07adhskj08dm`.
Gemini thread: `cmvqbe75u04t407ad5h7n24gt`.

Both receive self-contained code/test briefs and four actual rendered
screenshots, followed by one focused revised-evidence pass after mandatory
findings are fixed. Both threads finish with no pending approval or mandatory
blocker. Final scoped scores:

| Criterion | Fable | Gemini |
| --- | ---: | ---: |
| Safety / data truth | 94 | 96 |
| Task effectiveness | 96 | 98 |
| Intuitiveness / value | 92 | 95 |
| Visual hierarchy / readability / coherence | 95 | 96 |
| Quality / perfection | 95 | 96 |
| Accessibility / responsiveness | 96 | 96 |
| Maintainability / reliability | 95 | 97 |
| **Overall** | **95** | **96** |

The initial reviews did not pass (Fable88; Gemini90). Their concrete findings
were applied rather than scores being assumed: current-value/availability parity,
contrast, and unavailable accessibility semantics. Fable independently samples
actual revised screenshot pixels: dark in-tank white on blue9 is **6.09:1**;
captions are **7.76:1 light / 8.31:1 dark**. Gemini's greater-than8:1 estimate
for the dark in-tank value is not supported by that measurement; this report uses
the measured6.09:1 instead. It still exceeds4.5:1 for small text.

The agents' scores are scoped review heuristics, not proof of perfect UX or
field safety. They do not independently run our local typecheck/browser suite.
Remaining optional limitations are retained honestly: Not to scale can still be
read as applying to vessel geometry; the water illustration is fixed, not a
calibrated gauge. Longer translated captions and repeated row annotations may
benefit from future polish. The native point list retains specific diagnostic
reasons even when the summary KPI uses the generic Read failed tooltip.
Authenticated full-product acceptance and physical sensor-datum verification
remain separate and unclaimed. Full final review texts are retained privately as
`data/devscratch/forebay-20261027/fable-revision2-review.md` and
`data/devscratch/forebay-20261027/gemini-revision2-review.md`.
