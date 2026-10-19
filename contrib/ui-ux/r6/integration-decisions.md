# S0 completion, capture and assistant decision brief

**Status:** source-backed owner review with isolated backend test evidence; no owner approval, new API, deployed acceptance or external writeback proof.
**Base:** `fe0d73a4a`, 18 October 2026. Cross-reference D11–D13, D17, D19 and D25 in `contracts.csv`.

**19 October approval update:** the user confirms sole frontend/backend ownership and delegates approval/design authority. O04–O09 in owner-approval.md select the conservative internal-authority, separate-endpoint, exact-intent/reconciliation, truthful media and single-assistant principles and explicitly defer hardware selection. Owner questions below are historical requirements now assigned to this stream, not a missing external team. Precise schema/provider implementation and runtime acceptance remain unfinished; no new API or wire support is claimed by approval.

## D12: retain the actual completion authority

| Contract | Standalone work | Packet-owned work |
|---|---|---|
| UI owner | `CloseoutPanel.tsx` | `PacketCloseoutModal.tsx` |
| HTTP target | `/api/tasks/work-orders/:id/complete/` | `/api/repair/packets/:id/close/` |
| Intent body | Flat closeout fields, required `expected_version`, required `idempotency_key`, optional `capture_id` | Nested `closeout`, required integer `expected_version`; API supplies a random key if caller omits it |
| Domain path | `tasks.services.closeout.complete_work_order` | `repair.services.close_repair_packet`, then the same completion service with an internal packet-finalization capability |
| Response | Canonical `CommandResult` | Packet serialization plus `ok`, work-order ID and lifecycle status |
| Capture consumption | Reviewed capture is locked, checked against its exact decisions, consumed atomically | Current packet adapter/service does not forward `capture_id`; parity remains unresolved |
| Existing UI key recovery | A new key is made per invocation | Modal omits the key |

The shared service writes internal structured closeout and maintenance history and may schedule notification/learning effects. That is not evidence of a successful external maintenance-system update or physical return to service. Packet close also evaluates packet return criteria before reaching the command ledger. Preserve the distinction between domain state and later R6 service/authority displays.

**Owner decisions:** confirm the actual final maintenance-record system and acknowledgement; agree the packet capture path and receipt needed by UI20; agree whether packet errors/results need a narrow additive contract. No packet-owned call is rerouted through standalone completion in S0.

The synthetic packet rejection fixture exercises nested fields plus version and retained draft text. It deliberately does not claim durable replay, capture consumption, physical acceptance or external writeback.

## D17: exact replay and uncertain outcome

Source evidence: `tasks/services/work_orders.py` hashes command, actor and canonical payload; the ledger keys by work order/key and rejects a changed hash. `complete_work_order` checks replay before requiring a fresh version. The HTTP canonical adapter first resolves authorized work. The packet wrapper performs its own status/return checks before replay, so packet retry behavior needs its own evidence.

Proposed controller responsibilities, pending platform/UI20 agreement:

1. Retain the endpoint/record owner, actor/session scope, exact submitted body/version and one logical operation key before sending.
2. Reuse that same intent for double click, refresh and safe retry. A new version/body with the previous key is a conflict; minting a new key after an ambiguous response can create a second logical command.
3. Distinguish pending, confirmed refusal, confirmed completion and outcome unknown. Timeout is not proof of failure. Preserve the report/draft and show the actual state.
4. Reconcile using authorized durable results before permitting a new operation. No dedicated lookup-by-key API has been verified; do not invent one or infer success from a toast/lifecycle badge alone.
5. Coordinate two tabs and invalidate pending work on account/scope changes. Agree the permitted local persistence, retention and recovery policy before storing operational drafts in arbitrary settings.

### Executed isolated evidence and remaining boundaries

`backend_baseline.py` uses the cached image offline and a task-owned SQLite test database. The original 69 selected tests pass. Historical expanded run:150/151 with stale RBAC expectation R11. The closure pass fixes that exact expectation and targeted legacy scope; the final expanded 32-module/class baseline passes 366/366. Earlier failure evidence remains retained. All ten new tests in `tasks.tests.test_s0_capability_contracts` pass independently.

| Contract case | Actual evidence | Still unverified |
|---|---|---|
| Standalone HTTP replay | Same original key/body/version returns the same response; one closeout. Changed intent with the same key returns 409 IDEMPOTENCY_CONFLICT. Ignoring the first receipt does not require a fresh operation. | Actual network loss after commit, browser refresh/two tabs, durable UI recovery and actor-scoped draft policy. |
| Packet HTTP replay | Same nested intent replays with one closeout/packet event. Changed intent with the same key returns the adapter's existing HTTP 400 IDEMPOTENCY_CONFLICT. | UI currently omits the key; additive receipt/reconciliation policy remains an owner decision. |
| Packet outer safety check | After a successful close, a newly active LOTO point makes exact replay return 400 REPAIR_CLOSEOUT_INVALID while the original work remains completed with one closeout. | An authorized durable reconciliation path for this uncertain UI outcome; a refusal must not be displayed as proof the original command never committed. |
| Authority/version/capability | Missing capture/complete authority returns 403 without new records; scope revoked after completion returns 404 on exact standalone replay; stale version returns 409. All four WorkOrders × Wizard combinations tested. | Selected legacy/resource/packet scope and native action-role matrix now passes21 normal-user session tests; real browser authentication/bootstrap/deployment remains unverified. Overview advertises only the WorkOrders flag, not Wizard availability. |
| Capture and transaction | Existing capture/security/completion suites pass revision/active-lock/decision/foreign-capture/atomic-consumption and rollback cases. | Native saved-decision UI rehydration, original audio and packet capture forwarding. |
| Effects | Existing effect suite passes idempotent execution, backoff, unknown-outcome reconciliation, authority and failed-effect/committed-completion separation using synthetic/local executors. | Actual configured external delivery/acknowledgement; no real integration endpoint was contacted. |

Backend test success does not prove browser draft retention, external business-system writeback, physical acceptance or human contract approval. Required runtime/owner cases in the rightmost column remain open.

## D11/D13: preserve review and media provenance

The capture service owns active-capture locks, immutable revisions and accepted/edited/rejected decisions. Current Review initialization and submit behavior do not establish faithful rehydration of those decisions. The native voice handoff accepts an explicitly reviewed transcript; it does not establish retained original audio.

**Owner decisions:** approved audio/transcript provider and retrieval lifecycle; exact work/request/capture/session linkage; unchanged versus corrected decision semantics; packet capture handoff; interruption and consent/retention requirements. These decisions must reuse the existing voice/capture owners and services.

Acceptance requires saved decisions and source media to survive reload with their original revision/attribution. No new extraction wizard, second recorder or parallel evidence store is introduced here.

## D19: exact work/request context in the existing assistant

`AIChatRoutingHint` currently contains machine ID and name. The existing analysis scope supports authorized asset selection, date window and source-class allowlist. A `work_order` source class selects a kind of evidence; it is not an exact work-order/request ID.

`AIChatDrawer.handleSendMessage` currently warns and sends after a failed machine-scope update. That behavior is source-confirmed, not newly exercised by this baseline. It cannot serve as proof of R6 fail-closed Respond/Correct context.

**Existing assistant owner must agree:** canonical work/component/packet/request identity and revisions; request ownership; selected evidence and units/window/provenance; thread reuse/new-thread behavior; authorization/version handshake; capability-unavailable/refused scope; saved result/event IDs; return panel and draft restoration. These are requirements for a contract, not invented fields in a shipped wire type.

Required contract cases: two requests on one machine never cross-bind; two work orders never share completion; wrong/revoked scope refuses send; unsupported context is explicit; reload restores saved evidence and correct request; compound evidence and attributed corrections return to the same work record. Existing global chat remains the only primary assistant.

## D25 and review/capacity inputs

No hardware, firmware, phone platform, SDK or media transport is selected. O09 explicitly defers selection for S0/S1 and names the user as project decision owner. Audio-only voice/uploaded video are not glasses ingestion; P48/S7 real hardware/session/persistence/reconnect release acceptance remains unverified.

This deferral follows explicit user delegation, not silence. Do not imply that it satisfies later hardware criteria or prove unsupported live-video capabilities.

The user is the sole frontend/backend decision owner and delegates bounded approvals to Codex. P00/P01/P04's artifacts are accepted; P02 remains In progress for actual canonical context work. One user-plus-agent stream is the accepted planning basis, not a known weekly capacity or staffed17-week commitment. Independent/manual review is not claimed.

## Closure-pass disposition

Agent engineering/design/planning reviews are complete in `closure-review.md` and `design-review.md`; exact owner choices above are still unconfirmed. Shared Closeout types and board/detail projections preserve existing wire shapes only. Permission fixes do not approve D12/D17/D19 or original audio. Full synthetic validation is 366 backend/95 browser/44 unit plus S0 types; it is not external delivery, hardware or human product acceptance.

**S1 overlap:** the user starts S1 while these decisions remain open. P14 reuses the existing indexed-media metadata/stream viewer for native machine-file preview. That is not an original-audio, unindexed-file delivery, exact assistant-context or final-update contract extension. The generated/chat wire payload remains unchanged; current implementation/tests are in `s1.md`.
