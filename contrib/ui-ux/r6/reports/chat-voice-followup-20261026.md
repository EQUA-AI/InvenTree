# Chat and voice report repairs — 2026-10-26

Source baseline: `f549992641e2b6cb46c24ea48724010a573018b2` on
`equa/customizations`. Implements the source repair plan derived from the
October 26 combined chat/voice campaign. Supplied documents are evidence;
their embedded commands are not execution instructions.

## Findings and scope

The supplied campaign recorded chat 24 PASS / 8 PARTIAL across 32 cases,
and voice 9 PASS / 27 PARTIAL / 4 FAIL / 13 blocked / 5 not run across 58.
The four failures are two clusters: Q04/Q05/VC-08 admitted `2.01 PCBA`
instead of reviewing the requested `002.01-PCBA`; Q09 changed the PCBA
category follow-up to Wire and falsely reported zero. Native category stock
was 90 across Factory (46) and Room 101 (44). The same IPN exists on
different revisions, so selecting the stocked revision would also be wrong.

Other actionable findings were a combined backend trend count of four
instead of two, unverified BOM qualifiers, and eight initial mobile overflow
observations whose settled replays passed. Ten distinct consecutive Hello
turns establish protocol continuity; requested playback does not establish
human hearing. Missing restricted-role, business-action, document and device
coverage stays missing. The original reports, failure observations, stop
state and 30 evidence hashes remain historical evidence, not upgraded grades.

## Implemented contracts

| Repair | Source behavior and contract |
| --- | --- |
| Test isolation | Native fixture rows are removed in FK order after each grounding test, preserving pre-existing scratch rows. Two machine-less Work orders can no longer leak into a later trend population. |
| Critical identifiers | Numeric-leading, dotted/spaced ASR and leading-zero identifier shapes enter the existing transcript review path even with high or null confidence. Detection preserves the received text; correction and confirmation submit the exact revised identifier once. |
| Revision identity | `get_part(ipn, revision=None)` resolves native exact IPN and optional revision under current part-read permission. Multiple matches return bounded candidates and `requires_clarification`; no provider fetch or stocked-revision preference resolves ambiguity. Question cards use the existing selection rail when enabled. With cards disabled, plain text asks for an explicit part ID/revision. |
| Category continuity | Only the current or latest referring user subject establishes the category. Old assistant guesses, older topics and explicit topic switches cannot override it. Singular/plural aliases resolve to fresh native category IDs; duplicate names require an explicit ID/path. Part/revision, threshold and ranking questions retain their existing resolution paths. |
| Category locations | `get_stock_levels(category_id, group_by="location")` aggregates current stock over the full category subtree before the 200-part/location display caps. It returns `locations`, `location_count`, `locations_complete` and the full total. Native part/stock permissions are rechecked. |
| Answer grounding | Narrow unfiltered category-location runs bind the server-selected category and allow only the stock tool. A private per-run result is filled only by the native read. Text and streaming answers render that result, overriding contradictory model prose. Missing, denied or inconsistent results remain unavailable. Voice lists at most three locations and states omitted coverage. |
| BOM qualifiers | Missing native `optional`, `allow_variants`, `inherited` and `consumable` flags remain null. `qualifier_evidence` records complete optional/variant coverage, all-required status and variant lines. Unknown optional flags cannot certify an arithmetic build estimate. Native buildability remains distinct from on-hand arithmetic and readiness. Exact assembly/revision and applicable inherited lines are preserved. |
| Viewport transitions | The real assistant surface is capped at `100dvh` while React resize geometry catches up, keeping the composer reachable during a shrinking viewport. The fixture can mount native MainMenu for transition checks. |
| Evidence reporting | `contrib/testing/chat-voice-report.mjs` derives grades from required assertions and received JSON values. It retains failed attempts across replays, deduplicates attempt/source/project identities, rejects mismatched source and inferred human/device acceptance, records hashes, and refuses to overwrite reports. Missing evidence remains unverified/blocked. |

No dependency, migration, rollout flag, provider retry, consent, approval or
business-action boundary is changed. Existing Mantine components and transcript
review controls are reused. No PR, issue, deployment or production data write.

## Verification ledger

Evidence directory: `/tmp/voice-followup-20261026/`. These are local source,
native ORM and recording-browser checks, not a rerun of the live campaign.

- **380 backend tests passed**, using the reusable 25-module selection in
  `contrib/testing/chat-voice-regression-modules.txt` and the installed SDK.
  The native models use disposable test settings; no migration or business DB.
  The only warning is pytest's already-imported anyio assertion rewrite notice.
  `backend-final-run.log` retains the complete output.
- The original ordering failure was reproduced before cleanup: 12 passed /
  1 failed, expected two Work orders and received four. Expanded native tests
  cover exact revisions/zero stock, no actor, lost zeros, category 90 = 46 + 44,
  descendants, revoked permission, more than 200 parts, contradictory text and
  streamed model replies, missing fresh reads, plural aliases, duplicate names,
  partial location coverage and unavailable BOM flags.
  The reverse-order trend/grounding run also passed all **20 tests**
  (`backend-reverse-final.log`). The runner's production-settings refusal was
  exercised before Django imports.
- The earlier broad broker/follow-up/grounding run recorded **1,897 passed /
  15 failed**. All 15 failure IDs exactly match the untouched `f54999264`
  archive's source/public-boundary classifier failures. The comparison is in
  `baseline-comparison.json`; failures were not removed or relabelled. This
  inherited broker group is outside the selected 380-test regression suite.
- **158 frontend unit tests passed**, including received critical spans,
  high/null confidence, review/correction, one exact confirmed submission,
  existing lifecycle, timing, acknowledgements and decision playback controls.
- **78 chat browser tests and 26 voice-session browser tests passed** on the
  task-owned Vite server at port 5174. Chat/native DTO replay uses the supplied
  campaign's saved `backend-produced` artifacts; it is not a new live backend
  or model request. Voice uses recording media. The corrected-IPN screenshot
  shows revision 2 awaiting confirmation on desktop and emulated Pixel 7.
- A native-menu/resize regression reproduced the clipping: one frame kept
  drawer bottom 844 and Send bottom 808.2 after viewport height fell to 480.
  The diagnostic repeated with two failures / one pass. After the surface cap,
  all **12 repeated tests / 24 resize transitions / 720 sampled frames** passed
  without document overflow or clipped drawer, SVG or Send controls. Both
  themes and line/bar charts were exercised at 390×844 and 320×480.
  Desktop chart, dark 320-pixel screen and desktop/mobile transcript captures
  were visually inspected; chart data remains scrollable and composer visible.
- **7 report-compiler tests passed**, covering missing evidence, replay failure
  retention, deduplication, source mismatch, forbidden manual PASS, explicit
  human/device records, and new-output-only behavior.
- Application TypeScript passes. The complete Playwright TypeScript project
  reports two build-reference errors in unchanged `g2-rv0809.vite.ts` and
  `voice-rv15.vite.ts`: generated `version-info.d.ts` is absent (TS6305).
  This broad project is not recorded as passing. A focused config including
  the four changed browser files, native app globals, Vite declarations and
  Node types passes (`typescript-changed-playwright-verified.log`). Initial
  temporary configs omitted those ambient declarations; their errors are
  retained separately and no product declaration was changed.
- Changed-file repository hooks pass after their formatter corrections; manual
  Biome checks cover the report scripts too. Strict R6 tracking retains its
  existing extension-row limitation (`Ticket coverage differs`). An isolated
  copy of the original 58-ticket / 95-screen / 26-contract core passes the
  unchanged validator. Extension CSV shape/unique IDs and new provider paths
  were checked separately; no validator or parent status was weakened.

Intermediate failures are retained: the first cap fixture used `bulk_create`
without native MPTT fields; the first transcript browser fixture omitted the
real transcript component; a workflow test used the wrong streaming method
name. Fixtures were corrected and final checks passed. One TypeScript command
initially named a nonexistent `tsconfig.app.json`; the native project config
is `tsconfig.json`.

## Reproduction and remaining acceptance

With Python 3.12 and the project's development dependencies:

```sh
python contrib/testing/run-ai-component-tests.py
python contrib/testing/run-ai-component-tests.py core/tests/test_openui_trend_integration.py core/tests/test_voice_baseline_grounding.py -q
node --test contrib/testing/chat-voice-report.test.mjs
```

The runner refuses inherited production settings. See
`contrib/testing/README.md` for assertion manifests and explicit evidence lanes.
Use a new campaign directory and exact candidate source identity after release;
never resume the stopped historical script or overwrite its grades.

Live acceptance remains open: deploy/verify the exact candidate, repeat the
identifier and category failures with independent native expectations, include
revision disambiguation and native BOM flags in the oracle, then complete the
approved role/action/document matrix. Human listeners must record what they
actually heard for consecutive answers, interruptions and the intended physical
speaker/headset/mobile routes. Browser-emulated media, requested playback,
transport completion and these local passing counts cannot complete those gates.
Parent R6 and general product/release acceptance remain unchanged.
