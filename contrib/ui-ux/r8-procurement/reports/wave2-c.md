# Wave 2 C — recovery, fixture and backend acceptance

23 October 2026; worktree `r8-w2c`, based on `r8/integration` at `1778fb6c2`.
This is Lane C's execution ledger, sprint backlog and contract evidence. R6
parent acceptance is unchanged. Lane C source and focused isolated verification are complete. No deployment,
showcase seed or product acceptance is claimed.

## Execution ledger / sprint backlog

| Work | State | Evidence / remaining gate |
| --- | --- | --- |
| Read contracts, owner decisions, R6 handoff/design/memory and R8 package | Complete | Wave 1 integration, P1/P2/P3, P2 contract; proposed reference index inspected |
| B10 native browser recovery | Implemented and fixture-verified | Shared retained-operation hook plus receipt-array, named approval, recorded document and allocation-use adapters |
| Deterministic local fixture command | Implemented and verified | `seed_r8_procurement`; existing named person; dry-run/remove; dedicated DB allowlist; separate tagged fixture Work |
| Whole chain through use and Closeout cost | Verified in PostgreSQL fixtures | Exact source/approval/document/ack/receipt/reservation/use/Closeout identities; native 620 + 45 = 665 purchasing cost |
| Original offline profile | Blocked by inherited SQLite checkpoint | Missing `tasks_workorder.recurrence_of_id`; complete log retained |
| Fresh PostgreSQL + selected R6 regressions | 191/191 passed; global drift gate fails | Real migration graph, no schema supplement; all owned containers/networks removed |
| Frontend type/Lingui/unit/browser fixtures | Passed; captures inspected | 15 browser cases / 15 captures; own harness 15273 and browserless 3023 removed; no lane A/B UI files edited |
| Native screen integration, human review and owner seed decision | Open | Utilities ready for native form consumers; no PR/issue, Git writes, owner/shared DB or Azure access |

## Implementation and contracts

`reports/integration-wave1.md` governs IDs, fields, versions, visibility and
`{"hidden": true}`. Materials reads remain read-only. Source, native approval,
recorded document, receipt and use identities remain independent. Email is exact
recorded evidence only; no send path is added or invoked.

### Recovery utilities for lanes A/B

`src/frontend/src/components/records/useProcurementRecovery.ts` exports:

- `useMaintenanceReceiptRecovery({workOrderId, purchaseOrderId, onSaved})`:
  submit the complete native receive body including the stable
  `maintenance_operation_key`, **JobKitLine.version**, actual location and
  one line's batch/status/quantity. Success is the actual HTTP 201
  `StockItemSerializer[]`; it has no Work version envelope. First submit and
  recovery both use native exact POST replay, without a reconcile-only header.
- `useRecordedDocumentRecovery(...)`: exact native recorded-evidence POST replay;
  response must match the PO and original document operation key, remain
  `recorded_not_sent`, `supplier_email_sent=false`, with verified attachment.
  Retained hashes/bytes and original recorder remain native server facts.
- `usePurchaseApprovalRecovery({workOrderId, approvalId, onSaved})`: replay the
  exact named in-app approval decision with retained review revision/hash.
  It does not open/confirm review, create a new approval or bypass authority.
  Callback receives the actual terminal status, including failure; a failed
  approval is never a placed PO. Preparation/creation retains wave 1's native
  `agent_run_id`/`tool_call_id` replay contract.
- `useAllocationUseRecovery({workOrderId, allocationId, onSaved})`: first use
  invokes the native consume command. Recovery sends the same allocation and
  body with `X-AIMMS-Reconcile-Only: 1`, which can only retrieve an already
  consumed allocation with native stock-tracking evidence. No uncommitted use
  can be applied by reconciliation. Ordinary duplicate consume remains refused.

All adapters retain a normalized immutable JSON transport snapshot **before**
submission. Optional undefined fields and Date serialization follow actual JSON
transport. Storage is partitioned by host, authenticated person, command family
and entity identity. Scope changes leave the original intent in its original
namespace; late results, including A-to-B-to-A navigation, cannot update a new
screen or clear its operation. Duplicate clicks are suppressed, malformed storage
blocks writes, and uncertain/denied/malformed responses keep the original intent.
Retries never mint another key. Cross-tab changes refresh the native source.
Callbacks receive actual results; read refresh failure cannot undo a confirmed
write. Refresh keys include actual Materials/Work/Closeout, native instance/table
data and approval inbox/review/count query families.

These are shared utilities and a browser fixture. Lane C intentionally does not
edit the receiving form, Parts, PO or Closeout UI owned by lanes A/B. Their mounted
consumers must import the adapters (or the generic hook for additional native
contracts) and retain their existing modal/query/focus conventions. Fixture
screenshots are **not** native U1–U4 or authenticated deployment acceptance.

### Native use reconciliation and scope

The existing `POST /api/tasks/work-orders/{work}/job-kit/allocations/{id}/consume/`
accepts `X-AIMMS-Reconcile-Only: 1` only as an explicit readback request. It locks
and authorizes the exact allocation and returns its native serializer only when
already consumed and backed by `stock_tracking_id`; otherwise HTTP 409, with no
stock mutation. Normal consume semantics, including duplicate refusal, remain.

All custody commands now recheck independent current machine scope. Receipt-linked
allocations additionally verify the exact protected receipt's retained Work,
machine and client context, so reassigning shared Work cannot transfer old receipt
custody. Generic allocations without receipts retain native behavior. No migration,
new public receipt shape, background mutation, GET continuation or synthetic use
tracking is added.

### Local seed command

`src/backend/tasks/management/commands/seed_r8_procurement.py` prepares:

- New **R8 FIXTURE** suppliers A/B, a marked representative seal/service Part,
  SupplierParts and native price breaks; representative goods/freight totals
  are 620/45/665 and 560/20/580 USD, explicitly fixture provenance. Fit and
  delivery promise remain unknown. No real manufacturer/SKU/technical fit is invented.
- Separate Work `R8-FIXTURE-003826`, with the exact P1 `r8-fixture:<namespace>` tag and an empty Job Kit bound explicitly
  to the read-only target `WO-003826` / **Cedar Creek Pump 08**. The real target's
  stock, kit, commitments, lifecycle and completed Closeout are never edited.
- A new fixture reviewer group assigned to an **existing active person with a
  full name**, using the native purchase-order role and
  `work_order.can_review_approvals` field. Native rebuilding owns permission
  projection. No user/password/superuser, service authority or approval is created.
- A retained manifest of owned record IDs, namespace, target and approver
  in fixture metadata. Rerunning reuses those exact identities; unmarked collisions
  or a different actor/target fail closed. No existing record is adopted or reset.

P1 explicitly refuses recorded-fixture provenance on WO-003826 even if tagged;
the scenario-linked companion preserves that existing boundary. No seed option
bypasses it.

The command creates no PO, stock, reservation, source/fit assertion, document,
email, acknowledgment, physical receipt, maintenance record or Closeout.
`--remove` removes only untouched owned preparation records and the fixture role;
stock, POs, allocations, events, progression, attachments, additional group members
or changed dependencies cause refusal. Unanticipated protected references roll
back removal. Repeated remove returns absent. `--dry-run` writes nothing for either
creation or removal.

Allowed databases are PostgreSQL `r8_demo`, `r8_integration` or
`test_r8_integration` on explicit loopback/test hosts with DEBUG enabled and
`--confirm-db` exactly matching. The dedicated baseline runner's test-only
`/evidence/test.sqlite3` is the only SQLite exception. The owner's
`aimms_azure_copy`, other shared names and remote hosts are refused. No env file
is read. The command has only been invoked by isolated test cases.

Example for an owner-reviewed **dedicated** local R8 database, not an instruction
or authorization to seed the owner's copy:

```sh
python manage.py seed_r8_procurement --confirm-db r8_demo --approver '<existing-person>' --dry-run
python manage.py seed_r8_procurement --confirm-db r8_demo --approver '<existing-person>'
python manage.py seed_r8_procurement --confirm-db r8_demo --approver '<existing-person>' --remove --dry-run
python manage.py seed_r8_procurement --confirm-db r8_demo --approver '<existing-person>' --remove
```

## Verification evidence

All artifacts below are under `data/devscratch/r8-w2c/`. No skipped cases in the
focused behavioral profiles. A passing subset does not waive wave 1's inherited
broad-suite failures or the global migration-state gate.

| Check | Result | Evidence / boundary |
| --- | --- | --- |
| Original SQLite runner | Failed before behavioral assertions: inherited missing `tasks_workorder.recurrence_of_id` | `backend/backend.log`; no unsupplemented SQLite acceptance |
| Explicit SQLite runtime fixture | **191/191 passed**, 46.522 s | `backend-supplemented/backend.log`, `manifest.json`; same assertions with guarded R6 schema supplement, not a migration repair |
| Fresh PostgreSQL selected chain + R6 | **191/191 passed**, 44.452 s, zero skips | `postgres/check-2.log`; real migrations, no schema supplement; includes source/ack, native named purchasing/documents, receipts, Materials, reservation/custody/reconciliation and final Closeout |
| PostgreSQL concurrency | **5/5 passed**, 2.381 s | `postgres/check-4.log`; original native stock/transfer/sales/role cases, explicit private full-app cascading cleanup profile for inherited retired-table FKs |
| Final seed/provenance amendment on another fresh PostgreSQL | **5/5 passed**, 0.424 s | `postgres-fixture-final/check-2.log`; exact P1 tag/provenance, real command DB guard, create/dry-run/replay/remove, no-send, existing stock/kit/Work/completed Closeout preservation |
| Email recipient policy | **23/23 passed** | Both PostgreSQL profiles' `email-policy.log`; no provider account contacted |
| Migration graph | Fresh migration application and leaf/conflict check pass; **global state drift fails** | Both PostgreSQL profiles' `check-0.log` and `check-1.log`; same five inherited apps as wave 1; no generated migration added |
| TypeScript | Passed | `tsc-final.log`, exit 0 |
| Lingui extract + compile | Passed; **40 .po files restored bytewise** | `lingui.log`, `lingui-restoration.json`; generated catalogs remain local |
| Changed pure module unit tests | **5/5 passed** | `vitest-final.log`; JSON transport snapshot, stock-array response, malformed/cross-family/scope storage and exact tracked use |
| Playwright recovery matrix | **15/15 passed**, 42.0 s | `playwright-final.log`; native operation hooks with intercepted fixtures only; reload/exact body/key, refused access, scope change, stale response, malformed response/storage, use read-only header |
| Capture inspection | **15 PNGs inspected** | `captures/`, `capture-manifest.json`; 1440×900 light/dark and 390×844 phone; no overflow; original provider-icon fixture toast removed before final captures |
| Formatting/lint | Passed | `prek-final.log`; explicit repository-pinned Ruff v0.15.12 also checks tests excluded by normal config (`prek-tests-final.log`); `git diff --check` passes |
| Cleanup | All task-owned resources removed | Both PostgreSQL profiles' `cleanup.json` have container/network exit 0; `browser-cleanup.json` confirms browser absent |

Both PostgreSQL runner exits are **1 solely because the global migration-state
check fails**; behavioral, graph, concurrency and email-policy subprocesses pass.
The full 191-case PostgreSQL run preceded the final fixture-tag amendment; the
additional five-case PostgreSQL run verifies that amendment. Final SQLite 191
also includes it. Functional command tests keep the actual DB guard in these
private profiles; standard CI can mock only the local-command configuration guard
while the explicit refusal cases test it independently. No production guard is
relaxed for CI.

The PostgreSQL runner now supports an output directory/prefix through
`R8_POSTGRES_OUTPUT` / `R8_POSTGRES_PREFIX` so lanes do not overwrite each other's
evidence. `R8_POSTGRES_FOCUSED_ONLY=1` runs migration/graph/selected tests without
repeating the already-passed concurrency profile; it is used only for the final
fixture-only amendment. All tests remain serial. Each runner creates a new UUID
pgvector container/internal-only network, temporary config and generated credentials,
with no env files, external network or existing database/container lifecycle access.

Reproduction:

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --image r8-int-tests-wave1:local --output data/devscratch/r8-w2c/backend-supplemented tasks.tests.r8_wave2_offline
R8_TEST_IMAGE=r8-int-tests-wave1:local R8_POSTGRES_OUTPUT=data/devscratch/r8-w2c/postgres R8_POSTGRES_PREFIX=r8-w2c-pg- python3 contrib/ui-ux/r8-procurement/backend_postgres.py tasks.tests.test_r8_fixture tasks.tests.test_r8_acceptance tasks.tests.test_procurement_integration tasks.tests.test_maintenance_receipts tasks.tests.test_procurement approvals.test_procurement_r8 order.test_procurement_evidence tasks.tests.test_work_materials tasks.tests.test_job_kit_reservation tasks.tests.test_job_kit_custody tasks.tests.test_job_kit_reconcile tasks.tests.test_closeout_reconcile tasks.tests.test_workorder_closeout order.test_api.PurchaseOrderReceiveTest
R8_TEST_IMAGE=r8-int-tests-wave1:local R8_POSTGRES_OUTPUT=data/devscratch/r8-w2c/postgres-fixture-final R8_POSTGRES_PREFIX=r8-w2c-pg- R8_POSTGRES_FOCUSED_ONLY=1 python3 contrib/ui-ux/r8-procurement/backend_postgres.py tasks.tests.test_r8_fixture
```

Browser configuration and the sandbox-safe Vite launcher are
`data/devscratch/r8-w2c/{browser.config.mts,serve.mjs}`, modeled on the supplied
S4 config. The launcher uses Vite's runner loader and inline configuration because
`node_modules` is a read-only shared symlink and vanilla-extract's default loader
tries to write `.vite-temp` there. Vitest likewise uses `--configLoader runner`.
Original startup failures are retained. The fixture server disables backend proxy
and dotenv loading. No localhost owner app/bundle is changed.

Captures cover receipt pending/saved, recorded document, approval and tracked use
hook fixtures. The raw status text in the latter fixture is intentional contract
evidence, not a new product surface. Native U1–U4 capture/acceptance remains with
lanes A/B. No generated image or visual reference is presented as a deployed UI.

Failed attempts remain in `postgres/history/` and their original logs. They caught
and corrected the native double-consume regression, two initial fixture assertions,
native role rebuild removing a direct permission grant, and native Part deletion
requiring deactivation. Ordinary double-consume assertions remain unchanged.
Removal now deactivates only the verified owned fixture Part in its atomic removal
transaction. The final provenance amendment uses P1's real validation helper and
continues to reject WO-003826.

## Owner / integration questions and remaining gates

The owner decides whether to prepare a dedicated local R8 fixture database and
which existing named person should review POs. The real showcase's spare/supplier,
fit source, delivery promise, receiving street and physical batch still require
actual authorized evidence. The fixture companion is intentionally separate and
does not populate or reopen the real completed showcase.

Native lane A/B integration, host macOS browser verification, authenticated real
login/storage, human review, global migration/broad-suite inherited failures,
showcase seed approval, deployment and video/product acceptance remain separate.
No Figma, Azure, Cosmos, supplier contact, owner/shared database, existing service
container, Git index/commit, PR or issue was changed.
