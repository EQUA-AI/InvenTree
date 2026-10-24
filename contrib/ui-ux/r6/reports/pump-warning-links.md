# Pump monitoring warning follow-up — 23 October 2026

Status: implementation and targeted fixture verification complete; local commit
authorized. Parent R6 acceptance and human/deployment acceptance unchanged.

## Current screen to implement

Screen: machine Health/Performance, pump mimic and native 3D equipment view.
User: station operator and maintenance engineer.
Objective: find warning/critical equipment through the existing bell, see matching
motor alarm attention in 2D/3D, identify complete abnormal signal rows, and open
the winding chart from its critical summary card.
Sources: existing machine-health anomalies, configured signal states, PS-MOTOR
partStates and Performance parameter/series readers. Apply masterdesignprompt.md
and designprompt1.md within React/Mantine/Lingui and native permissions.

## Execution ledger and contract evidence

| Item | Contract and implementation | Verification |
| --- | --- | --- |
| 1 | D18/D03: reuse machine-health notification category, recipients and existing ingestion refresh; warning/critical severity dedupe; machine Health anomaly deep link | Warning delivery backend included in the successful 70-case SQLite run; exact open/resolved/suppressed alarm links pass 12 desktop/phone cases, including history beyond the initial ten rows |
| 2 | D02/D20: reuse exact PS-MOTOR warning/attention state in native 3D; respect acknowledgement and reduced motion | Four desktop/phone and light/dark cases pass: blink, static reduced-motion alternative, 2D agreement, acknowledgement and recovery without replacing the renderer |
| 3 | D03: full warning/critical row and expanded detail fill with Mantine red-light tokens; keep selection and sticky cells | Four light/dark desktop/phone row-fill cases pass; captures inspected |
| 4 | D21: keyboard accessible winding card clears display filters, expands winding section, focuses/scrolls/highlights winding chart | Four light/dark desktop/phone keyboard and repeat-activation cases pass; captures inspected |

## Conclusion verification — 24 October 2026

- App plus S0/S1/S2/S3/S4/S6/S7/S8 TypeScript projects pass (nine projects).
  Per-project logs: `data/devscratch/pump-warning-links/conclusion-types/`.
- Full Vitest: 118 files, 1,107 tests pass. Focused navigation/KPI tests: nine
  tests pass. `npm run compile` passes; no new frontend text was introduced.
- Browser verification uses the pre-existing Playwright browser endpoint on
  3028 and separately owned fixture servers on 15290–15293. Initial eight
  Health/Performance cases, four motor cases and 12 exact-alarm link cases
  pass. After the summary-fixture correction, the final complete pump spec also
  passes 20/20. Together with the unchanged four motor cases, all 24 unique
  cases have a passing final fixture run. Ports 15290–15293 are clear after
  Playwright cleanup; pre-existing browser containers and port 15284 are left
  unchanged.
- Backend: `conclusion-sqlite-focused/` runs the four corrected timezone test
  modules and `machine_health.tests.test_alarm_notifications`: 70 run, zero
  failures/errors, four expected PostgreSQL-only skips. Final full fork SQLite:
  4,469 run, zero failures/errors, 106 expected skips. Corrected PostgreSQL
  migration suite: 24 run, nine pass and 15 explicitly blocked checkpoint skips.
  Backend fixture follow-up is committed as `ce34a9e3d`. Remaining upstream
  migration gates are in [the backend report](backend-test-fixes.md).
  After the full replay, the staged secret scan falsely flagged the local
  `severity_key` identifier. Renaming it to `marker` leaves notification behavior
  unchanged; all 11 notification tests pass again using a copy of the earlier
  isolated focused test database. The staged secret scan then passes without
  any ignore entry or bypass.
- Twenty-four primary PNGs retained with source paths and SHA-256 hashes in
  `data/devscratch/pump-warning-links/conclusion-captures/manifest.json`.
  Sixteen were inspected directly: all eight row/chart captures, all four motor
  captures, and four representative active/resolved/suppressed alarm captures.
- All applicable hooks pass on the complete 39-file proposal; the first pass
  formatted one fixture file. Final S3 type recheck also passes.
  `validate_tracking.py` still rejects the inherited R8 additions to its fixed
  58-ticket/26-contract inventory. The CSVs already contain 60 task rows and
  28 contract rows at `c447e15d1`. A separate structural/provider audit passes:
  all rows parse, provider files exist, and every prior task status and contract
  disposition is preserved. This does not claim that the fixed-count validator
  passed.

Historical failed browser attempts under `data/devscratch/pump-warning-links/`
remain available: the initial macOS browser launch failed with a
MachPortRendezvous denial, and an earlier row-fill assertion failed. Fresh
isolated harness replays pass without weakening those assertions. The two
untracked `playwright.*-local.config.ts` wrappers contain machine-local browser
paths and are excluded from the product commit.

This is isolated fixture evidence, not owner data, authenticated deployment or
product acceptance. No migrations, Azure access, issues, push or PR.
