# S0 engineering and planning closure review

**18 October 2026 — agent review completed; human approval not supplied.** This review implements the user's request to close permission, engineering, design, planning and validation work. It does not declare deployment, domain, hardware or full S0 acceptance. See `design-review.md` for visual findings and `s0-review.md` for remaining exit decisions.

**19 October superseding decision:** the sole frontend/backend project owner explicitly delegates approval. `owner-approval.md` records O01–O10: bounded P00/P01/P04 artifacts accepted, conservative implementation decisions approved, hardware selection deferred, no external owner wait. P02/full S0 and unfinished S1 integration remain open. Earlier pending-owner statements below describe the historical review; no independent manual audit or deployed/hardware acceptance is asserted.

## Engineering decisions implemented

| Area | Implemented result | Verification / limit |
|---|---|---|
| Legacy work authorization | Canonical customer-first/client scope now filters legacy work lists, overview, cards, parts, schedules, dependencies and HTTP command targets. Proposed owner changes are checked before save. | `tasks.tests.test_legacy_scope_api`: 21 non-superuser session/role/grant tests; existing client, role and tool-scope suites also pass. Feature flags do not disable scope. |
| Repair authorization | A linked packet follows its owning work; only an unlinked packet follows its machine. Lists, details, leaf actions and gate actions use the same boundary. Native work-order roles apply to packet CRUD and action writes. | View-only actors can read authorized packets but cannot generate, transition, confirm/waive/prove/lock out, update or delete them. Existing safety, lifecycle and completion assertions remain. |
| Repair chooser/intake | Customer-authorized work can appear in the machine chooser without granting asset registry access. Foreign/missing intake assets, hidden duplicate links and revoked create-replay owners fail closed. | Missing and inaccessible intake assets now both return 404; authorized duplicate/replay behavior remains covered. Hidden repair work cannot be bypassed with an override. |
| Maintenance resources | Work and machine attachment metadata/list/detail/write queries check exact owner scope; reassignment checks the proposed owner before persistence. | Existing part attachment/upload/thumbnail regressions remain green. This does not prove direct storage URL protection, Azure availability or all generic model policies. |
| Canonical consumer seams | `Closeout.ts` exports the actual existing native types. `workProjection.ts` is consumed by the board/detail, preserving card IDs, owning work IDs, lifecycle and versions. | Seven new pure projection cases; no new wire endpoint, request identity, service-restoration fact or unused adapter. Existing source types remain authoritative. |
| Responsive preservation | Shared PageDetail actions wrap; PanelGroup uses labeled, locally scrollable horizontal navigation on phones and retains the desktop rail. | Native `MachineDetail.tsx` and `Header.tsx` are unmodified. Shell Details width changes from 696px to 390px at a 390px viewport. See design review for remaining readability concerns. |
| Baseline registration | Exact RBAC model-set expectation includes the already registered `assets_assetlocation`. | R11 resolved without weakening the exact-set guard or changing registration policy. |

The normal-user matrix authenticates with `APIClient.login` and stored Group/RuleSet/ClientScopeGrant rows. It is real Django test-session evidence, not a browser login/bootstrap, CSRF, allauth or deployed-session audit. Other existing tests retain their original authentication adapters.

### Compatibility and deliberate boundaries

- Users need a configured canonical resolver and explicit resolvable ownership. A work-order role alone no longer exposes legacy jobs across clients. Unowned legacy work/packets are withheld, not backfilled or assigned by this patch. No production data migration is performed.
- Explicit customer ownership wins over machine tenancy. Revoking a stored client grant affects the next test-session request; superuser role rights do not invent a maintenance boundary.
- Scheduling services remain trusted internal primitives; the changed HTTP surfaces authorize targets before invoking them. The existing tool-scope regressions run, but this is not an audit of every internal/plugin caller.
- The asset registry's existing role-global **generic equipment** visibility policy is unchanged; registered stations/pumps use the existing client boundary. Do not describe this work as a complete asset-policy rewrite. Move/barcode preserve their separate native role/model contracts.
- Safety-template administration, media storage delivery, unrelated generic attachment types and external effect delivery are not redefined. Second-person/waiver authority and completion readiness remain service-owned.
- No credentials, production telemetry, existing database, existing container lifecycle, Cosmos RU/s, Azure write or OT control is changed. Follow the repository security policy and threat model; no AI issue, PR or external security report is filed.

## Validation closure

- Expanded offline backend baseline: **366 passed**, 32 selected modules/classes; runner defaults now reproduce this set. The original scope probe separately passes canonical/legacy parity and revocation assertions.
- Pure units: **44 passed** (7 work projections, 8 supplied trends, 29 location tree). Expanded S0 TypeScript passes.
- Biome 1.9.4: **24 changed JS/TS/JSON files pass**; no dependency, lockfile or lint-rule change. Ruff formatting passes all 26 changed Python files.
- Ruff diagnostics are **not globally clean**: 60 existing D101/D102 documentation findings remain in four legacy workflow test files. Comparison with HEAD by path/code/message/symbol finds **zero introduced diagnostics**. No suppression or test assertion is removed; do not claim a clean whole-repository lint pass.
- Browser outcome and tracking validation are recorded in the final `s0.md` checkpoint and ignored `closure/manifest.json`, not inferred from an in-flight process. CI collection includes the new pure projections; GitHub Actions itself is not executed locally.

Retain failed fixture evidence: the first broader workflow run used unowned fixtures and one invalid module label; those are not passing results. Fixtures now name an authorized client or customer, without a global resolver bypass. The initial long-identity browser test expected a trailing slash the native router does not emit; its failure and corrected exact route remain separate. The original phone overflow regression is also retained.

## Capacity-aware sequence

**Observed execution model:** one implementation stream, one active ticket at a time. This is not an approved staffing or weekly-hours commitment. The earlier 17-week/two-frontend-plus-backend-plus-QA estimate remains only a staffed hypothesis and must not be copied as this stream's delivery date.

1. Review this patch and L01–L07; resolve explicit S0 decisions below. Preserve green baselines while reviewing.
2. Once S0 prerequisites are accepted, implement P03 scoped explorer, P14 native resources, P15 Work list and P17 seven panel homes in dependency order. Reuse canonical providers; do not start a second case engine.
3. Follow with P16 native alternate views and P28 counted Work Attachments. P33 exact assistant integration waits for D19 agreement; discovery may proceed but unsupported scope must remain explicit.
4. For each ticket, split source/contract work, desktop implementation, phone/keyboard states and validation. Re-estimate after its first working vertical slice with actual availability, reviewer response time and unresolved dependencies. No fabricated hours, velocity, percentages or finish dates.
5. Keep S2–S8 and deferred procurement separate. P48/S7 hardware and S8 connected acceptance are not waived by an S0 planning decision.

## Decision package for the user / designated owners

| Decision | Recommended disposition | What remains genuinely external |
|---|---|---|
| Engineering/design reviewer | Human reviews the narrow scope policy/compatibility change and the preservation-compatible shared responsive treatment. | Reviewer name and actual approval; agent self-review is not manual approval. |
| D12/D17 final update | Retain distinct native standalone and packet endpoints until an agreed adapter/consolidation exists. Persist one exact intent/key/version and reconcile committed-but-unconfirmed outcomes; never infer external writeback. | Maintenance/platform owner, final target, packet capture forwarding and approved reconciliation behavior. |
| D11/D13 story/media | Reuse current capture locks/revisions and truthful transcript fallback; show original audio unavailable until a retained-media contract exists. | Capture/media owner, consent/retention, original audio retrieval and unchanged/corrected review semantics. |
| D19 assistant | Extend the existing primary assistant only after exact work/request/evidence identity, authorization/version handshake and saved-result return are agreed. | Existing chat owner and a confirmed versioned contract; no second assistant or invented optional wire fields. |
| D25 glasses | Proposed: defer device selection while explicitly retaining P48/S7 hardware acceptance and full-R6 release risk. | User approval of deferral or actual selected hardware/firmware/phone/SDK/transport/session owner. No answer is assumed. |
| P01 coherent case | Review the prepared selectable-case checklist now; user selects the real showcase later, as R6 permits. | Domain reviewer and later approved records. Pump 02 remains reference-only; no forced success or synthetic plant facts. |

Questions about reviewers/contracts and the hardware/capacity proposal were sent; no reply has been received at this checkpoint. P00/P02 remain In progress; P01/P04 can be reviewed as prepared artifacts, but cannot be marked Done before P00. S1 implementation remains Planned. These are specific acceptance/contract decisions, not a reason to redo the completed technical work.

**Later S1 instruction:** user explicitly says “meanwhile start s1”, then requests continued next-task work. P03/P14/P15/P17/P28 now overlap unfinished S0 reviews; P16 is Ready for review and P33 Blocked on the exact assistant contract. See `s1.md` for current technical evidence and candidates. This changes execution sequencing only: prerequisites, owner contracts, human/domain acceptance and hardware/release gates are not waived. S0 evidence/status/estimates above remain historical to that closure checkpoint.
