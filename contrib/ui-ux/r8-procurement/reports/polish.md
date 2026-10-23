# R8 production polish — ready for human review

23 October 2026. Worktree `r8-polish`; base `3f82042d0`.

Scope: U1 action alignment, U2 context hierarchy, U3 receiving labels, U4 Closeout and all seven Work tabs. Native React/Mantine architecture, named approval, recorded-only email, replay, read-only Materials and scope/permissions remain binding. Fixture-only local verification; no real showcase or Azure access and no Git writes.

| Work | Status | Evidence |
| --- | --- | --- |
| Read binding contracts, owner decisions, R6 guidance, package and skills | Complete | integration-wave1, P1/P2/P3, p2 contract, gap analysis, R8 package, R6 master/designprompt1 |
| Baseline captures | Complete | 36 exact-size captures from the immutable before bundle; all 32 changed production files in its source snapshot match HEAD bytewise |
| Production wording and layout | Complete | Audit dispositions below implemented and replayed on fixtures |
| R8 config and regression updates | Complete | `playwright.r8.config.ts`, fixture-only server launcher; desktop and phone projects |
| Type/Lingui/unit/browser/prek verification | Complete for this lane | Actual results and inherited gates below; no deployment/product acceptance inferred |
| Screenshot inspection | Complete | 72 primary before/after images plus 79 supporting images inspected; hashes and dimensions retained |

Current screens: Work, Diagnose, Plan & Safety, Parts, Verify & Return, Closeout, History; native PO work/source context and receiving modal. Users: maintenance technician/supervisor, purchasing and stores. Objective: see actual facts, missing facts and next actions without developer prose. Tokens: existing Mantine spacing, colors, typography and native breakpoint system. Applied skills: equa-mantine-inventree, equa-visual-craft, equa-interface-audit and equa-browser-testing. No new motion, theme or dependencies.

## Audit findings and dispositions

| Surface | Finding | Disposition |
| --- | --- | --- |
| All tabs | Repeated authority warning plus Work reference in navigation; uppercase dimension-prefixed badges | Compact linked authority state, details on focus/hover; normal-case values with dimension available to screen readers and on hover |
| Work | Outcome absence is a paragraph; service/obligation caveats repeat; Board cards caption; provider coverage prose | Missing outcome as dash; one service info tooltip; omit redundant card caption; coverage in information tooltip |
| Diagnose | Missing diagnosis and preliminary qualification explained with paragraphs; source/attempt caveats compete with findings | Missing diagnosis as dash; preserve preliminary/disputed labels; details in tooltip |
| Plan & Safety | Missing package/provider and routine absence rendered as long warnings; repeated physical-authority caveats | Labelled package/authority sections with missing dash; one authority explanation; actual restrictions stay visible |
| Parts / U1 | Source, approval, link and acknowledgment commands appear at different heights under factual columns | One consistently aligned command row; six factual columns preserved; native material/custody disclosure retained |
| Verify & Return | Empty criteria/test states and release caveats obscure test facts | Missing tests as dash with reason; retain not-evaluated/failed/pass distinction and independent acceptance/permission records |
| Closeout / U4 | Extraction, authority, time and export paragraphs; raw narrative coordinates; uppercase material state | One account info tooltip; sources in field tooltips; missing values as dashes; sentence-case material status |
| History | Native/no-provider and authority disclaimers repeat around actual hold/resume history | Compact empty history and information tooltip; retain actual events/actors/reasons |
| PO / U2 | Work/source group lacks a heading and clear containment | One Work / source heading, same four independent source/placed/document/ack columns |
| Receiving / U3 | Native title/field case and raw reservation code | Receive stock / Batch for maintenance only; code in info tooltip; pending/attention/read-failure labels stay visible |

## Implementation and consumer contracts

Missing fallback Parts notes/codes and request owners, checkpoints, priority and updater facts use dashes with reasons. Header status badges remain static in the keyboard order, with screen-reader descriptions and hover information. Missing-value and information controls retain keyboard/touch access.

The dispositions above also cover assistant/request, assignment, source coverage, material reconciliation and downtime captions. Calibration keeps its visible incomplete-traceability state; its explanation and the separate calibration-acceptance caveat are information controls. Disputed evidence, restrictions, scope review, pending writes and read failures retain visible states. The account only asks for missing required facts and omits the duplicate “known facts” card when the prepared account already contains them.

U1 commands share one aligned row under the six factual columns, with a separator and wrapping phone layout. Source evidence and the native material disclosure stay beside their owning facts. Named approval, source, placed order, recorded PDF/email, acknowledgment, receipt and reservation remain independent.

[Consumer contract evidence](../contracts/polish.md) records the unchanged handlers, permission/stale predicates, hidden shape and exact IDs. Producer authority remains [integration-wave1](integration-wave1.md), [P1](P1.md), [P2](P2.md), [P3](P3.md) and [p2](../contracts/p2.md). No backend file, command guarantee, machine scope, real stock, commitment or completed showcase Closeout changes.

## Source evidence

| Disposition | Source |
| --- | --- |
| Shared information and missing-value controls | `src/frontend/src/components/items/ValueTooltip.tsx:139` |
| Work navigation and authority | `src/frontend/src/pages/tasks/workorders/WorkPanelNavigation.tsx:88` |
| Header badge values | `src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx:974` |
| U1 aligned command row | `src/frontend/src/pages/tasks/workorders/WorkProcurement.tsx:375` |
| U2 Work/source group | `src/frontend/src/pages/purchasing/PurchaseOrderProcurement.tsx:121` |
| U3 maintenance receive title | `src/frontend/src/forms/PurchaseOrderForms.tsx:982` |
| Work outcome and missing facts | `src/frontend/src/pages/tasks/workorders/execution/WorkBrief.tsx:113` |
| Diagnose qualification and scope | `src/frontend/src/pages/tasks/workorders/execution/DiagnoseProjection.tsx:285` |
| Plan authority facts | `src/frontend/src/pages/tasks/workorders/execution/PreparedPackage.tsx:430` |
| Verify criteria and independent release | `src/frontend/src/pages/tasks/workorders/execution/VerificationReturn.tsx:186` |
| Service/acceptance and follow-up caveat | `src/frontend/src/pages/tasks/workorders/execution/ServiceRestoration.tsx:52` |
| U4 account information and source attribution | `src/frontend/src/pages/tasks/workorders/PreparedCloseout.tsx:205` |
| Closeout missing questions only | `src/frontend/src/pages/tasks/workorders/CloseoutPanel.tsx:637` |
| History empty and independent transitions | `src/frontend/src/pages/tasks/workorders/execution/WorkContinuity.tsx:614` |

## Verification ledger

Evidence root: `data/devscratch/r8-polish/`. Intermediate failures and interrupted runs are retained; only completed checks below count as passing evidence.

| Check | Actual result | Evidence |
| --- | --- | --- |
| Isolated offline backend | 325 passed, zero skips | `backend/manifest.json`, `backend/backend.log`; runner label `tasks.tests.r8_wave2_offline` |
| Fresh PostgreSQL behavioral checks | 200 passed, zero skips | `postgres/check-2.log`, receive benchmark 390/400 |
| Fresh PostgreSQL concurrency | 5 passed | `postgres/check-4.log` |
| Email recipient policy | 23 passed | `postgres/email-policy.log`; recorded-only behavior unchanged |
| Migration graph | No conflicts | `postgres/check-1.log` |
| Migration dry-run | Fails: unchanged backend drift | `postgres/check-0.log`; aichat/users/assets/repair/tasks, already open in merged integration report |
| Frontend units | 174 passed in 25 files | `units-final.log` |
| Types | App and R8/S1/S4/S6/S7/S8 passed; final S1/S4 assertion checks repeated | `tsc-complete.json`, `tsc-last-s1.log`, `tsc-last-s4.log` |
| Lingui | Extract/compile completed; all 40 .po files restored bytewise | `lingui-complete.log`, `lingui-complete.json`, `locale-before.json` |
| R8 fixture suite | 148 passed; 7 intentional duplicate phone-dark audit skips | `browser-r8-complete.log` |
| Wider Work/Closeout suites | 540/542 passed in broad run; both coverage-tooltip navigation failures fixed and replayed successfully | `browser-legacy-delivery.log`, then 18/18 `browser-legacy-release-focused.log` |
| Final affected consumers | 108/114 passed, six S1 stale-copy failures resolved; final S1 replay 28/28 passed | `browser-consumer-final.log`, `browser-s1-complete.log`; includes S0 Work and current S4 investigation/outcome/verification and S6 service facts |
| Native source-module journeys | 54/54 passed on default Vite fixture server | `browser-native-final.log`; both projects, S7 phone companion + S8 review |
| R8 default-server smoke | 3/3 passed on default Vite, no bundle override | `browser-r8-native-smoke.log`; R8 procurement entry plus missing-fact/authority focus on both projects |
| Changed-file prek | Passed after formatter fixes | `final-review.json` records the clean command/file set/log; intermediate modifying runs retained |
| R6 tracking validator | Fails: inherited 58-ticket expectation versus 60 CSV rows | `tracking-baseline.json` proves validator and CSV match HEAD bytewise |

The isolated backend command was `python3 contrib/ui-ux/r6/backend_baseline.py --image r8-int-tests-wave1:local --output data/devscratch/r8-polish/backend tasks.tests.r8_wave2_offline`. PostgreSQL used the checked-in `backend_postgres.py` with `R8_POSTGRES_PREFIX=r8-polish-pg-` and a new UUID-named container/network. Both were removed successfully (`postgres/cleanup.json`). No existing database/container or Azure credentials were used. The backend runner's overall nonzero result is the migration dry-run above; passing behavioral tests do not clear that gate.

The checked-in R8 configuration uses the S-suite fixture entry and defaults, isolated loopback port, no dotenv/proxy, desktop 1440×900 and phone 390×844. `AIMMS_BROWSER_WS` supports this lane's own Linux Chromium; optional `AIMMS_R8_BUNDLE_DIR` serves only prebuilt fixture assets. The bundle blocks backend/media paths and caches compressed assets. Tests which deliberately import source modules run through the default Vite profile. No authenticated app or owner localhost is used.

The initial broad replay had 470 passes and 72 failures: stale prose selectors and two omitted nested fixture HTML entrypoints. Those entrypoints are included in the final fixture build. The next broad replay passed 540 cases; the two phone Verify cases left a coverage tooltip open over the next link. The tests now dismiss it with Escape before navigation, and both cases pass. This is recorded as a broad run plus focused replays, not a fictitious single clean 542-case run.

The native S7/S8 replay also caught a real keyboard-order regression introduced by focusable status badges. Badges are now static, as in the original navigation order, with screen-reader descriptions and hover details. The unchanged Attachments → Talk → Ask → boundary → workflow keyboard assertions pass. Screenshot inspection caught remaining fallback Parts notes and request metadata rendered as “Not recorded”; these now use the missing-value control. A final consumer replay covers those changes. Two older S1 material-subtotal expectations now use the existing native “Allocated” term with the same quantities; missing-context, fit-identity and physical-authority assertions check their actual tooltip/dash replacements. Business intent, exact command/replay checks and no-write assertions remain.

Final reproducible R8 invocation, from `src/frontend`:

```sh
AIMMS_HARNESS_PORT=15275 \
AIMMS_BROWSER_WS=ws://127.0.0.1:3025/r8-polish \
AIMMS_R8_BUNDLE_DIR="$PWD/../../data/devscratch/r8-polish/final-bundle" \
AIMMS_CAPTURE_DIR="$PWD/../../data/devscratch/r8-polish/captures/after" \
node node_modules/@playwright/test/cli.js test --config playwright.r8.config.ts --workers=2
```

Omit the bundle override for the default native Vite server. The fixture bundle is built from the same local source through `vite.s0.config.ts`, with s0-native, r8-procurement, r8-recovery, aichat-drawer and approval-role entries. Native source imports are verified separately; no stub replaces the native user store. Frontend checks use `tsc --noEmit`, the existing Vitest config with `--configLoader runner`, and `PATH=/opt/homebrew/bin:$PATH prek run --files ...`. All 40 `.po` files match the initial byte hashes after extract/compile.

## Captures and inspection

Before bundle source is immutable and all 32 changed production files match HEAD (`capture-source-manifest.json`). The primary matrix is 36 images per phase: seven Work tabs × desktop light, desktop dark, phone light (21); U1 shortage and selected source × three views (6); U2 native PO × three (3); U3 receiving × three (3); U4 material/account region × three (3). The separate Closeout-tab views show the top of the full account; U4 shows its material rows. All captures are explicit synthetic or representative fixtures, never the owner's real showcase.

`captures/before/` and `captures/after/` each contain the full 36-image primary matrix, all inspected. Six before and 73 after supporting PNGs were also inspected: 151 images in total, with the primary comparison count still 72. `capture-manifest.json` records SHA-256, dimensions, phase and primary/supporting classification. Nine final recaptures changed after the last fixes and were inspected again (`review/final-changed/`); unchanged captures were confirmed bytewise against the inspected versions. Exact-size review sheets/indexes are in `review/before/`, `review/after/` and their `-extra` counterparts. The native Vite smoke PNG is byte-identical to its inspected final primary image.

Kept native action-specific custody confirmation context, required source-format labels and commercial controls. Synthetic headers and the recovery contract harness's debug output are fixture instrumentation, not production captions or proof of real showcase behavior.

The review checks hierarchy, aligned commands, missing facts, actual active/error states, dark contrast, phone wrapping, table-local scrolling, and the phone dock. The native fallback parts table retains its labelled horizontal scroll region. No new motion is introduced; screenshots disable animation and reduced-motion behavior is preserved.

## Changed specifications

- `tests/r8/pui_lane_b.spec.ts`
- `tests/r8/pui_polish.spec.ts`
- `tests/r8/pui_procurement_a.spec.ts`
- `tests/r8/pui_procurement_recovery.spec.ts`
- `tests/s1/pui_reference_focus.spec.ts`
- `tests/s1/pui_work_materials.spec.ts`
- `tests/s1/pui_work_panels.spec.ts`
- `tests/s4/pui_work_authority.spec.ts`
- `tests/s4/pui_work_continuity.spec.ts`
- `tests/s4/pui_work_diagnose.spec.ts`
- `tests/s4/pui_work_handled.spec.ts`
- `tests/s4/pui_work_investigation.spec.ts`
- `tests/s4/pui_work_observation.spec.ts`
- `tests/s4/pui_work_outcome.spec.ts`
- `tests/s4/pui_work_package.spec.ts`
- `tests/s4/pui_work_time.spec.ts`
- `tests/s4/pui_work_verify.spec.ts`
- `tests/s6/pui_closeout_reconciliation.spec.ts`
- `tests/s6/pui_closeout_report.spec.ts`
- `tests/s6/pui_closeout_story.spec.ts`
- `tests/s6/pui_routine_work.spec.ts`
- `tests/s6/pui_service_authority.spec.ts`
- `tests/s6/pui_service_restoration.spec.ts`
- `tests/s7/pui_phone_companion.spec.ts`
- `tests/s8/pui_p31_work_authority_dock.spec.ts`
- `tests/s8/pui_p31_work_review.spec.ts`

Supporting config: `playwright.r8.config.ts`, `playwright/r8-server.mjs`, `tsconfig.r8-procurement.json`. New tab audit asserts read-only traffic, no page overflow, absence of the removed visible prose, missing dash and keyboard-accessible authority caveat. The procurement suite also measures command alignment.

## Handoff and remaining gates

Implementation, fixture checks and requested screenshot inspection are complete. No unresolved implementation decision needs an owner answer. Next: human review of U1–U4 and the seven Work tabs, then the coordinator's authenticated local integration/real-showcase checks. No PR or issue was created. No deployment, real stock/commitment mutation, owner Closeout mutation, email send or product acceptance is claimed.

Parent migration and tracking gates remain open independently of this presentation-only lane. The own PostgreSQL container/network and browser `r8-w2polish-browser-3025` were removed successfully; ports 3025 and 15275 are clear (`cleanup.json`). The shared R6 handoff and this lane's backlog/contract evidence point here.
