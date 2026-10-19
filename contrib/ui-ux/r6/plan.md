# AIMMS R6 UI/UX implementation plan

**Prepared:** 18 October 2026 · **Status:** user approved beginning S0 on 18 October 2026; S0 in progress.

This is the approved delivery direction, not a claim of completed screens or runtime acceptance. Preparation was documentation-only; subsequent implementation and evidence are recorded in the S0 ledger. No push or PR is authorized by this plan.

## 1. Recommendation and scope

Evolve the existing native application into a coherent, evidence-led maintenance product. Do not rebuild the shell, assistant, inventory system, charts or workflow engine. Prioritize a connected equipment → investigation → safe work → verification → Closeout journey over isolated attractive pages.

- **52 current work items:** worklist 01–52, mapped to handoff tickets P00–P51.
- **6 later/conditional extensions:** worklist 53–58, mapped to L01–L06.
- **95 numbered review states**, not 95 new routes: 88 current product states, 5 later procurement states (32–36), 1 review-only state (47), 1 retired state (56).
- **21 UI areas**, including preserved areas, drawers and a shared controller; not 21 separate pages.
- Acceptance retains **72 operational actions, 26 stakeholder journeys, 32 consolidated requirements, 54 original feedback dispositions and 23 acceptance groups**.
- All states 70–95 are current operational scope, even when omitted from the successful showcase.

Companions (kept under `contrib/ui-ux/r6/` because new `specs/` files are Git-ignored):

- [Sprint backlog](sprint-backlog.csv): every P/L ticket, worklist item, prerequisite, owner and acceptance group.
- [Screen delivery register](screen-plan.csv): every screen ID, owning area, delivery stage and disposition. Stage means integrated review readiness, not the only time the screen is touched.
- [Agent handoff](HANDOFF.md): current state, safety rules and exact next steps for Copilot or Codex.
- [S0 execution ledger](s0.md): subtask status, tests, preservation inventory, layout review and fixture checklist.
- [Contract register](contracts.csv): D01–D26 providers, evidence level and open owner decisions.
- [Native baseline](native-baseline.md), [display projection seams](display-projections.md) and [integration decision brief](integration-decisions.md): resumed S0 leaf permissions, confirmed inputs and reviewable owner questions.

### Source hierarchy and evidence

Authoritative directory: `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/EQUA_AIMMS_Handoff_R6_FINAL_UI_UX`.

1. Latest explicit R6 clarifications and overrides; supplied operational workflow.
2. R6 master blueprint, numbered inventory, backend dependencies, acceptance, backlog and companion traceability/journeys.
3. `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/EQUA_AIMMS_Simple_Complete_UI_UX_Worklist.md` as the complete plain-language scope index.
4. `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/EQUA_AIMMS_R5_1_Reference_Previous_UI_Screens` for supporting layout/style reference only.

The handoff's inspected native baseline is `6871d38`; this workspace was checked on `inventTree-aniket` at `fe0d73a4a`, initially clean. Differences must be reconciled rather than assuming the handoff describes the deployed build. Source/layout review of R5.1 was performed; its PNGs/WebGL were not visually reviewed or rendered in this planning session. Browser and hardware acceptance remain pending. Prior-session deployment/database reports are not fresh runtime verification.

### Non-negotiable preservation

- Leave native Machine Details, identity header, QR/barcode, placement and actions intact. Screen 56 stays retired; never reuse its ID.
- Seven directly navigable work panels: **Work, Diagnose, Plan & Safety, Parts, Verify & Return, Closeout, History**. They are views, not required sequential steps.
- Attachments remains a counted resource, not an eighth stage.
- Existing AIMMS chat remains the primary diagnostic interaction. No duplicate assistant or competing response wizard.
- Preserve native Health graph forms, all Performance chart families, controls, Board, Calendar, Timeline, Risk Radar and feature/permission rules.
- Real interactive 3D is the default Equipment view; existing Process schematic is secondary. Preserve legacy schematic deep links.
- Only Closeout contains **Update maintenance record**. No final action in Work or Verify & Return.
- OT/SCADA remains read-only. UI actions must not imply physical execution, isolation, operating permission or service restoration.

## 2. Current application: reuse and verified source gaps

Paths below are relative to the repository root. Findings are source inspections, not claims that features work in the current deployment.

| Area | Existing implementation to reuse | R6 work / risk |
|---|---|---|
| Shell/routes | `src/frontend/src/router.tsx`, `components/nav/{Layout, Header, NavigationDrawer}.tsx`, `components/panels/PanelGroup.tsx` | Preserve nested links, saved panels, plugin extensions and `/tasks/*` compatibility. Do not replace app-wide navigation with a new dashboard. |
| Equipment | `pages/assets/{MachineIndex, MachineDetail, EquipmentRegistry}.tsx`; backend `InvenTree/assets/{api, registry_api}.py` | Refine location explorer, add controlled 3D integration, nest mimic; canonical component and client scope are critical. |
| Health | `pages/assets/health/{MachineHealthPanel, SignalTrendChart}.tsx`; backend `InvenTree/machine_health/api.py` | Complete summary/columns, truthful gaps/time spacing, same signal on expansion; preserve native graphs. |
| Performance | `pages/assets/performance/{PerformancePanel, TimeRangeControl}.tsx`, `usePerformanceData.ts` | Preserve visualization inventory; correct incompatible axes/unevaluated green summaries; add search, comparisons and bounded history. |
| Work | `pages/maintenance/MaintenanceIndex.tsx`, `pages/tasks/workorders/WorkOrderDetail.tsx` | Current overview uses Kanban; canonical commands are feature-dependent. Work-order lifecycle, board stage and card identity are not interchangeable. |
| Repair/safety | `pages/repair/RepairPacketDetail.tsx`; backend `InvenTree/repair/{api, risk_api}.py` | Reuse safety gates, diagnosis, revisions and work-package links. Packet-owned versus canonical work needs explicit route/action parity. |
| Closeout | `pages/tasks/workorders/CloseoutPanel.tsx`, `DictateCloseoutModal.tsx`; backend `tasks/services/{closeout, closeout_capture, work_orders}.py` | Existing stepper must be adapted, not simply relabeled. Packet-owned work does not mount this native panel; map its alternative completion authority before consolidation. |
| Chat/voice | `states/AIChatState.tsx`, `components/nav/AIChatDrawer.tsx`, `hooks/{UseAIChat, useVoiceCapture, useVoiceLiveSession}.tsx` or `.ts` as applicable | Current opener has machine hint, not typed work/request/Closeout context. Machine-scope failure can currently allow sending to continue: do not rely on it for fail-closed R6 scope. |
| Inventory/resources | Native Part, Stock, Purchasing and Attachment panels; backend company/order/stock/common APIs | Link exact existing records. Do not build duplicate Part/PO/document stores. Shared Azure PDF storage is still an operational dependency, not something this UI plan provisions. |

### Important integration discoveries

1. **Completion:** backend requires `expected_version` and `idempotency_key`, stores command events, and replays the identical request. Current Closeout generates a fresh key per invocation, has generic failure handling and loads effects after receipt. There is no wired durable pending identity/uncertain-outcome recovery in that panel. No dedicated native lookup by idempotency key was located. Confirm safe exact-request replay and authorized result/effect reconciliation before considering a narrow API extension.
2. **Capture:** native Closeout has a single-active-capture guard, version/row locking, immutable revisions and reviewed-decision equality checks. Current Review sends nonempty fields as edited and may not rehydrate saved decisions faithfully. Prefill, voice, resume and unchanged-versus-corrected review require deliberate integration.
3. **Voice provenance:** native dictation accepts a device-keyboard/typed transcript; that is not proof of retained audio. Live audio exists separately. Required voice story/original audio/transcript linkage must be demonstrated, not inferred from a microphone button.
4. **Chat:** agree versioned work/request/evidence intent and persisted-result return with the existing chat team. Do not resurrect the removed parallel record-scoped conversation rail or silently reduce work chat to machine chat.
5. **Glasses:** inspected live transport is audio-only. Uploaded video evidence is not live camera ingestion. Hardware, firmware, transport, SDK, session host and phone platforms are unselected/unverified. This is a full-R6 release gate, not a cosmetic phone status card.
6. **Historian bounds:** current Health maximum is 6 hours; visible presets are 5/10/15/30 minutes. Performance maximum is 24 hours; server series points are clamped to 24–360. Longer investigations use supported bounded slices and retained evidence; extent metadata is not an unlimited query API.
7. **Routine work:** reuse existing procedure/task/job-kit support for preventive, inspection and calibration variants. A new PM recurrence engine or procedure-authoring product is not required merely because no dedicated screen exists.

Actual frontend lockfile includes React 19, Mantine 9, Router 6, TanStack Query 5, Zustand, Lingui and Playwright. Keep this stack. The backend combines Django/DRF and an authenticated FastAPI AI service under ASGI; a Django-only run does not validate AI integration.

## 3. Product design direction

### Visual system

Use a restrained industrial workbench: native indigo actions, neutral surfaces, thin borders, modest elevation, clear technical typography and deliberate whitespace. No marketing hero, decorative KPI walls or rainbow status cards.

| Element | Design target |
|---|---|
| Typography | Existing font; page titles 22–24px, sections 16–18px, body 14px. 12px only for secondary metadata. |
| Spacing/density | Native token equivalents of 8/12/16/24px; practical 48–64px one/two-line rows; compact and readable rather than sparse or cramped. |
| Actions | At most one filled primary action per active task surface; contextual links/secondary actions for inspection. Never hide required native actions. |
| Status | Text + icon + color: Normal green, Warning yellow, Critical red, Offline/Unknown gray. Condition, quality, freshness and coverage are separate. |
| Layout | Content-led main column, optional 300–340px aside at wide desktop; stack at narrower sizes; prose about 70–85 characters wide. |
| Tables/charts | Adjacent units, tabular numerals, purposeful precision; local horizontal scrolling, no page-wide overflow. Required columns remain reachable. |
| Theme/motion | Preserve native light/dark support; visible focus, reduced motion, no continuous model rotation or unsupported operating animation. |

These are native theme mappings, not a second design system. Do not copy R5.1's 960px minimum page width, light-only shell, 9–11px operational text or CSS that hides required meaning.

### Screen composition and interaction plan

| Surface | Design specification | Screen references |
|---|---|---|
| Machines / native Details (UI01–02) | Scoped tree + searchable result table. Preserve expanded location, list position and filters. Keep native Details/header untouched. | 01; 56 retired |
| Equipment (UI03) | Roughly two-thirds viewer / one-third inspector; visible component list; 3D/Process switch. Inspector: Overview, Work, Identity & Parts. Stack on narrow screens. Reference-model disclosure; honest unbound/parent context. | 02–09, 48–49, 55, 67 |
| Health (UI04) | Four summary dimensions above signal table; ten accessible fields; original row sparkline and full blue chart. Signal selection → evidence/trend, expanded chart → same signal/range on return. | 10–14, 50, 70–71 |
| Performance (UI05) | Section navigation and channel search above retained charts; collapse/Show all; compatible comparisons, saved sets and source-window disclosure. Never replace all charts with a generic dashboard. | 15–16, 72–74 |
| Machine records (UI06–09) | Open/completed work views; native Parts/Stock links; retained event evidence in Fault History; attachment selection/preview with return restoration. | 17–23 |
| Maintenance (UI10) | Actionable Work list: My work / Needs attention / All; URL then saved choice before role default. Machine/component, priority, lifecycle, next action, AIMMS progress, owner, schedule. Keep four native alternate views. | 24–25, 54, 64–66 |
| Work (UI11) | Identity/header → supported outcome/next feasible action → actual service/equipment state → saved AIMMS work → investigation/scope → dependencies/ownership → verification/Closeout summaries. Existing details remain available. | 26–27, 51, 58, 62–63; shared 75–95 |
| Diagnose (UI12) | Qualified explanation, unresolved questions, support/contradiction evidence, next request and queue. Respond/Correct/Continue opens existing scoped chat. No parallel questionnaire. | 28–29, 75–77, 79, 81, 94 |
| Plan & Safety (UI13) | Effective scope/procedure revision, sequence, resources, restrictions, prerequisites, operations window and sourced criteria. Separate preparation from authority and physical state. | 30, 77–78, 80, 90–91 |
| Work Parts (UI14) | Required → Reserved → Issued → Fitted, with source record, actor/time, fit basis, stock location and returns. Shortage owner visible even while full sourcing is deferred. | 31; 32–36 later; 77 |
| Verify & Return (UI15) | Criteria and attempts → technical acceptance → controlled-test authority/release → normal-service permission → actual operating/service outcome. Preserve failed/retest evidence; link to Closeout. | 37–40, 52, 80–82, 93 |
| Closeout (UI21 + UI20) | Read-only verification summary → technician story → prepared account → materials/time discrepancies → directly readable report → single final update. Voice first, reuse known answers, typed fallback/reason. | 41, 59–61, 83–86 |
| History (UI16) | Chronological sourced work/evidence/material/test/report/time events, accepted versions and amendments. Distinguish occurrence and receipt times. Hide infrastructure noise, not business facts. | 42, 78–79, 86, 92, 95 |
| Resources / capture (UI17–19) | Header Attachments(n); native work-owned drawer, target 720px desktop/full-width phone. Preview replaces drawer body with Back. Existing observation capture retains drafts and correlation. | 43–46, 53, 57, 68–69 |
| Phone / glasses (shared) | Same seven panels and case identity, immediate next request/chat, readable references and field capture. Separate device connection, media received, recording persisted and evidence saved. | 87–89 |

### Shared interaction rules and useful improvements

- Persistent case context across equipment, signal, work, file and assistant: canonical machine/position/work/request/evidence IDs, permitted scope, selected time window and return state. Do not put credentials or private evidence contents in URLs.
- A concise **case brief** states what is known, next feasible action/owner, current restrictions and missing evidence. Avoid multiple equally prominent banners for the same fact.
- Reusable **source/evidence disclosure**, **owned dependency row**, **recorded AIMMS action row** and **separate outcome milestones** improve consistency without creating new backend entities.
- Show recorded business results, not simulated progress. Opening a panel cannot generate a plan, reserve stock, start labor, accept assignment or change lifecycle.
- All source unavailable, empty, loading, stale, denied and conflicting states have distinct copy and recovery. Failure must never look like an empty successful query.
- Preserve draft, focused trigger, selected item, scroll and active panel through dialogs, capture and previews. Keep overlays shallow rather than stacking drawers.
- Phone layouts are designed during each feature sprint, not shrunk at the end. Test 360/390/430 CSS px, desktop 1100×800, 1440×900 and 1920×1080.
- WCAG 2.2 AA is the target: keyboard/list parity for 3D, focus return, contrast, non-color status, meaningful control names, reduced motion and non-disruptive announcements. Certification is not claimed.

## 4. Technical delivery approach and decision gates

### Reuse-first integration

- Use Mantine/shared panels and forms, TanStack Query for server state, Zustand only for UI state, Lingui for all visible strings. Preserve existing plugin and API contracts.
- Keep canonical display adapters small and typed. Each field records its source and explicit unavailable/unknown state. Do not invent endpoints, enum values or a parallel case engine.
- Establish a per-action matrix: existing route/control → new home → canonical ID → endpoint/feature gate → permission → test. Include packet-owned and non-packet work.
- Map D01–D26 to actual providers and classify **source inspected / owner reported / runtime tested / unavailable**. Missing information does not automatically justify a new backend service.
- Verify server-side role and client/object authorization, including attachments, shared investigations, saved views, chat and phone sessions. Visible buttons are not an access control boundary.
- Retain existing URLs or explicit compatibility redirects, saved panel keys and history navigation. Route names for new panels are agreed in S0; no speculative new public endpoint is committed here.

### 3D

Port the authored `Existing_3D_Reference/twin-scene.mjs` through a thin controlled React adapter. Lazy load and mount once per model identity; telemetry must update data without rebuilding camera/geometry. Support selection, modes, labels, presets, zoom/focus/reset, keyboard list, local error boundary and complete resource disposal.

Do not port the old `window.R4` wrapper, DOM replacement, fixture condition values or prototype mutation logic. Keep geometry identity separate from installed equipment identity. Preserve notices; the supplied Three.js MIT notice does not license arbitrary OEM CAD. Reference geometry never proves as-built fit, safe disassembly or plant operating state.

### Telemetry

- Keep original 120×28 Health graphs and full blue AreaChart. Preserve null samples and actual timestamps through adapters; `connectNulls=false` alone cannot restore discarded gaps. Do not zero-fill or manufacture samples.
- Keep all Performance forms: thermal/electrical trends, valid dual axes, step traces, heatmaps, sensor bars, cooling differences, scatter, KPI sparklines, station comparisons/tables and Other.
- Separate incompatible power-factor/reactive-power units; preserve every series. No green overall verdict when limits/evaluated coverage/history are absent.
- UI search/collapse must not change historical binding/query identity. Preserve batched requests/shared caches; measure 50/200/500-channel workloads before performance claims.
- Two before/after windows have separate bounded identities; retain source window labels during loading. No increased Cosmos throughput or unsupported long-range read.

### Closeout command and evidence

- Distinguish native completion, persisted report revision, queued effects and any configured external update. Success copy must match the actual completed target.
- Retain one logical-operation identity and exact payload/version across pending/uncertain states. Reconcile original outcome before any new command; do not create a new key on generic retry.
- Test double-click, refresh, reconnect, two tabs, stale revision, delayed effects and committed-but-response-lost cases. Telemetry refresh must not invalidate unrelated report readiness.
- Preserve active-capture exclusivity, saved decisions and required native fields `action`, `result`, `verification_summary`. Prefill must not silently create a capture, disable voice or bypass review/readiness.
- Keep original audio/transcript, attribution and correction history. Text spans are not audio timestamps. Held/retained-analysis/refurbishment material is not automatically returned/consumed/scrapped.
- Adapt the stepper to directly accessible prepared account/report; do not erase its governed capabilities. Field readers/evaluators belong in Verify & Return; Closeout consumes the same canonical results.

### Early decisions, owners and release consequences

| Decision | Owner role / target | Deliverable and gate |
|---|---|---|
| D19 exact chat scope and saved-result return | Existing chat owner + integrator, S0; P33 integration S1 | Supported typed intents, same-thread policy, fail-closed invalid scope and source-tested round trip. Blocks honest scoped diagnosis if unresolved. |
| D12/D17 final target/replay/reconciliation | Backend integrator + workflow owner, S0; P25 S6 | Native command/effects contract, packet ownership and recovery strategy; narrow extension only if necessary. Blocks final mutation acceptance. |
| D11/D13 voice/capture and decision semantics | Capture/chat owner + integrator, S0–S1 | Original audio availability, locks/resume, transcript provenance, saved decision rehydration and fallback. Blocks story-complete Closeout acceptance. |
| D25 actual glasses stack | Device/chat owner + user/operator, start S0 | Device/firmware, supported phones, SDK/licensing, session host/transport, full-duplex/live-video and saved evidence feasibility. P48 acceptance S7; blocks full R6 signoff. |
| D02 geometry and bindings | Assets/3D owner + plant reviewer, S0–S2 | Model applicability and stable position-to-scene mapping; unbound/parent fallbacks. Reference geometry allowed when labeled. |
| D09/D10 authority/service criteria | Operations/domain owner, before S4 acceptance | Real criteria, delegated authority, physical/service source records and independent restrictions. No fabricated pass. |
| D26 preferences/history | Telemetry/integration owner, S0 discovery then S5 | Native preference mapping and authorized shared sets; actual retention/window limits. No assumed saved-view endpoint. |
| Final case and deployment/media | User/domain + deployment operator, select before S7 | Coherent in-stock case and deployed capabilities. Pump 02 remains reference fixture until chosen. Azure Files access/mount remains separately blocked. |

These are assigned roles, not confirmed staffing or newly granted permissions. Work on independently testable read-only layouts can proceed while a dependency is blocked; no placeholder may be represented as integrated functionality.

## 5. Proposed sprint sequence

**Planning assumption:** S0 is one week; S1–S8 are two-week sprints, approximately **17 weeks** after approval with two frontend engineers, one backend/integration engineer, one QA engineer and shared design/3D/domain/chat/device support. This is a capacity hypothesis, not a committed deadline. Allow **2–4 additional weeks contingency** for demonstrated integration gaps; unresolved hardware/owner access can extend beyond that. A single implementation stream must be re-estimated rather than given the same calendar promise.

Scope and gates are authoritative; dates are not. Re-estimate at S0 after runtime verification and contract spikes. Within a sprint, prerequisite tickets finish before dependent acceptance. Discovery and isolated scaffolding may proceed together, but do not satisfy an unfinished prerequisite. The CSV contains every dependency. S0 is authorized; source inspection and fixture tests alone do not mark P00 complete.

**18 October closure checkpoint:** permission R02, responsive containment R10 and stale RBAC expectation R11 are technically resolved. Final evidence:366 backend/95 browser/44 unit, S0 types, Biome 24, Ruff 26 format/no introduced diagnostics (60 inherited docstring findings disclosed). `closure-review.md` and `design-review.md` complete agent reviews and one-stream planning/owner decisions. P01/P04 are Ready for review; P00/P02 and human/contract/device decisions remain open. S1 implementation stays Planned; no staffed 17-weekpromise, product acceptance or deployment proof.

| Sprint | Outcome / tickets | Reviewable exit |
|---|---|---|
| **S0 — Baseline, design and contracts** (1 week) | P00 P01 P02 P04. Runtime capability/route/field/action map; preservation ledger; theme/layout rules and desktop/phone design specs; canonical projections; selectable fixture checklist. Start chat/completion/capture/glasses spikes. | Native baseline captures and source/build IDs; approved critical layouts; contract owners and supported/missing fields; packet-owned path mapped; estimates revised. No deleted native capability. |
| **S1 — Navigation and operational frame** | P03 P14 P15 P16 P17 P28 P33. Machines explorer, Work list, seven panels, counted resources, native alternate views and scoped assistant entry. | Machine → work → attachment/chat → return preserves identity/selection; URL/saved preference behavior and denied/flag-off cases pass. Chat scope must be real, not just a button. |
| **S2 — Equipment and parts identity** | P05 P06 P07 P08 P13. React 3D adapter, controls/list, bound inspector, native schematic and exact Part/Stock links. | Real renderer, reversible modes, keyboard parity, no remount on telemetry, no bearing-to-impeller leakage, WebGL fallback and legacy schematic links pass. |
| **S3 — Health and Performance fidelity** | P09 P10 P11 P12 P37 P38. Native chart preservation/corrections, complete Health context, high-channel navigation, retained fault/work links. | Before/after native visual review; all chart families retained; truthful zero/null/irregular time; unknown coverage neutral; filtering does not refetch history; 50/200/500 fixtures exercised. |
| **S4 — Connected repair and physical outcomes** | P18 P19 P20 P21 P22 P23 P27 P29. Recorded AIMMS work, Work/Diagnose, Plan & Safety, in-stock materials, verification/return, attributed time, capture recovery. | Same records across assets/work/parts/evidence; physical facts separate from preparation; failed/no-criteria tests and restrictions visible; no extra routine manual preparation chores. |
| **S5 — Complex investigation and history** | P39 P40 P41 P42 P43 P44. Comparisons/saved views, bounded long-case history, multi-round evidence, contradictions/cannot-perform, scope revision, hold/handover/multi-technician. | Chat and panels share saved evidence; laboratory identity/units retained; only affected dependencies reassessed; accepted ownership and sibling-job boundaries; independently keyed before/after windows. |
| **S6 — Closeout preparation and service account** | P24 P25 P45 P46 P49 P51. Direct report/history, command-controller integration, voice story/known-answer reuse, material/time reconciliation, service obligations and routine-work variants. | Readable prepared account without wizard traversal; capture locks/review/voice survive resume; true material custody/unknown time; separate service/resilience/obligations; final command tested in sandbox, not exposed elsewhere. |
| **S7 — Final update, phone and field integration** | P26 P30 P47 P48 P50. Single Closeout action, conflict/uncertainty recovery, all phone panels, actual glasses session/evidence, continuous monitoring and recurrence. | One logical update despite retry/refresh/two tabs; no false downstream success; drafts retained; phone handoff and real hardware tests; prior accepted report immutable on recurrence. |
| **S8 — Release validation and design polish** | P31 P32 P34 P35 P36. Final responsive/accessibility and permission checks, coherent successful case, operational/native regression, numbered captures and reviewed release evidence. | A01–A23 signed off with evidence; 88 current states accounted for including variants; explicit later/review/retired dispositions; exact-build screenshots and known limitations; human review before deployment/PR. |

**Parallelism:** design stays one sprint ahead; QA, accessibility, authorization and failure handling begin with each feature. S8 is final signoff, not the first test pass. 3D/device feasibility starts early even though integration closes later. Hardware inability triggers a documented scope/release decision; it does not silently demote P48 to later.

**Review milestones:** S1 coherent navigation; S2 real equipment; S3 trustworthy telemetry; S5 complex operations; S6 complete prepared account; S7 connected field/update journey; S8 acceptance package. Optional demonstrations never substitute for core operational testing.

### Later/conditional extensions (separate authorization and estimate)

| Worklist / tickets | Boundary |
|---|---|
| 53 / L01 | Full sourcing, RFQ, quote ingestion/comparison. Native shortage owner/context remains current. |
| 54 / L02 | Enhanced PO/acknowledgment/receipt/allocation journey after L01. Preserve existing purchasing now; physical receipt is not usable stock. |
| 55 / L03 | Genuinely new analytics only; native fidelity, scaling, comparisons and history are current. |
| 56 / L04 | Richer cross-system saved views; ordinary location browsing and Performance saved sets remain current. |
| 57 / L05 | Additional time-correction editor only if needed; existing supported correction remains accessible now. |
| 58 / L06 | Additional report-amendment editor only if needed; immutable accepted history/native amendments remain current. |

Do not add estate dashboards, full PM recurrence, external supplier accounts, generic compliance/laboratory engines, a new document search product or seven sector-specific builds to this release merely because related backend capabilities exist.

## 6. Verification and acceptance plan

### Definition of done for each feature

1. R6 UI/screen/worklist/ticket mapping and all native field/action destinations recorded.
2. Responsive design reviewed in the native theme; real accessible controls, not static imitation.
3. Canonical API integration, role/client/object scope, feature gates and source provenance verified.
4. Loading/empty/denied/stale/error/conflict/retry states implemented where applicable; no draft/evidence loss.
5. Relevant unit/API/contract and Playwright tests pass; measured evidence separated from fixtures and source inference.
6. Same IDs, values, units, time windows and versioned results agree across related screens.
7. Design/domain review completed and deviations documented. A screenshot alone does not prove behavior.

### Required test suites / fixtures

| Suite | Essential cases |
|---|---|
| Native regression | Details/header/barcodes; existing routes and `/tasks/*`; Board/Calendar/Timeline/Risk Radar; all charts/controls; Part/Stock and attachment permissions; packet/non-packet work. |
| Telemetry | True zero, null/missing/cadence gap, irregular timestamps, raw unitless vibration, provisional limits, unavailable anomaly association, stale retained values, missing critical coverage, wrong unit pairing; search without history-key churn. |
| Investigation | Multi-round compound photos/readings/reports; sample identity/method/time/status; correction and persistent contradiction; cannot-perform; changed scope/material/authority/tests; internal specialist coordinator. |
| Handover/safety | Hold/resume; accepted incoming owner versus read receipt; multi-tech attribution; shared evidence without shared completion; test permission versus service authorization; failed retest; partial restoration and independent obligations. |
| Closeout | Prefill does not block voice; active capture conflict; reload saved decisions; accepted versus edited; original audio/transcript retrieval; honest removed-item custody; unknown labor/downtime; immutable report/amendment. |
| Command reliability | Duplicate click, exact replay, changed payload with reused key, stale version, refresh/two tabs, unauthorized action, delayed effects, response lost after commit, safe reconciliation before retry. |
| Field/accessibility | 360/390/430px phone; all seven panels; keyboard/focus/contrast; 3D list parity; no global overflow; capture/context recovery; actual selected glasses full-duplex/noise/talk-over/video/gaps/reconnect and saved evidence retrieval. |
| Operational lifecycle | Preventive/inspection/calibration finding → corrective link; service/resilience/obligation distinctions; recurrence/reopen versus amendment; monitoring remains configured throughout. |

Use local/sandbox fixtures; no tests against production mutations. Backend tests follow repository `--keepdb` guidance except migration tests. Run frontend typecheck, Biome, build and relevant Playwright configurations; register new coverage in the correct CI lane rather than assuming every test file is collected. Use ASGI for authenticated AI integration. Do not claim a pass before commands/browser/device checks run.

### Screenshot and evidence package

- Preserve inventory IDs 01–95. Capture actual implemented current states with necessary pending/stale/uncertain/denied variants; one image per ID is not necessarily enough.
- Each capture records source/build ID, route/panel, viewport, scenario/record provenance, permitted role, feature configuration and state. Clearly label fixtures versus live data and redact sensitive content.
- 32–36 stay later unless separately approved, 47 is not product navigation, 56 stays retired. Do not relabel old R5.1 images as R6 implementation.
- Include full F01–F72/J01–J26/R6-01–R6-32/feedback crosswalk using the handoff's existing registers; all 72 actions need a disposition, not an invented screen or permission group for every role.
- Deliver test results, contract-gap register, hardware/platform verification, known limitations and native preservation evidence alongside screenshots.

## 7. Rollout and safety constraints

- Local/sandbox implementation first. Preserve native recovery/deep links and use existing feature configuration for staged exposure where possible. Avoid speculative schema changes; if necessary use reviewed reversible migrations and API-schema checks.
- Before any Azure write: obtain the relevant authorization, take a fresh validated backup and run a dry-run. This plan authorizes none. Cosmos stays at **400 RU/s**.
- Keep secrets out of docs, captures, logs and source. Never use alternate credentials to bypass missing operator access.
- Keep OEM thresholds provisional without documentary approval; Millbrook speed/field-voltage questions, historical source gap and incomplete plant evidence remain unresolved. Recorded history is not current live telemetry.
- Azure reference manuals remain link-only according to prior handover; deployment-shared PDF media is not newly verified here. Show actual attachment availability rather than assuming upload/mount succeeded.
- User approves this plan before code starts. Commit only on the agreed branch, no AI co-author trailer; ask before any future push. Human review precedes PR/deployment approval.
- Release rollback must restore UI exposure and compatible routes without undoing accepted business records. Review data migrations and command effects separately from frontend rollback.

## 8. Approval and current action

**Latest execution direction:** “meanwhile start s1” explicitly authorizes overlap while S0 review/owner gates remain open. `s1.md` records the P14 native attachment preview/return slice and validation; P14/screens22–23 are In progress, not accepted Done. Dependency links, S0 contracts and hardware/human release gates are retained. No capacity/date commitment is inferred.

The user approved starting S0 and requested durable tracking for a possible Codex handoff. Exact delivery dates still require staffing and S0 findings. During S0, obtain chat/device owner availability and glasses selection; the user chooses the final showcase case before connected acceptance. No need to block baseline/design work on that final case selection.

Continue from `HANDOFF.md` and `s0.md`: finish the runtime preservation ledger, desktop/phone layout review and integration contract verification before treating S0 as complete. Do not start a wholesale screen rewrite or mark future screen states implemented from fixture captures.
