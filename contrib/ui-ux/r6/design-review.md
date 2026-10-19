# S0 agent design review — L01–L07

**18 October 2026. Status: agent review complete; human/domain signoff pending.** One coherent direction: preserve the industrial workbench and native capabilities, improve containment and task hierarchy, and keep safety/authority/evidence explicit. This is not a Figma workflow, final numbered R6 screen set, accessibility certification or deployed acceptance.

**19 October update:** O02 in owner-approval.md accepts the bounded native/shared-wrapper preservation and L01–L07 direction under explicit user delegation. P04's artifact is accepted; provisional scenes and unverified accessibility/domain behavior remain provisional. Pending-human-review references below belong to the18 October checkpoint, not a request for another team. Independent manual auditing is not claimed.

## Evidence reviewed

Actual synthetic screenshots were inspected, not only component source:

- Native production Layout/header and Details, before/after at 390px; after at 1440px. Original measured overflow: 696px document at 390px; shared-wrapper treatment: 390px. The earlier component-only baseline measured 680px.
- Phone native Work overview: canonical WO-73 identity, distinct lifecycle/board/priority badges, metadata and board-card section.
- Desktop native Work/standalone baseline: retained metadata and cards. This capture does not show the proposed complete R6 Closeout report surface.
- Phone packet-closeout rejection: all entered action/result/verification text remains, error is visible, and retry/cancel remain reachable.
- Phone failed-trend table: raw zero and units stay visible; missing values/trends are not replaced with invented readings.
- Narrow location explorer: selected tree path, Choose location and search remain available. Existing tests cover keyboard tree navigation and error-versus-empty states.

Evidence roots: `data/devscratch/r6-s0/2026-10-18/biome/browser/` (historical), `closure/layout-before/`, `closure/layout-after/` and `closure/browser/` (new checkpoint). See the checkpoint manifest for exact files/hashes. The full new browser run also checks long title/serial values in light/dark, 360/390/430/1024px containment, native tab End/Enter activation, Attachments and return to Details. Synthetic providers, fallback Timeline fonts and seeded shell roles are explicit limitations.

## Top five improvements, in harm order

| Priority / finding | Treatment and reason | Preserve / acceptance |
|---|---|---|
| Major — R10 page-wide overflow hid native identity/actions on phone | **Implemented:** PageDetail action/identity groups wrap; PanelGroup switches to labeled horizontal phone tabs with local scrolling. At wide sizes, retain the native vertical rail. | No Details field/action, QR/Move destination, header component or machine route is replaced. Assert document width does not exceed viewport; reach Attachments by keyboard, return to Details; verify long identity values and both themes. Human approval still pending. |
| Major — command failure/physical success can be confused | Packet screenshot shows a stale-command error with preserved text, but the native title says “return to service” while the command proves internal completion, not every operating/authority fact. | Preserve draft/error/safety blockers and endpoint ownership. L05/L06 must label technical acceptance, authority, actual service and final record update separately. Lost receipt/reload/replay must reconcile, not silently submit a new command. Requires D12/D17 decision, not cosmetic relabeling alone. |
| Moderate — phone Details values wrap awkwardly inside native table cells | Shared containment restores reachability, but values such as manufacturer can still break mid-word and the native table has nested chrome. **Not redesigned:** P04 forbids a new Details page. | Keep fields, labels, description, original source and actions. Propose native/shared table readability review only if explicitly accepted; test long serials without truncating identity. Do not call containment a complete typography/readability approval. |
| Moderate — Work summary gives equal weight to metadata instead of the next feasible task | Native screenshot has three equal badges and many date/assignment fields before work content. L04 should lead with supported next action/owner/restrictions, then saved evidence and existing metadata through the seven homes. | Retain board/lifecycle distinction, canonical work/card IDs, all existing sections and direct navigation. Phone brief stays above the active task; no tab visit changes lifecycle. Current native view is a baseline, not a completed R6 panel design. |
| Moderate — unavailable telemetry needs explicit accessible meaning | The failed-trend screenshot preserves 0 MW and raw units, but small secondary timestamps, question-mark state icons and “No trend” are insufficient evidence of full quality/freshness/error comprehension. | Keep raw values, units, observed/received/source distinctions and native charts. L03/S3 must provide accessible state names and meaningful unavailable-source recovery without invented green status, thresholds or interpolated gaps. Contrast and screen-reader behavior require measured checks, not visual guessing. |

## L01–L07 disposition

| Layout | Reviewed starting point / direction | Status and later acceptance |
|---|---|---|
| L01 Machines | Actual narrow location explorer plus native Details/QR/header. Retain tree/selection/filter/return context; local scrolling rather than global minimum width. | Native treatment implemented/tested; human review pending. Later P03 must preserve URL/list state and scoped condition/coverage distinction. |
| L02 Equipment | Source/spec review of existing authored scene and native mimic. Proposed viewer plus component list/inspector; stack on phone. | **Provisional design only:** no rendered R6 3D port reviewed. P05/P06 require actual scene, reference disclosure, mesh/list/keyboard parity and stable renderer under polling. No screenshot approval claimed. |
| L03 Health | Actual supplied-trend table and native mimic/Performance baselines; retain native chart families and signal/range ownership. | Agent critique complete; new summaries, ten-field layout, gaps/axes/expanded chart and measured accessibility are S3 implementation/QA. |
| L04 Work/Diagnose | Actual desktop/phone native overview, card navigation and packet views; seven directly reachable homes with an evidence-led brief. | Direction reviewed; seven R6 homes not implemented. D06/D19 exact request and chat contract still needed for Respond/Correct behavior. |
| L05 Verify & Return | Packet safety/readiness source and rejected closeout screenshot; distinct attempts, authority and actual service outcomes. | **Provisional:** no R6 verification surface or approved domain criteria reviewed. Preserve failures/retests and keep final update in Closeout. |
| L06 Closeout | Actual native packet draft/error and standalone consumer baseline; story → reviewed facts/material/time discrepancies → readable report → one final update. | Agent review complete, future R6 composition provisional. Do not approve original audio, saved review rehydration or uncertain-result recovery from present screenshots. |
| L07 Resource/capture | Native Attachments navigation and existing drawer/reflow/capture source; exact owner model/PK; preview replaces body with Back. | Native reachability tested. Counted Work drawer, preview/focus/draft return and recording-received/persisted/extracted/saved states need later implementation and integration proof. |

## Four-pass conclusion

- **Critique:** visibility and truthful command/authority meaning outrank decorative changes. Record the five findings rather than assigning invented UX scores.
- **Distill:** retain native controls/data; later remove duplicated visual emphasis, not safety, attribution or source metadata. No new theme, icon family, inventory store or assistant.
- **Polish:** existing Mantine breakpoints/tokens, labeled phone navigation, wrapping action group, desktop rail and native light/dark behavior. No arbitrary global width or tiny operational text.
- **Harden:** browser checks cover long identity strings, keyboard Attachments/return, selected themes/breakpoints, missing trends, synthetic denials and rejected packet draft preservation. Full WCAG contrast/screen-reader, all plugin/grouped panels, real media/offline hardware, timezone and deployed login/latency remain unverified.

The later delegated project-owner approval/date/scope is recorded in owner-approval.md. It accepts the shared responsive treatment and provisional direction for development, not unimplemented screens, physical authority or a product release. No independent human audit is claimed; actual manual review remains required before a PR.
