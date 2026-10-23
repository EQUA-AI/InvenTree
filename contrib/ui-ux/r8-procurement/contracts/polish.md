# R8 polish contract evidence

Presentation-only consumer work in `r8-polish`, from `3f82042d0`.
Authoritative producer contracts remain [integration-wave1](../reports/integration-wave1.md), [P1](../reports/P1.md), [P2](../reports/P2.md), [P3](../reports/P3.md) and [p2](p2.md).

- U1 reads the same Materials procurement context and six independent factual columns. Source, named approval, link, document navigation and acknowledgment retain their existing handlers and permission/disabled predicates. Commands are grouped visually; no read invokes a command.
- U2 keeps the same explicit Work ID and exact PO-line association, document/ack visibility and retained last-read behavior. A Work / source heading does not confer approval or delivery authority.
- U3 uses the existing native form, required batch and exact retained request/key/actor recovery. Only its maintenance title/field label and reservation-code presentation change. Pending, attention and failed reads stay visible.
- U4 retains material/stock/tracking/receipt/fit IDs and goods-plus-charges cost facts. Explanations move into accessible information controls. Plain-text report serialization remains plain text. Completed account and frozen-report behavior are unchanged.
- All Work tabs retain their native routes, owning panels, command gates and actual values. Missing facts use an em dash with the original reason on hover/focus. Read failures and actionable states remain visible. Dependency labels map only the four native `KanbanCardDependency.TYPE_CHOICES`; procurement event titles map only the six documented producer events. Hidden procurement metadata remains `{"hidden": true}` and no denied metadata is inspected for labels.
- Nothing sends email, changes named approval, seeds stock, alters Materials reads, grants machine scope or changes backend code.

Verified by source inspection, isolated backend checks, app/harness types, unit tests, fixture browser runs and inspected screenshots. [The lane report](../reports/polish.md) records exact counts, resolved intermediate failures and inherited migration/tracking gates. Fixture evidence is not authenticated deployment, real showcase or product acceptance.


## Fix round 1 — retained authority and refused sources

On `4175a75aa`, normal specialized Closeout command gates were intact; the host permission failures matched new information/missing-value controls. Those remain readable. Retained capture and decision save retries now require their corresponding current specialized permission and readable sources, including after reload. Retained final-update retry uses the same source lock as a new final update. Drafts, command owner/endpoint, payload and idempotency key stay retained. Capture-disabled manual account fields require complete permission. Already-open dictation rechecks capture authority/source availability, disables write controls on loss, and does not start a cancellation write when closed while locked.

A refused detailed Parts read has an explicit unavailable alert. Only actual nonempty overview requirements may supply the native fallback; refusal never establishes zero requirements. Retry only reads. Packet dictation continues to lock final update via the retained Narrative editor after the completed-question card disappears.

The [Fix round 1 ledger](../reports/polish.md#fix-round-1) distinguishes reproduced product gaps, semantic selector failures and the fixture's opaque-origin storage exception. Backend named approval, recorded-only email, replay guarantees, Materials read-only access and machine scope remain unchanged. Local fixture evidence does not replace server enforcement or deployment acceptance.
