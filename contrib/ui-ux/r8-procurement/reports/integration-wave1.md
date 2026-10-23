# R8 backend wave 1 integration

23 October 2026; `r8-int`, branch `r8/integration`. Integrates P1 `60cd95cc9`,
P2 `ba2c4593c`, P3 `3cab72af8` on R6 `270b3b74e`. This report is the integration
execution ledger, sprint disposition and contract evidence for U1–U4. Implementation and focused local verification are complete; whole-backend
acceptance remains blocked by the failures below. No deployed or product
acceptance is claimed.

Owner decisions remain binding: **Cedar Creek Pump 08 / WO-003826**, named
in-app approval for each PO, exact recorded PDF/email only, no sending, local
work only. All test records are disposable fixtures. No showcase data, existing
container, existing database, Azure, Cosmos, secrets/env files, Git index or
commit was written. Human review is required before any PR; no issue or PR is
created. The coordinator must stage the resolved merge; `UU` in the unchanged
index is expected even after conflict markers are removed from the source.

## Execution ledger and backlog

| Work | Disposition | Evidence / remaining gate |
| --- | --- | --- |
| Read P1/P2/P3 and contracts/owner decisions | Complete | Lane reports, `contracts/p2.md`, owner decisions and memory runner guidance |
| Work audit conflict | Implemented | One `hidden: true` shape and common scope/role predicate; P3 assertion and shared types updated |
| Cross-lane producer chain | Verified in local API fixtures; broader acceptance open | Source → exact native PO/named execution → retained document → explicit acknowledgment → exact receipt → reservation |
| Migrations | Fresh PostgreSQL application and single leaves verified; global drift gate open | R8 migrations are new tables only; five-app drift also reproduces at R6 baseline |
| Combined offline regressions | Final: 2,212 tests; 3 failures / 22 errors / 6 skips | `data/devscratch/r8-int/backend/` |
| Unsupplemented PostgreSQL regressions | Standard broad run failed; final 269 focused + 5 concurrency passed | Fresh migration graph; no schema supplement |
| Formatting / lint | All applicable changed-file prek hooks and standalone shared types passed | `data/devscratch/r8-int/prek.log` |
| UI, B09, real data, deployment and product acceptance | Open / outside wave 1 | No screen implementation or screenshot acceptance claimed |

## Conflict and unified visibility

`WorkOrderEventSerializer.get_metadata` delegates every `PROC_*` event to
`can_read_procurement_event`. Denial always returns **`{"hidden": true}`**.
The event envelope retains its native event/actor/time identity; hidden metadata
is not an empty successful receipt. P3's former `receipt_visible: false` shape is
superseded. Shared TypeScript includes `HiddenProcurementMetadata`.

All procurement evidence requires native PO view and independent current machine
scope. Receipt facts and allocation events additionally require Work-stock view.
Email/document evidence additionally requires `users.view_email`, matching P2's
native evidence API. Existing Work API visibility and role gates still apply.
The same predicate is used by Materials and generic Work audit; Job Kit receipt
readback now also verifies retained machine/client context, preventing reassignment
from transferring old receipt history. Unauthorized procurement has no `lines`;
unauthorized stock has no receipt IDs/snapshots/outcomes; unauthorized email has
neither an email event nor retained document metadata/context.

New producer events capture `schema_version: r8.1`, `machine_id`,
`machine_client_id`, `kit_line_id`, and exact `purchase_order_id` /
`purchase_order_line_id` where applicable. P3's original compact receipt events
are accepted only through their protected `MaintenanceReceipt` ledger and its
immutable requirement scope. Missing ledger or mismatched Work/machine/client
fails closed. Adaptation is in memory and never rewrites historical events.
No Part name, newest-stock lookup, generic PO membership or current Work scope is
used to guess a receipt association.

## UI contracts

### U1 Work Parts: shared read and source/ack commands

`GET /api/tasks/work-orders/{work_pk}/materials/` retains native Materials fields
and adds typed `procurement` (`MaintenanceProcurementContext`,
`src/frontend/lib/types/Procurement.ts`). Reads perform no save, receive, reserve
or continuation. Feature defaults remain off; enable Work Orders and Job Kits
only through the existing registered settings. Work lifecycle, Kit version and
JobKitLine requirement version are separate tokens.

The context keeps `schema_version`, Work/version/Kit identity,
`purchasing_visible`, `stock_visible`, `approval_policy: named_approver_per_po`,
`email_policy: recorded_only`, `lines[]` and `pending_operations[]`.

Each line retains `kit_line_id`, UUID `key`, Part/base UOM, decimal required
quantity, requirement/configuration versions and fingerprints, native due date,
source deadline/dependent step, `source_selection`, `selection_current`,
`orders[]`, and disjoint reserved/issued/used/outstanding quantity buckets.
Stale selection remains historical; fingerprint currency is not technical fit.

Existing source command:
`POST /api/tasks/work-orders/{work_pk}/procurement/source-selection/`.
Existing acknowledgment command:
`POST /api/tasks/work-orders/{work_pk}/procurement/acknowledgment/`.
P1's strict snake_case request contracts, current scope/roles, retained PDF
references and hashes, provenance, versions, replay and `{code, detail}` conflicts
remain as described in [P1](P1.md). Both return the original event envelope on
HTTP 200 first execution and replay. Neither command purchases or sends email.

### U2 native PO: approval, binding and retained documents

Use the existing approval API (`POST /api/approvals/`, then its
open/confirm-viewed/approve flow) with assigned named person and screen review.
P2's `place_purchase_order` payload and freight-inclusive totals remain in
[contracts/p2.md](../contracts/p2.md). Retain `agent_run_id` and `tool_call_id`
through retries. Native goods/charges/header baselines, locks and durable approval
execution still govern the purchase; a Work evidence event grants no authority.

The existing shortage link command now appends `PROC_PO_LINKED` atomically with
its validated exact native PO-line link. It retains the current source event ID
when the selected offer matches the actual SupplierPart and supplier and the actual successful placement/issue `ApprovalExecution`:
operation key, approval ID, reviewed revision/hash, actor ID/label and original
execution result (including native line/charge IDs, goods-plus-freight total,
currency and no-send facts). A PO without a matching durable execution has
`approval_execution: null`; no approver or exact placement instant is invented.
Repeated linking adds no effect, including after partial/full receipt. The three new ledger models are explicitly
registered as endpoint-protected records in `users.ruleset`, matching existing
Job Kit ledger policy; no generic ledger CRUD authority is introduced.

Each `orders[]` entry retains native ID/reference/status/supplier/SupplierPart,
order and received quantities, packaging, sourced order UOM, base UOM and pack
conversion, goods/charge/complete totals, extra-line IDs, native recorded total,
placement date and target date. Unknown cost/FX stays null; freight counts once.
`binding` is the actual link event; `binding.snapshot.approval_execution` is the
actual named execution above. `placed` remains the native date-only fact with
unknown actor; the binding event's actor is the linker, not an inferred approver.

`POST /api/order/po/{pk}/recorded-evidence/` and its GET/read-by-operation-key
remain P2's native exact-byte API (201 first, 200 replay, 409 changed intent).
Replay rechecks current evidence permissions and preserves the original recorder,
including when another authorized reader recovers the result. Recording appends
`PROC_PO_EMAIL_RECORDED` references only to authorized, matching scoped Work
bindings and exact native goods-line IDs. Work rows are locked before PO rows,
consistent with linking, acknowledgment and receipt. Documents recorded before
linking acquire the same deduplicated reference when the link commits.

`recorded_email` is that Work event envelope. Its snapshot contains exact
`document_evidence_id`, `document_operation_key`, `binding_event_id`, optional
`source_event_id`, PDF/MIME hashes, actual recording `source_at`,
`state: recorded_not_sent` and `supplier_email_sent: false`.
`recorded_document` is the locally reconciled native evidence result without
base64 bytes: pk/order/key, hashes, recording time, exact context,
`attachment_state: verified | corrupt`, recorded-not-sent state. Fetch bytes only
through the native authorized evidence endpoint. No native attachment IDs,
provider acceptance, sent timestamp or delivery are fabricated. Retained bytes
remain unchanged by later PO edits. Email evidence and explicit supplier
acknowledgment remain independent; `acknowledgment_current` checks the captured
commercial baseline.

### U3 receiving and reservation recovery

Use native `POST /api/order/po/{pk}/receive/` with P3's existing added fields:
`maintenance_operation_key` (maximum 128 chars),
`maintenance_requirement_version` (**JobKitLine.version**), actual location and
one native `items[]` entry containing line, decimal order quantity, status and
required captured batch. First execution and exact replay return the original
**HTTP 201 StockItemSerializer[]**, including after auto-completion or stock edits.
Persist the exact request/key/actor before submission; never manufacture a new
key on timeout. Recovery is exact POST replay, never GET mutation.

`GET /api/tasks/work-orders/{work_pk}/job-kit/` retains P3's authorized
`receipts[]`: original request, receipt/event/line/shortage/actor IDs, recording
and continuation time, before/after order quantities, immutable stock snapshots,
reservation state/code and exact allocation IDs. See [P3](P3.md) for full fields,
errors and physical/continuation transaction boundaries.

Materials `lines[].receipts[]` now contains actual receipt event envelopes with
P3's implemented stock shape, replacing P1's proposed producer-only shape:
`stock_item`, decimal `quantity`, `batch`, `location`, `status`, nullable
`status_custom_key`, nullable ISO `expiry_date`. Before/after fields are
`received_before` / `received_after`; canonical IDs accompany P3's original
`po_line` / `kit_line` aliases. Receipt quantities are native base units;
before/after received totals are supplier order units.

`lines[].receipt_allocations[]` contains actual `PROC_RECEIPT_ALLOCATED` event
envelopes with receipt ID, original receive key, exact PO/line/Kit identities,
`allocation_ids` and `stock_ids`. `lines[].receipt_outcomes[]` separately carries
current receipt ID/event/key, `reservation_state: pending | attention | allocated`,
stable `reservation_code` and allocation IDs. This mutable outcome never rewrites
the immutable receipt snapshot. Pending/attention retains physical receipt;
only exact replay retries continuation. Read current custody from native
allocations; allocated does not mean issued, consumed or fitted.

### U4 Closeout

Use the same scoped Materials procurement context alongside existing usage,
custody and native allocations. PO cost, document/acknowledgment, exact receipt
and reservation facts now share actual IDs. Consumed fulfillment remains counted
without re-reserving it or decrementing stock twice. Fulfillment buckets exclude
allocations for a previously selected Part, matching P3’s allocator. No wave-1 change treats
receipt, reservation, issue or consumption as fit/operating return, and no completed
Closeout is rewritten. B09/UI implementation and rendered acceptance remain open.

### Event and operation identities

| Work event | Producer / durable identity |
| --- | --- |
| `PROC_SOURCE_SELECTED` | P1 source command / `(Work, operation_key)` |
| `PROC_PO_LINKED` | Existing validated shortage link / SHA256 of shortage + exact native line, guarded Work command |
| `PROC_PO_EMAIL_RECORDED` | Native recorded evidence/link integration / SHA256 of retained evidence + binding event, guarded Work command |
| `PROC_PO_ACKNOWLEDGED` | P1 explicit acknowledgment / `(Work, operation_key)` |
| `PROC_RECEIPT_RECORDED` | Native maintenance receipt / globally unique receive key plus Work command |
| `PROC_RECEIPT_ALLOCATED` | Post-commit continuation / receipt-specific Work command; original receive key retained in metadata |

All values fit native 40-character event names and reuse one constants registry.
Event keys alone are not unique. Public key domains stay separate: P1 evidence
keys are opaque nonblank strings ≤128; P2 document keys retain its safe-character
validation and global uniqueness; P2 approvals derive SHA256 from run/tool IDs;
P3 receive keys retain global uniqueness. Internal derived keys fit their columns
and never replace the original UI intent key. Actor/payload/scope checks,
transaction rollback and replay-before-mutable-precondition guarantees remain.

## Migrations and verification

R8 adds only `order/0123_purchaseorderdocumentevidence.py` and
`tasks/0050_maintenance_receipts.py`. Dependencies retain order 0122 and tasks
0041. Tasks 0050 also retains its explicit tasks 0030 dependency so replacement of
0031–0041 by `legacy_boundary` leaves only one tasks leaf. No merge migration or
existing migration edit is needed. Both migrations contain CreateModel only:
**no new NOT NULL column is added to an existing table**, so no R8 existing-writer
SQL default is missing. Inherited R6 DB-default safeguards are unchanged.

Fresh pgvector PostgreSQL 16 application of the full migration graph succeeded.
Django reports tasks leaf 0050, order leaf 0123, one leaf per app and
`detect_conflicts() == {}`. The global `makemigrations --check --dry-run` exits 1:
aichat action/ID field drift plus compatibility-checkpoint runtime state missing
in users, assets, repair and tasks. The same five-app proposals reproduce at
untouched R6 `270b3b74e` in a network-none container from `git archive` (read-only
Git). Logs: `data/devscratch/r8-int/baseline-drift/makemigrations.log` and
PostgreSQL check history. R8 model/migration parity tests remain separate from
this failing global gate. No duplicate generated migrations are added over
archived compatibility tables just to suppress this finding.

The first combined offline run completed **2,203 tests: 4 failures, 22 errors,
6 skips** (exit 1, 986.755 seconds). All lane producer flows and the initial merged
end-to-end chain passed. Full logs and manifest are retained. This was an initial
integration snapshot, before the final edge checks and corrections below:

- Corrected my overly strict P2 document-recorder replay check: unchanged P2
  semantics allow another currently authorized evidence reader to recover the
  exact original result, preserving its original recorder. The existing permission
  revoke/replay assertions remain unchanged; a new regression checks attribution.
- Registered the new three ledger models in the endpoint-protected role inventory,
  addressing `RuleSetModelTest.test_model_names` without opening generic CRUD.
- Updated only the Closeout fixture’s client/machine authority to match P3’s
  mandatory independent stock scope. Its completion/release assertions are unchanged.
- Other failures involve inherited replaced-node/model-state assertions, missing
  `tasks.record_operating_authority` permission definitions, four unchanged repair
  executor machine-scope failures and the unchanged IoT checkpoint role assertion.
  They remain open; source inspection is not a passing baseline test run.

The dependency-complete standard PostgreSQL run completed **2,215 tests:
3 failures, 30 errors**, zero skips (176.953 seconds). This includes one now-fixed
integration fixture duplicate RuleSet error. The PO link's PostgreSQL-only
`FOR UPDATE` nullable-outer-join failure from the earlier 2,214-case run is fixed:
lock the native line first, then its actual SupplierPart/Part/supplier through
nonnullable joins, retaining commercial identity locks. The full-chain,
document-before-link and machine-reassignment checks passed in this standard run.

The standard PostgreSQL failures also include archived FK tables blocking
TransactionTestCase TRUNCATE, resulting cross-test row contamination, and backward
migration tests encountering retained tables/replaced nodes. All are retained,
not relabeled as skips or waived acceptance. The complete standard log is
`data/devscratch/r8-int/postgres/history/1792717603338669000/check-2.log`.

A separate fresh migrated database ran the original **five concurrency tests:
5/5 passed, zero skips** (2.367 seconds). `tasks.tests.r8_postgres_concurrency`
uses Django's full `available_apps` fixture scope to enable cascading test-row
cleanup for the inherited retired-table FKs. It preserves every assertion,
actual PostgreSQL transactions/locks and the migrated schema. It refuses other
engines, DB names and hosts. This is an explicit test-fixture cleanup profile,
not proof that ordinary teardown or deployed migration acceptance is clean.
The native stock two, transfer-order serial one, sales allocation one and role
rebuild one all ran. **23/23 AI email recipient-policy pytest cases passed**.

The original cached image lacked pytest and django-test-migrations. A task-owned
`r8-int-tests-wave1:local` derivative installs the exact declared requirements-dev
versions (and pure-Python test dependencies) from downloaded wheels with
`docker build --network none`; no application dependency files or Django version
were changed. Preparation and Dockerfile: `data/devscratch/r8-int/test-image/`.
Tests themselves retain network-none/internal-only isolation. Failed bootstrap,
missing-tool and intermediate behavioral logs remain in history; every created
PostgreSQL container/network was removed successfully.

The final focused PostgreSQL run passed **269/269 tests, zero skips** (33.806
seconds), combining every P1/P2/P3 backend test with the integration checks and
selected touched R6 Materials, Job Kit, allocation/custody, native receiving,
Closeout, approvals/email and user-role regressions. This includes the corrected
email-visibility fixture and the unchanged native role-inventory assertion.
Native receiving benchmark: **390/400 queries**. The final separate concurrency
profile again passed **5/5**, and AI recipient policy **23/23**. Current evidence:
`postgres/check-2.log`, `check-4.log`, `email-policy.log`, `results.json`,
`isolation.json` and `cleanup.json` under `data/devscratch/r8-int/`.
The focused runner's overall exit remains 1 because the global migration check
fails, while its behavioral subprocesses all exit 0. All created PostgreSQL
containers and internal networks have been removed (both cleanup exit codes 0).
These are Django/API fixtures with controlled on_commit callbacks, plus actual
threaded PostgreSQL concurrency tests; no browser/deployment acceptance is inferred.

The final combined offline run completed **2,212 tests: 3 failures, 22 errors,
6 skips** (155.219 seconds), exit 1. **All 102 R8 lane/integration cases passed**:
P1 35, P2 32, P3 30 and five merged integration checks. The entire requested
app suites ran together; this subset count does not turn the full run green.
The six skips are the existing PostgreSQL-only benchmark/concurrency guards;
the relevant five concurrency tests ran and passed separately on PostgreSQL.
Remaining full-run failures: inherited model-state/replaced migration nodes,
missing operating-authority permission definitions, four unchanged repair-executor
machine-scope cases, IoT checkpoint role policy and backward-migration retained-table
collisions. Exact failing identities are in `results-summary.json`, with complete
tracebacks in `backend/backend.log`. The fixed Closeout fixture, native role-model
inventory and P2 replay regressions now pass.

The offline wrapper preserves discovery failures/selected method identities and
installs the guarded schema supplement once. Original and final logs/manifests
are retained in `backend/` and its timestamped `history/`. It remains synthetic
schema verification. No production schema or assertion is changed by the wrapper.

| Final check | Actual result | Boundary |
| --- | --- | --- |
| Offline full requested app suites | 2,212 run; 3 failures, 22 errors, 6 skips | Declared SQLite runtime supplement; 102/102 R8 cases pass |
| PostgreSQL standard full requested suites | 2,215 run; 3 failures, 30 errors | No schema supplement; before final fixture-only correction; full acceptance fails |
| PostgreSQL final three-lane + touched R6 focus | 269/269 pass, zero skips | Actual migrations, exact native API/producer chain; no schema supplement |
| PostgreSQL explicit concurrency cleanup profile | 5/5 pass, zero skips | Original assertions and real locks; full-app cascading fixture cleanup only |
| AI email recipient policy | 23/23 pass | Separate pytest package; no provider calls |
| Global migration drift | Fails; same proposed operations as R6 baseline | Single leaves and fresh application pass; no clean global-state claim |
| Formatting and shared TypeScript | Pass | Repository prek on every changed file; explicit pinned Ruff on new harness/tests; standalone Procurement.ts check |

Reproduction (test image preparation is retained under `test-image/`):

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --image r8-int-tests-wave1:local --output data/devscratch/r8-int/backend tasks.tests.r8_integration_offline
R8_TEST_IMAGE=r8-int-tests-wave1:local python3 contrib/ui-ux/r8-procurement/backend_postgres.py tasks order approvals users aichat.tests.test_email_contracts aichat.tests.test_flag_bridge
PATH=/opt/homebrew/bin:$PATH prek run --files <data/devscratch/r8-int/changed-files.txt entries>
```

The exact 269-case focused labels and each subprocess exit are retained in
`postgres/results.json`. `results-summary.json` records full failure identities;
`source-manifest.json` records final delivered file hashes. Standalone types were
checked using the existing main-checkout TypeScript executable with `--noEmit
--strict --skipLibCheck --target es2020 src/frontend/lib/types/Procurement.ts`.
No full frontend build or screen rendering was performed.

All runs are serial. The SQLite fixture is explicitly supplemented; PostgreSQL
uses actual migrations with no schema supplement. Retain complete logs, not only
terminal tails. The PostgreSQL runner uses a new UUID-named pgvector container,
new internal-only network, generated disposable credentials, clean config,
plugins disabled, locmem email, no env files/host ports and cleanup in `finally`.
The pytest-only AI recipient-policy module runs separately from Django discovery.

## Open items

- Global migration-state drift is an inherited blocker, not a passing check.
- Standard full-suite failures remain open in addition to global migration drift;
  focused passes do not waive them. The complete failures above need coordinator
  follow-up before whole-backend acceptance.
- Human merge/source review; the coordinator stages/commits the resolved file.
- U1–U4 UI/B09 implementation, authenticated screenshots, real spare/supplier/batch
  evidence and deployment/product acceptance remain separate. No Figma or frontend
  screen implementation occurred here; no rendered-screen claim is made.
