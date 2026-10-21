# P17 native workflow frame — field and action homes

**Started:** 19 October 2026, following the verified P03–P16 checkpoint. This is a bounded native navigation/preservation slice, not completion of the later autonomous case, physical-service, assistant or final-update designs. P00/P02 remain unfinished.

## Native migration map

| Existing field or action | Home in the seven-panel frame | Provider / preservation rule |
|---|---|---|
| Work ID, reference/title, machine/location, Back, Board stage, lifecycle, priority | Shared identity | Same WorkOrderOverview and owning work ID; stage/lifecycle/priority explicitly labeled. No card-ID substitution or machine Details/header redesign. |
| Description, type, assignee, estimated effort, scheduled start/end, due date, actual start/completion, reference, created/updated | Shared Record details disclosure | Preserve every existing field/value; a native disclosure keeps the case brief first, with explicit field-access checks. Elapsed dates are not attributed labor or service authority. |
| Source alert and retained alert identity; problem/impact and qualified native diagnosis; tracked Board cards | Work | Existing native components and overview fields. No new AIMMS action-success, timer or request facts. |
| Findings, observed values/units, immutable snapshot/source, observation time and verification label | Diagnose | Existing InvestigationSection with findings only. Null and zero remain distinct. |
| Approved scope/version/cause/steps, crew and planned elapsed, failure codes, gates and requirements, packet state/generation/link, dependencies | Plan & Safety | Existing native scope/safety/packet/dependency displays. Approved scope is not a new physical authority; preliminary diagnosis stays qualified here too. |
| Required quantities, allocation and native part links | Parts | Existing PartsTable; allocation is not issuance, fitting or service availability. |
| Persisted verification summary and verification timestamp, when supplied | Verify & Return | Read-only recorded summary, not invented physical attempts, accepted criteria or operating/service return. Those missing providers remain explicit. |
| Existing maintenance record and structured completion; standalone native closeout controls or owning packet link | Closeout | Preserve `canonical_commands_enabled && !repair_packet`. Do not create a second completion command. The native capture/parts/readings/complete implementation remains intact pending the agreed UI15/UI20 refactor. |
| Native audit events, reason, actor-source data and timestamps already supplied | History | Same append-only event projection; opening History writes nothing. |
| Native AttachmentTable and work owner binding | Counted shared resource outside the seven workflow panels | P28 reuses native file actions, table state and the single global preview. Exact `model_type=workorder`, `model_id=WorkOrder.id`; no eighth workflow tab, duplicate viewer or inferred applicability. |

## Routing and state

Use a validated `work_panel` query on the existing `/maintenance/work-orders/:id/*` route, so old links still open Work and every panel can be bookmarked. Unknown panels show an explicit recovery state. Panel names do not establish lifecycle stages.

The workspace owns only selected/visited panel state. Lazily mount a panel on its first visit, then keep it mounted for this work ID so the existing native closeout form/capture state is not destroyed by switching panels. Mantine9 explicitly uses `keepMountedMode='display-none'`: the default React Activity mode restarts effects when returning, even while retaining input state. The governed native reads remain four before and after returning in the controlled test. Do not invent persistent capture decisions, refresh/two-tab recovery or completion intent storage. Changing work identity resets the workspace.

## Counted resource and owner boundary

- `WorkAttachmentResource` requests the native scoped collection's count with `limit=1`, keyed by host, actor, authorization generation and work ID. It does not count filtered visible rows or treat a failed count as zero.
- Opening the nonmodal disclosure rechecks the count before showing files. First successful visit mounts the native table; subsequent closing uses hidden presentation to preserve native search/selection. Returning focus does not change the selected workflow panel.
- Existing upload, external-link, edit, delete, dropzone and bulk-delete success callbacks refresh the count. Browser save evidence intercepts a synthetic API; it is not a deployed native mutation acceptance.
- Bound attachment tables opt out of ambient page query filters. A host `work_panel`, Machines return-state `search`, or supplied `model_type`/`model_id` must not disable native controls or override a resource's owner. Interactive native search, filters and pagination remain. Other generic tables retain their default URL-filter behavior. This deliberately stops bound attachment resources from consuming old ambient URL-filter links.
- Work preview requires an exact matching work owner and uses the existing indexed-media metadata/hash/bytes authorization. Five normal-user session/role/grant cases cover same-client reads and fail-closed customer-attributed, foreign, unresolved, revoked and unindexed sources. No production media provider or direct storage policy changes.
- The shared native table toolbar now wraps instead of overlapping search and file actions on narrow screens. The external-link test uses a real pointer click and native submit, not forced activation.

## Explicit integration boundaries

- S1 adds the native frame, not A07 autonomous handling/person-work facts or a fake operational request model.
- D09/D10 physical tests, criteria and operating/service authority remain distinct from a structured closeout verification string.
- A18.15's shared reading controller and direct ready account/report/final update depend on the real UI15/UI20 work. Do not claim that wrapping the existing native closeout stepper completes that acceptance.
- D12/D17 completion target, replay and reconciliation and D19 exact assistant/work/request context remain owner-gated. No new assistant scope bag, browser labor timer, physical-control write or automatic command on navigation.
- These frame captures are synthetic component/navigation evidence, not accepted R6 numbered operational scenes. Later screen dispositions retain their real integration stages and primary owners.
- Phone safety summaries must stay outside technical-table scrolling. Only the native gate table scrolls; blocking count, stop warning and waiver explanation remain within the viewport. S1-21 records the red-before and refinement replay rather than treating the preceding full browser pass as proof of this geometry.
