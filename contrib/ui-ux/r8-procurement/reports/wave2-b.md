# R8 procurement wave 2 — lane B

23 October 2026 · worktree `r8-w2b` · branch `r8/w2b` · base `1778fb6c2`.
Local implementation and fixture verification; human review and deployment/product
acceptance remain separate. No Git writes, issues, PRs, supplier sends, Azure/env
access, showcase changes or existing-container changes.

## Execution ledger and sprint backlog

| Item | Built | Verification / disposition |
| --- | --- | --- |
| U3 native receiving | Stable exact request/body/key retained before POST, required maintenance batch/status, reload recovery and explicit receipt/reservation readback | 11 lane B browser fixtures pass; explicit native-form lost-response/reload replay and required batch verified |
| B09 custody / reconciliation | Nullable actual issuing actor; pending usage reconciles in the same consume/return transaction without moving stock again; completed material mutations refused | 118 SQLite + 133 PostgreSQL focus + 3 supplemental PostgreSQL scope/fit tests pass |
| U4 existing Closeout | Part/IPN, actual disposition/quantity, batch/stock/use tracking, exact receipt/PO goods-plus-charges totals and independent fitted records | Light/dark desktop + phone captures inspected; 4 retained full-page Closeout regressions pass |
| Native safety boundaries | Materials/receiving GET are read-only; stock/PO/machine scope retained; child completion and frozen reports unchanged | Existing focused completion/permission tests pass; no broader product acceptance |
| Final lint, types, Lingui, browser and cleanup | Complete within fixture scope | App/fixture tsc, 15 units, Lingui, changed-file prek, 19 browser executions and owned-resource cleanup verified |

## Current screens and design

Applied `r6/masterdesignprompt.md` and `r6/designprompt1.md`. Native receiving:
stores user records the exact physical batch/quantity/location/condition and
recovers uncertain intent. Prepared Closeout: maintenance engineer reviews actual
use, native purchasing cost and independent receipt/fit evidence. Reused React,
Mantine tokens, native API forms, TanStack Query and native routes. No applicable
installed raster/skill-authoring/OpenAI skill is needed; no Figma or new UI stack.
Fixtures are explicitly identified in harness/data; no owner facts are invented.

## Contract evidence

Uses `reports/integration-wave1.md`, P1/P2/P3 and `contracts/p2.md` unchanged:
`POST /api/order/po/{pk}/receive/` still returns HTTP 201 `StockItemSerializer[]`.
Maintenance requests carry `maintenance_operation_key` and **JobKitLine.version**
in `maintenance_requirement_version`; exact request/key/actor survive uncertain
responses. GET never continues reservation; only explicit same-body POST replay
retries continuation. Physical receipt and reservation outcome remain independent.

Additive `GET` on that same native receive route supplies
`{maintenance, binding, receipts}`. Unrelated POs return `maintenance:false`, null
binding and empty receipts. Linked POs require Work/machine, purchasing change,
stock add/view and purchasing/Work view permissions. Ambiguous bindings fail
closed. `binding` contains actual Work/reference/Kit-line/PO-line/current line
version. `receipts` reuses P3's authorized Job Kit projection, including exact
request, original actor/time/stock snapshots and pending/attention/allocated
outcome. No new route or mutation-on-read is introduced.

`JobKitAllocation.issued_by` is nullable and protected. Migration
`tasks/0060_jobkitallocation_issued_by.py` depends on 0050 and the swappable user;
old actors remain unknown, never inferred from reserver. Existing writers may
omit this nullable column. Issue captures the actual actor. Consumption/return
bind only existing pending, undisposed usage rows inside the custody transaction;
explicit reconciled dispositions stay human decisions. Refresh can recover the
actual consume tracker/actor without another decrement. Consumption never promotes
fit. Material commands and custody refuse terminal Work.

`CloseoutPartUsageSerializer` retains native `allocation`, `part`, `stock_item`,
`stock_tracking_id`, `resolved_by` and adds live `material` supporting context,
batched once per serialized Work. It reuses Materials boundaries and shared
procurement context; matches **requirement + exact receipt stock + native PO line**,
never Part name/newest stock/generic PO membership. Costs are the **whole native
PO total (goods + charges once)**, not a prorated usage cost, paid amount or
financial reconciliation. Unknown price/currency basis remains null. No PO total
is inferred from stock with no exact maintenance receipt association.

The additive context includes actual Part, protected stock identity, independent
issued actor/time, use-tracking ID, receipt event/actor/time/immutable stock facts,
native PO/line/reference/goods/charges/total/currency, and existing fitted record.
Receipt/PO/stock facts respect independent roles and machine scope. Hidden audit
metadata remains `{"hidden":true}`. Frozen completed reports are unchanged; the
new rows are current supporting evidence, not retroactively frozen history.

## Verification

Evidence root: `data/devscratch/r8-w2b/`. All data is disposable fixture data.

| Check | Actual result | Boundary / evidence |
| --- | --- | --- |
| Isolated backend baseline | **118/118 pass**, 31.617 s | `backend/backend.log`, manifest; declared guarded legacy SQLite runtime supplement; not migration acceptance |
| Fresh PostgreSQL focused suite | **133/133 pass**, zero skips, 35.955 s | `postgres/check-2.log`; real migration graph; native receiving + custody + Materials + completion + RBAC/readout regressions |
| Supplemental PostgreSQL exact joins/scope/fit | **3/3 pass**, 0.937 s | `postgres-scope/check-2.log`; exact wrong-stock exclusion, serializer purchasing/machine redaction and retained explicit fit/retraction/untracked-use behavior |
| PostgreSQL concurrency cleanup profile | **5/5 pass**, zero skips | `postgres/check-4.log`; existing explicit cascading-fixture teardown profile, real PostgreSQL locks; not ordinary full-suite teardown acceptance |
| AI recipient policy | **23/23 pass** | `postgres/email-policy.log`; no provider calls (also passes in supplemental runner) |
| Migration application / graph | New 0060 applies; one leaf per app, conflicts `{}` | Both disposable PostgreSQL runs; no issued-by drift proposed |
| Global migration drift | **Fails**, inherited five-app compatibility/runtime drift | `postgres/check-0.log`, `postgres-scope/check-0.log`; overall runner exit remains 1 despite passing behavioral subprocesses |
| Frontend TypeScript | App + new procurement fixture project **pass** | `tsc.log`, `tsc-fixture.log`; fixture project includes the native receiving harness and lane B specs |
| Frontend units | **15/15 pass** | `vitest.log`; receipt retention/change refusal/storage failure/traceability validation, Materials, Closeout account |
| Lingui | Extract + compile **pass**, all **40 .po files restored bytewise** | `lingui-results.json` and logs; compiled messages remain ignored local output |
| Lane B browser fixtures | **11/11 pass**, zero retries, 28.2 s | `browser.log`; native form batch validation + lost response/exact same key/body replay; completed-PO reload; denied read; attention/original actor; missing cost/fit keyboard reasons; light/dark/phone |
| Fixed viewport recaptures | **4/4 pass**, 14.1 s | `browser-viewport.log`; exact 1440×900 light/dark and 390×844 phone viewport PNGs plus complete page captures |
| Existing mounted Closeout report regressions | **4/4 pass**, 25.7 s | `browser-regression.log`; unchanged S6 spec, both themes at desktop/phone; native shell, report download/read-only sources/final action placement |
| Formatting / lint | Changed-file repository prek **passes**; explicit pinned Ruff test/migration profile **passes** | `prek.log`, `prek-tests.log`; legacy test_closeout_reconcile alone retains inherited D102/RUF001/RUF059 ignores; new tests/migration use the other pinned rules |
| Resource cleanup | Both uniquely named PG containers/networks removed; owned browser stopped; ports 3022/15272 clear | `postgres*/cleanup.json`, `cleanup.json`; no existing containers touched |

The first 118-case run had two assertion mismatches: Decimal string precision
and the now-recorded issue actor. Corrected numeric equality and the expected
actual actor; no behavioral assertion was weakened. Prior baseline logs are in
`backend/history/`. Browser setup initially needed an isolated Vite configuration
because shared node_modules are outside the writable worktree. The scratch
configuration uses the installed packages, local cache, no dotenv/backend proxy,
and the user's assigned browser/harness ports. Initial fixture issues (missing
compiled messages, tree fixture shape, capture paths and tooltip selection) were
corrected. No dependency installation or shared package writes were needed.

### Captures and visual inspection

**19 final PNGs** are in `data/devscratch/r8-w2b/captures/`; every PNG was opened
and inspected. The six principal screens are `receiving-desktop-light.png`,
`receiving-desktop-dark.png`, `receiving-phone.png`, and
`closeout-{desktop-light,desktop-dark,phone}-viewport.png`. Closeout also retains
complete page captures, focused unknown reasons, denied/attention/recovered/native
replay receipts, and four mounted S6 report regression captures. Exact dimensions
and hashes are in `captures-manifest.json`.

The initial phone native table clipped traceability sideways. Maintenance-only
CSS now stacks native cells, preserves quantity/batch/status/location in the
phone viewport, removes decorative subrow arrows there and keeps the desktop
native table. Receipt recovery hides the fresh input fields and shows the actual
saved stock facts; it cannot display an editable new receipt alongside an
uncertain original operation. After an allocated receipt is dismissed, its exact
local intent may be cleared; compare-before-delete protects a newer cross-tab
intent. Native status remains separate from current custody: the readback says
**reservation recorded**, not a claim that consumed/released stock is reserved.

### Reproduction

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --image r8-int-tests-wave1:local --output data/devscratch/r8-w2b/backend tasks.tests.r8_w2b_offline
# Exact PostgreSQL label lists and isolated runner copies are retained in
# postgres/results.json, postgres-scope/results.json and backend_postgres*.py.
cd src/frontend
node node_modules/typescript/bin/tsc --noEmit
node node_modules/typescript/bin/tsc --project tsconfig.r8-procurement.json
node node_modules/vitest/vitest.mjs run src/forms/maintenanceReceiving.test.ts src/pages/tasks/workorders/materials.test.ts src/pages/tasks/workorders/closeoutAccount.test.ts --configLoader runner
# Browser needs a NEW owned r8-w2b-browser-3022 server as described in the
# supplied browser memory; configs and commands are retained in the evidence.
```

`source-manifest.json` hashes the final changed source/test/report files. Lane A
WorkMaterialsPanel/PurchaseOrderDetail and lane C seed/recovery code are untouched.
The backend global drift failure and broader wave-1 full-suite failures remain
open; these focused passes do not waive them. This lane did not build/deploy a
production bundle or verify a real authenticated owner-data flow.

## Owner questions / remaining acceptance

No owner answer blocks lane B. Real spare, supplier, batch and technical/fit
records remain absent for Cedar Creek Pump 08 / WO-003826; fixture records do not
fulfil them. Named in-app PO approval and recorded-only email stay binding.
Mac browser confirmation, authenticated full-chain review with integrated lanes,
real-data validation, deployment and video remain separate coordinator/owner gates.
