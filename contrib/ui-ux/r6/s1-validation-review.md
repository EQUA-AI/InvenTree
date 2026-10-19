# S1 native foundation — engineering, visual and workflow review

**Reviewed:** 19 October 2026. **Scope:** P03 return-state, P15 Work list, P16 preservation, P17 native workflow frame and P28 shared attachment resource. This is an agent review of local implementation/synthetic renders, not an independent human audit, deployment or full sprint acceptance. The later owner-approval.md accepts bounded artifacts/direction under delegation; P33 is now In progress for exact shared-contract work rather than blocked on another owner. Technical evidence below is unchanged.

## Environment and evidence

- Workspace branch `inventTree-aniket`, base `fe0d73a4ae66e9284dbd8bb7b6e011de08cb1adf`; changes remain local and uncommitted.
- Existing React19/TypeScript/Mantine9/InvenTree architecture and installed dependencies. Relevant EQUA Mantine, fault-to-fix, composition, performance, craft, critique, interface-audit, browser-testing and visual-QA skills applied. No Figma or replacement design system.
- Browser: production components/Layout/Header with mocked APIs, normal reader/editor fixtures, isolated Chromium/loopback server, no authenticated backend or external reads/writes. The native save is an intercepted synthetic external-link POST, not UI-to-backend acceptance.
- Backend: 36 selected labels, real normal-user login/session/stored-role/client-grant checks, copied source in a disposable network-disabled cached-image container, task-owned SQLite/config/media. No existing service/database or Azure/OT mutation. SQLite cannot prove PostgreSQL locking.
- The current exact results, source/artifact hashes and limitations are in `s1.md` and `data/devscratch/r6-s1/2026-10-19/p17-p28/manifest.json`. Earlier P03–P16 and S0 manifests are immutable historical checkpoints.
- Captures are under that checkpoint's `browser/`; scenario folders below are relative to it. Fixture banners, debug controls, yellow placeholder image and synthetic plant/work descriptions are not product data or product chrome.

## Render review

Inspected actual 1440×900 desktop, 1024×768 tablet and 390×844 phone resource/list renders, plus normal-width desktop and light/dark workflow, long-identity, safety, filter and preview captures. Technical tables scroll locally on phones rather than shrinking identifiers/units. The shared identity keeps Board stage, canonical lifecycle and priority distinct. The seven native views have direct URL homes; resources do not become another workflow stage.

Representative captures:

- `s1-pui_work_list-fifty-job-d1d76-ork-with-native-back-return-s1-desktop/work-list-desktop-wide.png` and `work-list-tablet.png`.
- `s1-pui_work_list-search-an-4f458-and-retain-canonical-fields-s1-phone/work-list-dark-filtered.png` and `work-list-phone-filters.png`.
- `s1-pui_work_panels-packet--af74d-out-claiming-service-return-s1-desktop/work-plan-safety.png` and `work-panels-packet-dark.png`.
- `s1-pui_work_panels-keyboar-657b9--within-the-phone-workspace-s1-phone/work-panels-long-identity.png`.
- `s1-pui_work_attachments-pa-e524a--disable-its-native-filters-s1-phone/work-attachments-bound.png`, `work-attachments-tablet.png` and `work-attachments-desktop-wide.png`.
- `s1-pui_work_attachments-a--0e3a4-o-the-active-workflow-panel-s1-phone/work-attachment-preview.png`.

## Reproduced findings and fixes

### S1-Q01 — P1 — bound resource could display another owner

**Status:** fixed in the bounded implementation. **User/screen:** maintenance reader/editor, Work attachments; the same generic owner-binding issue also applies to native machine resources.

**Reproduce:** open Work73 with `?work_panel=parts&model_type=assetmachine&model_id=999&search=unrelated`, then open Attachments. The generic table previously copied ambient URL filters over its bound owner. Even an ordinary `work_panel` produced a false custom-filter warning and disabled native filtering. This is a wrong-context UI binding defect, not evidence of bypassing backend tenant authorization.

**Evidence:** `attempts/aimms-r6-s1-p28-binding-red.log` and the matching failed trace/capture; final bound-resource screenshots above. **Root cause:** `InvenTreeTable` treated a parent view's query as its own filter authority.

**Fix:** optional `enableQueryFilters` retains the generic default; bound `AttachmentTable` opts out of ambient page filters. Native interactive search/filter/pagination still operate; direct original links and server authorization remain unchanged. Old ambient URL-filter links no longer filter bound attachment resources.

**Acceptance/test:** every count/table GET retains `model_type=workorder&model_id=73`, native controls remain enabled, interactive search reaches the native request, and Parts stays selected. See `tests/s1/pui_work_attachments.spec.ts`, “page query parameters cannot retarget a bound work resource or disable its native filters”.

### S1-Q02 — P1 — phone file action intercepted by search

**Status:** fixed. **User/screen:** maintenance editor, native attachment toolbar at 390px.

**Reproduce:** open Work attachments and click Add external link with a real pointer. The old nonwrapping/growing toolbar groups overlapped the search input and file buttons, intercepting activation.

**Evidence:** preserved P28 failed attempts and the native-save final screenshot. **Root cause:** shared `InvenTreeTableHeader` nowrap/grow layout, not the attachment form or permission provider.

**Fix:** wrap the toolbar and its right controls, constrain their inline size and remove the growing spacer. Existing actions, native roles and fields remain.

**Acceptance/test:** pointer activation opens the native Upload Attachment form; one intercepted POST has the exact work owner; count changes4→5 without changing Parts. See `tests/s1/pui_work_attachments.spec.ts`, “a native external-link save keeps work ownership and refreshes the resource count”. No forced click or keyboard workaround is used for this action.

### S1-Q05 — P1 — phone safety summary was inside table scrolling

**Status:** fixed and replayed. **User/screen:** maintenance technician, Plan & Safety at390px.

**Reproduce:** open the synthetic completed packet with an independently pending lockout, then select Plan & Safety. The first P17 containment wrapper scrolled the entire native safety card at720px, putting the blocking count and part of the stop warning outside the phone viewport. A visible gate row alone did not verify the complete safety summary.

**Evidence:** `attempts/aimms-r6-s1-safety-red.log` and its phone failed screenshot/trace; desktop passes and phone fails the new geometry assertion before the fix. This is a real rendered regression discovered after the preceding passing94/95 checkpoint, not a fixture-label issue.

**Fix:** move horizontal scrolling into `WorkOrderSafetyReadiness` around only the technical gate table. Keep the native warning, waiver explanation and count in the fluid card; unchanged status calculations, copy and governed safety actions.

**Acceptance/test:** the full stop warning and blocking-count element fit inside the viewport on both projects; the detailed table can scroll locally. See the packet safety/authority case in `tests/s1/pui_work_panels.spec.ts`. Focused14 and final94 S1/95 S0 replays pass after the fix; the corrected phone capture is inspected. Earlier passing screenshots remain separate pre-refinement attempts.

### S1-Q03 — P2 — returning to a retained panel restarted governed reads

**Status:** fixed. **User/screen:** maintenance technician/editor, native standalone Closeout within the new frame.

**Reproduce:** visit native Closeout, enter a local draft, switch away and return. Mantine9's default React19 Activity retention retained inputs but restarted effects; the controlled native read count changed4→8.

**Evidence:** preserved P17 iteration logs and the final retained-draft test. **Root cause:** default effect lifecycle, not a backend command or a new capture controller.

**Fix:** lazy first visit plus explicit `keepMountedMode='display-none'`, keyed to owning work identity.

**Acceptance/test:** local draft survives; four governed reads remain four after return; no completion write occurs on navigation. See `tests/s1/pui_work_panels.spec.ts`, native Closeout retention case. Refresh/two-tab draft recovery and UI15/UI20 controller consolidation remain NOT VERIFIED.

### S1-Q04 — P2 — priority label clipped in the Work list

**Status:** fixed in the preceding P03–P16 continuation. **User/screen:** maintenance reader, dense native Work list.

**Reproduce:** inspect a Medium-priority row in the initial narrow priority column. The badge clipped its label.

**Evidence:** immutable P03–P16 attempts and current desktop/tablet list captures. **Root cause:** insufficient technical-column width.

**Fix:** 112px priority column; retain readable lifecycle/owner/window columns and local phone table scrolling.

**Acceptance/test:** the Medium badge's scroll width does not exceed its client width; the50-job case paginates and preserves exact owning-work Back state. See `tests/s1/pui_work_list.spec.ts`.

### S1-Q06 — P1 — refused machine scope silently sent the question anyway

**Status:** fixed in the first P02/P33 machine-scope slice; full regression checkpoint follows in `s1.md`. **User/screen:** normal-reader fixture, existing assistant launched from a machine.

**Reproduce:** Ask about machine7, enter a question and refuse the scope PUT with403. Previously the warning path still posted the typed message, cleared the hint and lost the composer's draft. The red-before browser capture shows the generated response instead of a blocking error.

**Fix:** require a canonical native GET/PUT acknowledgement before the launch sends. Validate thread, schema, machine IDs, source/date selection, editability and exact next version; do not treat HTTP200 or a matching name as identity. A persistent native Alert explains that nothing was sent and the draft/files remain. Retry is supported; discarding the machine hint is explicit. Concurrent activation and stale closed intents cannot duplicate/send the old launch. Backend authorization remains authoritative; hash shape validation is not cryptographic verification.

**Evidence/tests:** `tests/s1/pui_assistant_context.spec.ts` passes32 desktop/phone cases within126 passing S1 checks;43 parser and6 identity-display units pass within168 units;11 existing native backend scope tests pass. All17 existing native-drawer cases pass after correcting a real reload regression: detail summaries lack canonical IDs, so hinted summaries now load the full native scope. A delayed read cannot overwrite a newer acknowledged scope. The failed16/17 attempt remains retained. Reviewed actual light/dark phone/desktop error captures,1440/1024 resizing and post-retry response: message, attachment, machine identity, dismiss and send controls remain readable/reachable. Complete preservation replay is indexed in the current context checkpoint. Synthetic reader/API mocks are not browser-login or real upload acceptance. Exact work/request focus and persisted-result return remain unimplemented.

## Remaining gates — not accepted defects or invented behavior

| ID / priority | User / screen | Gap and next verification |
|---|---|---|
| S1-G01 / P1 | Maintenance / existing assistant | **NOT VERIFIED:** exact work/request/component/conversation intent and fail-closed result-return implementation. O07 delegates D19 design authority; P02/P33 must define/build/test the actual contract. Machine hint and media `work_order_id` are not substitutes. O09 defers selection but retains later device/session acceptance. |
| S1-G02 / P1 | Technician and operations / Verify, Closeout | **NOT VERIFIED:** physical criteria/attempts, operating/service authority, saved capture decisions and final-update/replay policy. Keep standalone and packet command ownership distinct; navigation and a verification string are not UI15/UI20 or return-to-service acceptance. D09–D12/D17 need domain/integration review. |
| S1-G03 / P2 | Technician/manager / Work list | **NOT VERIFIED:** real persona claim mapping, mixed-role precedence, Needs-attention rule, neutral observation action and durable AIMMS/next-action/component facts. Native CRUD roles and list filters do not establish these contracts. See `work-list-contract.md`. |
| S1-G04 / P2 | Reader/editor / machine and Work files | **NOT VERIFIED:** real storage/original-file policy, codec/native PDF rendering, full upload/edit/delete journeys, applicability/revision metadata and deployed UI-to-backend authorization. Indexed client-owned Work metadata/bytes are tested separately; customer-attributed/unresolved/foreign/unindexed sources fail closed. No auto-indexing or production media-policy change. |
| S1-G05 / P2 | Keyboard/screen-reader users / all changed screens | Keyboard navigation, return focus and responsive containment are checked. **NOT VERIFIED:** screen-reader/axe audit, full contrast,200% zoom, localization and all native form dialog semantics. No WCAG certification is claimed. |
| S1-G06 / P2 | All roles / deployment | **NOT VERIFIED:** real login/bootstrap/CSRF, production database/driver/locking, large-population capacity, deployment or human design/preservation review. Selected backend migration-drift warnings are recorded, not repaired by this frontend task. |

## Bounded review rubric

These separate weighted ratings are **internal agent heuristics for this local native-foundation slice**, not user research, product-readiness percentages or acceptance. No aggregate release score is assigned.

| Dimension / weight | Local rating | Confidence and limitation |
|---|---|---|
| Task completion /25 |18/25 | Native list/frame/resource and return paths implemented; complete fault-to-fix and real file operations remain outside this evidence. |
| Role/journey coverage /20 |10/20 | Reader/editor fixtures and normal-user stored-role/grant backend tests; actual personas and deployed journey not accepted. |
| Safety/evidence clarity /15 |12/15 | Exact owner, no duplicate completion authority, qualified diagnosis and explicit no-operating-permission copy; domain review open. |
| Visual hierarchy/usability /15 |12/15 | Actual light/dark and desktop/tablet/phone review; dense technical tables deliberately scroll locally on phones. |
| Runtime reliability /15 |12/15 | Relevant types/units/browser/backend checks; deployed concurrency, storage and load are unverified. |
| Accessibility/responsiveness /10 |6/10 | Keyboard, focus, long content and containment checks; no full assistive-technology/contrast/zoom audit. |

The fastest high-impact implementation improvements are owner-locked resources, pointer-reachable phone file actions, viewport-contained safety summaries, retained native panel effects/drafts and readable priority labels. All five have their stated source/fixture verification; human acceptance is separate. Next contract work is the agreed exact assistant context, actual persona/attention mapping and an approved real-file/domain/human validation session. None is resolved by marking a dependent parent Done.
