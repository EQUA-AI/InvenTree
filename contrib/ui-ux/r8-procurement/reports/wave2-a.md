# R8 wave 2 — lane A

23 October 2026. Base `1778fb6c2`, worktree `r8-w2a`, branch `r8/w2a`.
U1 and U2 are implemented and ready for human review. Local verification is complete.
This report is the lane execution ledger and contract evidence; the lane backlog
is [sprint-backlog-wave2-a.csv](../sprint-backlog-wave2-a.csv).
Deployment, integrated real-data, human design review and product acceptance remain open.

## Execution ledger

| Work | Disposition | Evidence / remaining gate |
| --- | --- | --- |
| Contracts, binding decisions, R6 prompts, memory and package | Read and applied | Wave 1 snake_case contracts supersede the package's proposed DTOs and automatic purchasing/sending |
| U1 mounted Work Materials | Implemented | Six columns, inline source evidence, precise deadline/arrival basis, explicit commands and retained read failures |
| U2 native PO detail | Implemented | Scoped Work/source context, independent placed/document/ack facts, existing named approval review |
| Offline backend regressions | 110/110 passed | Explicit P1 synthetic SQLite schema supplement; no migration acceptance inferred |
| Standard fresh PostgreSQL focus | 77/77 passed; zero skips | New uniquely named container/network; both removed successfully |
| Frontend checks | App/harness types, 18 units and Lingui passed | Final changed-file prek and source-form capture replay recorded below |
| Browser fixtures | 51-case matrix plus 10 supplemental cases passed | Additional 16 settled-form capture replays passed; final responsive source-form replay below |
| Capture inspection | Complete | All 48 final PNGs inspected; dimensions and hashes verified |
| Human / deployed / product acceptance | Open | Local fixtures cannot close these gates |

## Design definition

The master design prompt and Frontend Excellence guidance were applied to both
screens within the native React/TypeScript/Mantine architecture.

| Screen | Primary user / objective | Available facts and constraints |
| --- | --- | --- |
| Work Parts | Maintenance engineer and buyer; see requirement, evidenced source and actual material progress | Authorized Materials procurement and custody; exact IDs/versions; read-only refresh; native fallback; no invented fit/readiness |
| Native PO | Buyer and named approver; review the native commercial order and its independent evidence | Native PO/goods/charges and scoped Work context; existing assigned screen approval; recorded email only; original native panels/routes |

## What was built

`WorkMaterialsPanel.tsx` now mounts `WorkProcurement.tsx` when the authorized
r8.1 context has lines. The existing R6 requirements/cards and native table
fallback remain available for older, disabled or unauthorized procurement reads.
The expanded Material record reuses the existing reservation, custody, usage and
fitted-identity presentation; it does not introduce a second stock workflow.

The compact desktop grid has six columns:

| Column | Actual producer facts |
| --- | --- |
| Requirement | Part/IPN, decimal quantity/base unit, sourced needed-at/dependent step; a date-only Work due date remains date-only |
| Source / offer | Selected quote, delivered total or dash, retained reason, arrival with quote/estimate basis, original technical/deadline/offer PDF references, provenance and stale selection |
| Purchase order | Exact native PO link/reference/status, complete goods-plus-charges total, native placement date and independently retained named approval execution |
| Document / acknowledgment | Recorded PDF/email with **not sent**, exact retained downloads, independent supplier reference/accepted order quantity/promise/source/recorder, stale acknowledgment warning |
| Receipt | Exact protected receipt event, actual stock IDs/base quantities, batch/location and recorder/time |
| Reservation | Disjoint reserved/issued/used/outstanding buckets, current native stock custody, retained pending/attention continuation outcomes |

Phone layouts stack the same six fields. Missing facts use the existing
keyboard/hover/touch `MissingValue` tooltip. Active warnings use native status
styling and Mantine tokens. No receipt, allocation or used quantity becomes an
inferred fitted item or operating return. The redundant stock summary caption
was removed from this procurement view after screenshot review, because it could
imply use when material was merely reserved.

Source recording supports explicit original technical/quote/deadline PDFs,
multiple offers, SupplierPart/order-unit conversion, complete or unknown costs,
source times, provenance and a deliberate selection/supersession reason.
The original bytes are hashed before submission. Original evidence links also
verify the retained SHA-256 before opening; replaced/unavailable bytes produce a
short visible failure. Named PO preparation is offered only for a current,
supported, valid quote with complete costs and a sourced on-time supplier promise.
A nonzero absolute quote discount must be prepared in native purchasing, because
native PO discounts are percentages; the UI never copies one into the other.
Fixture placement descriptions retain the fixture namespace in their data.

The explicit actions cover source recording, named approval, exact placed-line
linking, document recording, acknowledgment and navigation to native receiving.
Receiving forms and receipt continuation belong to lane B and were not edited.
Materials GET/refresh performs no save, purchase, receive, reserve or continuation.

`PurchaseOrderDetail.tsx` keeps its native header, status, commercial details,
goods, additional charges, received stock, parameters, attachments and notes.
The added `PurchaseOrderProcurement.tsx` block shows an actual scoped Work reference
and machine link, source, placement, recorded email and acknowledgment separately.
The native Issue action now requests an assigned person's approval instead of
issuing directly. Approval review reuses `ApprovalInboxPanel` with an optional
initial approval ID; its existing open → confirm-viewed → screen approve flow,
revision/hash and required-section controls remain authoritative. Another assignee
is displayed as awaiting the assigned reviewer. A successful placement can link
only the exact native line from its execution result. Already placed native lines
can also be explicitly selected through the existing validated shortage-link API.

PO navigation from Work carries `?workOrder=<id>`, including receiving navigation.
Native PanelGroup navigation preserves that query, and Work → PO → Work is tested.
The block reads the same authorized Materials context and matches exact PO IDs;
it never guesses an association from Part name, supplier or the newest stock/order.
Direct PO entry has no reverse Work lookup in wave 1: it still shows native
placement and authorized recorded documents, while acknowledgment is a dash with
an accessible explanation to open the exact Work-linked context.

Transient Materials failures preserve the last successful response for that Work,
with a visible last-read warning/time and retry. New recording controls pause
while reads are stale. Current 401/403/404 denials remove the procurement facts;
404 retains the legacy Parts fallback. The PO's Work identity, Materials and
document read failures also remain visible. Role gates and server machine scope
remain authoritative; generic audit `{"hidden": true}` metadata is never used as
a successful procurement fact.

## Contracts and operation identity

Source contracts: [integration-wave1.md](integration-wave1.md), [P1](P1.md),
[P2](P2.md), [P3](P3.md), [contracts/p2.md](../contracts/p2.md), and
`src/frontend/lib/types/Procurement.ts`.

| Operation | Existing endpoint / contract used |
| --- | --- |
| Materials | `GET /api/tasks/work-orders/{id}/materials/`, additive typed `procurement` |
| Source | `POST .../procurement/source-selection/`; strict snake_case; Work lifecycle version, separate requirement version/configuration token, exact evidence and supersession |
| Acknowledgment | `POST .../procurement/acknowledgment/`; actual PO line, accepted supplier-order quantity, original source and explicit promise/deviations |
| Named approval | `POST /api/approvals/`; assigned user, stable run/tool/checkpoint IDs, `place_purchase_order` or native `issue_purchase_order`; existing screen review |
| Exact linkage | `POST .../job-kit/shortages/{id}/link-po/`; original shortage plus actual native PO-line identity |
| Documents | `POST /api/order/po/{id}/recorded-evidence/`; recorded-only provenance, explicit buyer/recipient/delivery facts, no sender |
| Exact downloads | Authorized `GET recorded-evidence/?operation_key=...`; PDF/MIME hashes and attachment state checked before download |
| Receiving | Link to native PO line-items; no new receiving implementation in lane A |

`useProcurementCommand.ts` adapts the persist-before-POST `useRecordedCommand`
pattern to these actual event/document/approval responses. It retains the exact
endpoint, body and stable identity by host, actor and resource before submission,
blocks replacement while uncertain, and replays the original POST after reload.
It does not send the unrelated record-command reconcile-only header or expect a
`version` response envelope. A refusal of an already uncertain retry does not
prove the original attempt failed and does not discard the retained intent.
No new key is manufactured on timeout. The PO PDF/email downloads never imply
sent, delivered, paid, supplier-confirmed or received.

## Actual verification

All evidence is local under `data/devscratch/r8-w2a/`. The browser uses only
explicitly representative fixture records, with a normal non-superuser buyer or
reader. These are native component/router and mocked API checks, not an
authenticated deployed purchasing flow.

| Check | Actual result | Evidence |
| --- | --- | --- |
| Prescribed isolated backend runner | 110/110 passed | `backend/backend.log`, `backend/manifest.json` |
| Fresh standard PostgreSQL | 77/77 passed, zero skips | `postgres/check-0.log`, `results.json`, `isolation.json`, `cleanup.json` |
| App TypeScript | Passed | `types.log` |
| Browser/harness TypeScript | Passed | `types-harness.log`, scratch `tsconfig.harness.json` |
| Changed procurement and retained Materials units | 18/18 passed | `unit.log` |
| Lingui extract + TypeScript compile | Passed; all 40 `.po` files restored bytewise | `lingui.log`, `locale-restore.json` |
| Full lane browser matrix | 51/51 passed, zero retries | `browser-verified.log` |
| Supplemental phone continuations/native draft/identity failure | 10/10 passed | `browser-supplement.log` |
| Settled form screenshots | 16/16 passed | `browser-forms-settled.log` |
| Final responsive source-form replay | 5/5 passed; final four captures inspected | `browser-source-final.log` |
| Repository-pinned formatting/lint and whitespace | All applicable hooks passed on 25 files; whitespace clean | `prek-final.log`, `git diff --check` |

Backend invocation:

```sh
python3 contrib/ui-ux/r6/backend_baseline.py \
  --output data/devscratch/r8-w2a/backend \
  tasks.tests.r8_p1_fixture approvals.test_procurement_r8 order.test_procurement_evidence
```

The P1 loader explicitly supplies missing runtime schema only in the runner's
private SQLite fixture. PostgreSQL used the ordinary migrated schema, with no
supplement, for `tasks.tests.test_procurement`, `tasks.tests.test_work_materials`,
`approvals.test_procurement_r8` and `order.test_procurement_evidence`. The scratch
`run-postgres.py` is the existing isolated integration runner narrowed to those
labels and renamed to this lane. Container `r8-w2a-pg-11ee4f29b9` and its internal
network were removed; both cleanup exits are zero. App image:
`sha256:89981388aff5f8329596f78ec9ce1bfb141ff9369e52fc7f9ae19305440fa96e`.
No existing containers or databases were used or restarted. The inherited whole
backend/migration acceptance failures remain as recorded in wave 1.

Browser invocation:

```sh
AIMMS_HARNESS_PORT=15271 node src/frontend/node_modules/playwright/cli.js test \
  --config data/devscratch/r8-w2a/browser.config.mts
```

Owned browser: `r8-w2a-browser-3021`, websocket `/r8-w2a`, Chromium in the cached
browserless image. Harness port 15271 has no backend proxy or dotenv loading.
Because `node_modules` is a read-only symlink outside this sandbox, Vite starts
from a scratch in-memory configuration and Vitest uses `--configLoader runner`;
no shared dependency files were changed or installed. Linux Chromium verification
is not Mac host acceptance.

The fixture cases cover source/ack/document exact retry, timeout and reload,
refusal after uncertainty, assigned named review with required sections and exact
line linkage, native draft approval rather than direct issue, read-only refresh,
legacy fallback, source currency, keyboard missing-fact tooltips, original source
hash mismatch, independent documents/acknowledgment, received-but-unreserved stock,
consumed fulfillment, navigation and phone containment. The source-form byte test
explicitly verifies hash transport using fixture bytes; native backend PDF parsing
and exact rendered document/MIME behavior are covered by the backend tests.

The 48 final PNGs are exact 1440×900 desktop and 390×844 phone captures, in light
and dark. They include five Work states, native PO, four command forms and eight
scrolled phone custody/communication views. `capture-review.json` records the
inspected files, dimensions and hashes. Screenshot inspection corrected the stock
caption, warning treatment, narrow phone offer fields and capture timing during
modal transitions. Original failures are retained in earlier scratch logs; none
is relabeled as a passing run. The corrected form captures wait for opacity 1.

## Owner inputs and handoff

No owner decision blocks this local U1/U2 implementation. The real Cedar Creek
Pump 08 / WO-003826 still needs actual spare/SupplierPart/fit/quote/deadline facts,
receiving/buyer details and named target-environment grants before procurement can
advance truthfully. Nothing here seeds or overwrites its stock, commitments or
completed Closeout. Fixture values remain explicitly marked as fixture data.

Coordinator: review this lane with U3/U4 and lane C, then perform the real scoped,
authenticated flow and Mac host review. Direct-entry reverse Work discovery is an
API extension beyond wave 1; Work-linked PO navigation is implemented now.
Target deployment, activation/grants, real-data acceptance and recording remain
separate. No supplier email, Azure access, Git write, issue or PR was performed.
Receiving forms, Closeout and seed/recovery code were not edited.

Final cleanup: `r8-w2a-browser-3021` stopped and removed; harness 15271 and
browser port 3021 are clear. `cleanup.json` and PostgreSQL `cleanup.json` retain
actual exit codes. `delivery-manifest.json` records final source and evidence
hashes. All 48 final capture files were opened for visual inspection, including
the four recaptured responsive source forms.

Summary: U1/U2 are ready for human review. 110 offline backend, 77 standard
PostgreSQL, 18 unit and 61 unique browser fixture cases passed, with focused
settled/responsive capture replays. App/harness types and Lingui pass; no
real-data, Mac host, deployment or product acceptance is inferred.
