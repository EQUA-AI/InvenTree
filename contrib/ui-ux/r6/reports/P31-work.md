# P31 — responsive and accessibility pass, Work surfaces (part A)

Status: **built and tested — P31 Work part A, including O12 D1–D9 corrections**. Current follow-up checks are recorded below; the initial-pass evidence is retained separately as history. This is fixture evidence, not R6, deployment, physical-authority, hardware or accessibility certification. Machine/station surfaces belong to part B and are unchanged.

Checkout: `/Users/Aniketkumar/IdeaProjects/InvenTree-r6/p31-work`, base `873a104e4`, branch `r6/p31-work`. Evidence: `data/devscratch/r6-s8/P31-work/`. Changes remain uncommitted for coordinator review. No git writes, dependency installation/download, backend change, Azure access/write/startup or shared-ledger edit.

## Screen definition and native mapping

Applies the master design prompt and designprompt1 within the existing React, Mantine and InvenTree architecture. Primary users are maintenance technicians, engineers and supervisors. The task is to inspect and operate the existing Work record, requests, evidence and maintenance-account controls at desktop/phone widths using a pointer or keyboard. Existing overview, request, attachment and Closeout providers remain authoritative; unknown physical authority, operating state, source quality and unavailable criteria remain explicitly unknown. No new data contract or endpoint is needed.

Scope: My work / All work; Work, Diagnose, Plan & Safety, Parts, Verify & Return, Closeout and History; shared Work attachments/preview; manual investigation observation recovery; prepared account/report and final update; the existing assistant opened from Work; P47's phone companion dock. Native Mantine components, permissions, mounted-panel drafts, internal-update semantics and separate packet completion remain intact.

## Built

- Named main landmarks and a level-one page heading, with contiguous section/subsection heading levels and native typography preserved.
- Work-scoped wrapping for status badges and action labels, with explicit visible focus. Work-list titles are fully available in the table instead of a two-line clamp.
- Focus-scroll clearance scales with control text on desktop as well as phone, keeping the Work back control below the sticky global header at 200% text.
- Labelled native scroll groups for Work tables, focusable only while content overflows, with an inset ring that survives ancestor clipping. The shared table adapter gains optional scroll-viewport and static row-action-heading props; only Work-owned Attachments opt into them. The decorative, unnamed action-column button is replaced with a visible static “Actions” heading for Work attachments. Its width follows text size, using the native DataTable CSS-length width contract. Work preview tooltips wrap within the viewport while retaining the full filename. Other model tables retain their existing rendering.
- Focus recovery when final update, manual observation, hold/resume, changed-condition, reading entry or Closeout story/review/material commands replace or disable their focused button. Recovery stays at the labelled result group for the operation; a user who has moved to another control keeps focus there.
- Escape closes manual observation recovery, restores the disclosure trigger and retains its local draft.
- The existing assistant's native context disclosure retains the complete Work title, including when no request ID is supplied. Workspace and list reserve measured desktop drawer space while about 600px remains for Work; otherwise the native modal overlay isolates the background. Saved widths are clamped during rendering. The Work-context assistant can scroll vertically to its header and composer at enlarged text sizes.
- P47's phone dock retains its existing navigation and adds clearance for keyboard-focused native disclosure summaries.
- S8 discovery/configuration, responsive fixtures and deterministic overflow, clipping, covered-action, keyboard/focus and accessible-name checks.

## Review findings and dispositions

O12 review follow-up is limited to D1–D9. Broader hierarchy, contrast-token, provider/honesty and routine-work uplift remains assigned to the integrated line. The guarded replay below checks the corrections independently of the initial results.

| Finding | Root correction | Regression coverage |
| --- | --- | --- |
| D1 | Native table viewports use an inset focus ring and are Tab stops only during real overflow. They are labelled groups, not extra landmarks. Resize/content observers update local overflow state. The shared adapter forwards DataTable's supported viewport ref because DataTable replaces `ScrollArea.viewportRef` internally. | All responsive workspace/list views check overflow versus tabindex; outline strokes are intersected with every ancestor clip. Work traversal now checks scroll stops as well as controls. |
| D2 | Translated assistant/back, table-control and pagination names; identifiers move to `data-testid`. Native refresh/filter/export handlers are on the button so keyboard activation works. The phone back control is 44px. Existing assistant selectors migrate to hooks without changing identity, permission, draft or send assertions. | Visible control names reject raw kebab-case identifiers; phone touch bounds and existing affected suites retain their behavior assertions. |
| D3 | Full-width composer text above a wrapping control row; send has an accessible name without an occluding tooltip. Full Work context uses a native keyboard disclosure, keeping the complete title available without occupying the composer. | Every assistant width/theme checks textbox width, control position, complete disclosed title, focus return, and tooltip intersection with typed text and the physical-authority boundary. |
| D4 | Form Escape ignores the active combobox/popover event origin; the dropdown gets its own dismissal first. | Native attachment dropdown Escape retains the observation draft and focus; a second Escape closes only the form and restores its trigger. |
| D5 | Wrapped Work badges use the native small radius. | Character rectangles are checked against the actual rounded root clip, including constrained multiline badges in both themes. |
| D6 | Saved width and announced splitter bounds use the same viewport clamp during rendering; Work keeps at least 600px before reserving space. Below that width the existing assistant becomes a modal overlay. Clearance is a DOM CSS-variable update, also on the Work list. | Saved 900px drawer at 1000/1100px on workspace/list; native keyboard resize restores reserved layout; background isolation, close-focus return and announced values within clamped bounds at every assistant size/theme. |
| D7 | Only explicit command handlers arm focus recovery; the operation is retired after recovery/completion. A later unrelated state/storage change cannot reuse its control. | Completed hold followed by another-tab storage event with body focus; existing command success/refusal/pending focus cases retained. |
| D8 | Recovery targets and scroll viewports are non-landmark groups. Removed redundant live wrappers/attributes around native alerts. | Command result is one labelled group, with one native alert channel and no live ancestor; existing result focus tests use the corrected group semantics. |
| D9 | Scrolling belongs to `AssistantSurface`'s content box. The resize handle is a fixed-layer sibling. Removed route-global `!important` drawer overrides. | At 200% text, content scrolls while the handle keeps its viewport rectangle; keyboard resizing and Escape return still work. |

## O12 follow-up verification

D1–D9 are **built and tested on the guarded native fixtures**. No broader Work redesign was started. Every S8 assertion from the initial pass is retained; new assertions cover clipped focus strokes, real overflow, raw accessible identifiers, rounded badge roots, tooltip intersections, operation-specific recovery and modal width fallback.

The D1 attachment probe exposed a DataTable ref override; the adapter now forwards the supported viewport ref. The stricter D2 checks also exposed native table-control and pagination names. The S1 replay found a missed row-menu selector migration; it and the reader's zero-menu assertion now use the stable test hook. This changes selectors while preserving upload/edit/delete permissions and behavior assertions. Other legacy table/chat selectors were audited for the same migration.

| Check on the corrected source | Actual result |
| --- | --- |
| Default + all sprint TypeScript | **9 type project(s), no diagnostics**, `review-types-bounds-final.log` |
| Units | **620 tests / 61 files passed**, `review-units-bounds-final.log` |
| S8 P31 | **89 passed**, `review-s8-green.log` |
| S8 final assistant/saved-width/scroll replay | **17 passed**, `review-bounds-final.log` |
| S1 corrected native attachment spec alone | **18 passed**, `review-native-attachments-replay.log` |
| S4 full suite | **158 passed**, `review-s4-green.log` |
| S6 full suite | **122 passed**, `review-s6-green.log` |
| S7 full suite | **98 passed**, `review-s7-bounds-final.log` |
| S3 Work-history selector compatibility | **46 passed**, `review-s3-green.log` |
| Native assistant layout/voice/reflow fixtures | **25 passed**, `review-chat-bounds-final.log` |

S1's seven affected specs originally returned **2 failed; 144 passed**. Both timeouts used a stale `row-action-menu` accessible-name selector; no assertions were waived for load. The full corrected attachment spec passed alone in both projects (18 cases); the other S1 cases passed without production changes. Together the replays cover all 146 distinct S1 cases. The original failed log and trace remain available.

The final production delta after the broad replay is exactly two assistant ARIA bounds: they now use the same viewport clamp as the rendered value. The strengthened 360px case failed against the preceding bundle (`328 < 340`); **17 final assistant/saved-width/scroll cases passed** after the correction. S7, the native assistant fixtures, all nine type projects and units were then replayed on the final source. **27 other production/config hashes stayed identical** (`review-bounds-delta.log`); the final manifest contains **67 source/test/config hashes**, and **66 active container files** match the final host bundle. This is incremental verification; the historical failed probes are not acceptance.

Host hooks: **6 applicable hooks passed** on all 68 changed files. `git diff --check` passed with no errors (`review-diff-final.log`). Cleanup: `docker stop r6-p31-browser-15232` passed (`review-browser-stop.log`), stopping only this run's browser and fixture server. All changes remain uncommitted.

Browser renders came from **Linux Chromium inside this run's dedicated `r6-p31-browser-15232` container**, using WS 15232 and harness 15222. The local native fixture bundle, synthetic API routes and request guards were retained. No backend or database application was started. The dedicated container was restarted for this follow-up; only that container is stopped during cleanup.

The updated visual review is separate from the historical initial evidence. Each row below includes seven panels, both list scopes, investigation/manual capture, Closeout account/recovery/action, attachment preview and the existing Work assistant. No page overflow or hidden target action was found by these rendered checks. Existing supplied units, criteria and state text remain available through wrapping and native scroll/disclosure controls. At enlarged text, the assistant header and composer are reachable by scrolling; the resize handle remains fixed. The full Work title is available through its keyboard disclosure, with the default context kept compact.

| Width / theme | Inspected panel crops | Inspected forms, list, account and assistant | Inspected attachment focus/columns |
| --- | --- | --- | --- |
| 1100x800 / light | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1100x800-light.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-light-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-light-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-light-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-light-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1100x800-light.png) |
| 1100x800 / dark | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1100x800-dark.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-dark-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-dark-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-dark-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1100x800-dark-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1100x800-dark.png) |
| 1440x900 / light | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1440x900-light.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-light-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-light-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-light-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-light-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1440x900-light.png) |
| 1440x900 / dark | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1440x900-dark.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-dark-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-dark-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-dark-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-dark-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1440x900-dark.png) |
| 1920x1080 / light | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1920x1080-light.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-light-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-light-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-light-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-light-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1920x1080-light.png) |
| 1920x1080 / dark | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1920x1080-dark.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-dark-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-dark-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-dark-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1920x1080-dark-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1920x1080-dark.png) |
| 360x844 / light | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-360x844-light.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-light-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-light-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-light-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-light-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-360x844-light.png) |
| 360x844 / dark | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-360x844-dark.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-dark-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-dark-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-dark-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-360x844-dark-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-360x844-dark.png) |
| 390x844 / light | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-390x844-light.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-light-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-light-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-light-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-light-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-390x844-light.png) |
| 390x844 / dark | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-390x844-dark.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-dark-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-dark-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-dark-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-390x844-dark-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-390x844-dark.png) |
| 1440x900-text200 / light | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1440x900-text200-light.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-light-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-light-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-light-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-light-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1440x900-text200-light.png) |
| 1440x900-text200 / dark | [Seven panels](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/panels-1440x900-text200-dark.png) | [1](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-dark-1.png), [2](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-dark-2.png), [3](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-dark-3.png), [4](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/extras-1440x900-text200-dark-4.png) | [Native viewport](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/attachments-final-1440x900-text200-dark.png) |

**87 inspected sheets reference 304 unique originals**, with hashes in `review-o12-viewed.json`. Phone/200% form/table detail crops and focused badge/900px-drawer/fixed-handle sheets are in `browser/review-o12/`. The review follow-up retains **2302 original browser PNGs** in `review-capture-manifest.json`, including failed probes and regression artifacts; only the inspected subset is claimed as visually reviewed. Historical initial manifests and captures remain unchanged.

Final-bound replay captures were also inspected: [expanded context, light](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/bounds-assistant-initial-light.png), [composer, light](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/bounds-assistant-light.png), [expanded context, dark](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/bounds-assistant-initial-dark.png), [composer, dark](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/bounds-assistant-dark.png), [saved wide drawers](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/bounds-wide.png), [fixed handle](../../../../data/devscratch/r6-s8/P31-work/browser/review-o12/bounds-fixed-handle.png).

Remaining acceptance: screen-reader/assistive-technology sessions, physical devices, connected providers and deployment are not inferred from these native Chromium fixtures. Backend-dependent legacy login/application E2E specs received selector-only compatibility edits but were not run against an application here; CI needs an isolated authorized application fixture for those. No backend field, endpoint or behavior changed, so no Django test was run. P31 part B and the explicitly deferred integrated Work uplift remain separate assignments.

CI additions: discover all four `tests/s8/pui_p31_work_*.spec.ts` files with `playwright.s8.config.ts`, type-check `tsconfig.s8.json`, retain their shared assertion/fixture helpers, and run default/all-sprint types, units and affected S1/S4/S6/S7 suites. Preserve screenshots, traces and failed/replay logs. Also run the selector-only S3/native assistant compatibility specs and legacy application E2E in their existing isolated CI environment. No workflow or shared ledger was edited.

### Final files changed for P31 Work

68 uncommitted files: 29 source/config/report files and 39 test/helper files. Shared production edits are confined to the native assistant, table adapter/header, `RowActions`, `TableColumnSelect` and table types; Work CSS remains local. Machine/station source and shared ledgers are unchanged. Existing test changes outside S8 are selector compatibility only.

```text
contrib/ui-ux/r6/reports/P31-work.md
src/frontend/lib/components/RowActions.tsx
src/frontend/lib/components/TableColumnSelect.tsx
src/frontend/lib/types/Tables.tsx
src/frontend/playwright.s8.config.ts
src/frontend/src/components/aichat/AssistantSurface.tsx
src/frontend/src/components/nav/AIChatDrawer.tsx
src/frontend/src/components/tables/InvenTreeTable.tsx
src/frontend/src/components/tables/InvenTreeTableHeader.tsx
src/frontend/src/components/tables/WorkTableScroll.tsx
src/frontend/src/pages/maintenance/WorkListPanel.tsx
src/frontend/src/pages/maintenance/workorders/components/WorkOrderSafetyReadiness.tsx
src/frontend/src/pages/tasks/workorders/CloseoutPanel.tsx
src/frontend/src/pages/tasks/workorders/CloseoutReconciliation.tsx
src/frontend/src/pages/tasks/workorders/FinalUpdateAction.tsx
src/frontend/src/pages/tasks/workorders/PreparedCloseout.tsx
src/frontend/src/pages/tasks/workorders/WorkAttachmentResource.tsx
src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx
src/frontend/src/pages/tasks/workorders/execution/DiagnoseProjection.tsx
src/frontend/src/pages/tasks/workorders/execution/ExecutionFacts.tsx
src/frontend/src/pages/tasks/workorders/execution/HandledByAimms.tsx
src/frontend/src/pages/tasks/workorders/execution/NativeObservationCapture.tsx
src/frontend/src/pages/tasks/workorders/execution/PreparedPackage.tsx
src/frontend/src/pages/tasks/workorders/execution/WorkContinuity.tsx
src/frontend/src/pages/tasks/workorders/useActionFocus.ts
src/frontend/src/pages/tasks/workorders/useWorkAssistantClearance.ts
src/frontend/src/pages/tasks/workorders/workCompanion.module.css
src/frontend/src/tables/general/AttachmentTable.tsx
src/frontend/tests/helpers.ts
src/frontend/tests/pages/aichat_harness.ts
src/frontend/tests/pages/pui_ai_chat.spec.ts
src/frontend/tests/pages/pui_ai_chat_bulk_delete.spec.ts
src/frontend/tests/pages/pui_ai_chat_drawer_layout.spec.ts
src/frontend/tests/pages/pui_ai_chat_evidence.spec.ts
src/frontend/tests/pages/pui_ai_chat_export.spec.ts
src/frontend/tests/pages/pui_ai_chat_lifecycle.spec.ts
src/frontend/tests/pages/pui_ai_chat_memory.spec.ts
src/frontend/tests/pages/pui_ai_chat_thread_state.spec.ts
src/frontend/tests/pages/pui_ai_chat_xss.spec.ts
src/frontend/tests/pages/pui_build.spec.ts
src/frontend/tests/pages/pui_part.spec.ts
src/frontend/tests/pages/pui_purchasing.spec.ts
src/frontend/tests/pages/pui_sales.spec.ts
src/frontend/tests/pages/pui_ui_reflow.spec.ts
src/frontend/tests/pages/pui_voice_decisions.spec.ts
src/frontend/tests/pages/pui_voice_live.spec.ts
src/frontend/tests/pui_ai_chat.spec.ts
src/frontend/tests/pui_exporting.spec.ts
src/frontend/tests/pui_importing.spec.ts
src/frontend/tests/pui_settings.spec.ts
src/frontend/tests/pui_tables.spec.ts
src/frontend/tests/s1/pui_assistant_context.spec.ts
src/frontend/tests/s1/pui_native_attachments.spec.ts
src/frontend/tests/s1/pui_work_assistant.spec.ts
src/frontend/tests/s1/pui_work_attachments.spec.ts
src/frontend/tests/s1/pui_work_list.spec.ts
src/frontend/tests/s3/pui_machine_work_history.spec.ts
src/frontend/tests/s4/pui_work_continuity.spec.ts
src/frontend/tests/s4/pui_work_investigation.spec.ts
src/frontend/tests/s7/pui_attachment_recovery.spec.ts
src/frontend/tests/s7/pui_phone_companion.spec.ts
src/frontend/tests/s8/pui_p31_work_commands.spec.ts
src/frontend/tests/s8/pui_p31_work_keyboard.spec.ts
src/frontend/tests/s8/pui_p31_work_reflow.spec.ts
src/frontend/tests/s8/pui_p31_work_review.spec.ts
src/frontend/tests/s8/workAccessibilityAssertions.ts
src/frontend/tests/s8/workAccessibilityFixtures.ts
src/frontend/tsconfig.s8.json
```


## Findings, captures and verification (initial pass)

Implementation, targeted replays and required visual review are complete. Literal validation commands, exits, durations and log paths are retained in `data/devscratch/r6-s8/P31-work/commands.jsonl`. Failed/interrupted harness attempts are retained separately and are not acceptance.

Confirmed pre-fix findings, retained with failing assertions/captures:

- Final-update confirmed/refused/uncertain outcomes at 1440/390 lost focus when the action changed; native hold, reading and changed-condition saves had the same failure (`audit-bundle`, `commands-red`, `deviation-red-valid`). The first changed-condition probe lacked the required editor capability; the corrected fixture reproduced the actual focus defect.
- Phone keyboard traversal focused the material-custody disclosure behind the dock (`keyboard-first`, 13 passing / 1 failing). Native `summary` elements now receive the same measured dock clearance as other actions.
- Supplemental full keyboard traversal at 200% text found the back control behind the sticky global header after switching to Closeout (`s8-text-keyboard`). Work focus-scroll clearance now applies at every width and scales with text size; this is a CSS-only adjustment.
- An additional runtime check found the native attachment action-column heading was an unnamed, nonfunctional button (`s8-attachment-keyboard`, 3 failing cases). Work attachments now opt into a static text heading; final follow-up covers every width, both themes, 200% text, button/checkbox names, horizontal keyboard scrolling and preview clickability.
- Hovering the long attachment preview name widened the 360px page to 585px (`s8-attachment-complete`). The native Work-only preview tooltip now wraps within the viewport; the check explicitly waits for the complete tooltip before asserting document width.
- Wrapped phone action labels clipped their text vertically (`refined-smoke`); explicit label height, overflow and line height fix the native Button metrics without hiding text.
- The long Work title was truncated in the phone assistant (`assistant-red`). Work-context text now wraps fully.
- The desktop assistant covered the right side of Closeout's full-width final action at both normal and 200% text (`assistant-clearance-probe`). Enlarged assistant context also clipped inside a pill, and its header could be stranded above an unscrollable container. Work now reserves the measured drawer width, the context badge uses the native small radius, and its outer container scrolls vertically.

Earlier host Vite/tunnel setup failures, interrupted runs and initial formatting/type errors are retained; none count as final acceptance.

Browser renders use **Linux Chromium in the dedicated `r6-p31-browser-15232` Docker container**, connected through WS port 15232. The fixture build uses the native `s0-native` and `aichat-drawer` entries with `NODE_ENV=development`, no dotenv/proxy/backend, existing API/external-request guards and synthetic records. A copied fixture bundle is served within that same container on harness port 15222 to avoid slow host-tunnel asset transport. No Django/API application is started. This is not deployment or measured device-performance acceptance.

## Open acceptance and CI

P31 as a whole still requires the separately assigned machine/station pass, original prerequisite/provider/hardware gates and connected release verification. Screen-reader/assistive-technology and physical phone/glasses acceptance are not inferred from Chromium fixtures. The text enlargement fixture doubles every rendered element's computed font size, including fixed-pixel text. It restores each original inline value before measuring newly mounted panels and overlays, preventing compounded enlargement. This is 200% text enlargement at a 1440px layout viewport; browser page zoom and assistive-technology acceptance are separate.

CI should add `playwright.s8.config.ts`, `tsconfig.s8.json` and all four `tests/s8/pui_p31_work_*.spec.ts` specs and their assertions/fixture helpers, and retain default/all sprint types, units and the affected S1/S4/S6/S7 Work suites. Keep pending/refused/uncertain command behavior, draft preservation, native attachment ownership and preview return, packet completion separation and P47 number-input/dock regressions.

## Surface findings and rendered matrix

Every matrix row below renders the seven panels, both list scopes, the investigation draft, changed-condition entry, saved criteria/attempt disclosure, Closeout report and its three recovery steps, attachment preview and the existing assistant. Native attachment supplements use the final static heading and bounded tooltip. The fixture deliberately supplies a long Work title, equipment reference and filename. No information was removed to obtain reflow.

| Surface | Finding and final behavior |
| --- | --- |
| My work / All work | Removed the title clamp; full title and equipment reference remain available. Named main/h1 and keyboard-scrollable table. Phone filter dialog has a named close control and returns focus. |
| Work | Wrapped badges/actions retain Board stage, lifecycle and priority text. Hold/resume results recover focus without moving a user who left the command. |
| Diagnose / investigation forms | Required unit (`mm/s`), conditions, saved identity and unresolved state remain readable. Manual observation closes with Escape, retains its draft, restores its trigger and focuses the result after saving. |
| Plan & Safety | Saved unit/minimum/maximum remain visible when opening sourced criteria with the keyboard. Changed-condition results retain the draft on refusal and recover focus. Wide dependencies/safety tables keep all columns in named scroll viewports. |
| Parts | Native required-parts table remains horizontally scrollable on phones. The unavailable detailed material provider stays explicitly unavailable; no stock availability or physical permission is inferred. |
| Verify & Return | Supplied numeric zero remains `0 mm/s`, alongside saved minimum `0`, maximum `4`, retained failed attempt and explicit missing authority/conditions. Reading-entry saves/refusals recover focus. |
| Closeout account / report / recovery | Report and story/review/material forms reflow; source and confidence labels remain text. Native recovery commands focus their result region. Unknown downtime stays blank with its existing explanation. |
| Final update | Full button is reachable above the phone dock and beside the desktop assistant. Confirmed, refused and uncertain outcomes retain focus at the maintenance-record result; pending completion does not steal focus from a later action. Internal record update remains separate from physical return. |
| Attachments | Named native viewport supports keyboard horizontal scrolling; all columns remain present. Work-only static Actions heading removes the unnamed no-op button. Full preview filename wraps inside the viewport. Preview and resource closing restore focus. |
| Existing Work assistant | Complete Work context wraps in both themes; desktop measured clearance follows native keyboard resizing. At 200% text the header and composer remain reachable through vertical scrolling; Escape returns to the Work trigger. |
| P47 phone companion dock | Existing navigation/authority wording is retained. Native disclosure summaries now receive measured dock clearance. Full Work keyboard traversal passed at 1440 and 390px and at 200% text in both themes, including clearance beneath the global header. |

The criterion/reading values above are explicitly synthetic saved fixture data, not equipment thresholds or measurements. These Work targets have no chart that needs a new narrow-width substitute; native table scroll disclosure retains the columns.

| Viewport / theme | Seven panels | Forms / account / list / assistant | Final native attachment view | Result |
| --- | --- | --- | --- | --- |
| 1100x800 / light | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1100x800-light.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-light-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-light-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-light-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-light-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1100x800-light.png) | Passed |
| 1100x800 / dark | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1100x800-dark.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-dark-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-dark-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-dark-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1100x800-dark-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1100x800-dark.png) | Passed |
| 1440x900 / light | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1440x900-light.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-light-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-light-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-light-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-light-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1440x900-light.png) | Passed |
| 1440x900 / dark | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1440x900-dark.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-dark-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-dark-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-dark-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-dark-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1440x900-dark.png) | Passed |
| 1920x1080 / light | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1920x1080-light.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-light-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-light-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-light-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-light-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1920x1080-light.png) | Passed |
| 1920x1080 / dark | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1920x1080-dark.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-dark-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-dark-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-dark-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1920x1080-dark-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1920x1080-dark.png) | Passed; standalone unchanged replay after a load timeout |
| 360x844 / light | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-360x844-light.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-light-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-light-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-light-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-light-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-360x844-light.png) | Passed |
| 360x844 / dark | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-360x844-dark.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-dark-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-dark-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-dark-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-360x844-dark-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-360x844-dark.png) | Passed |
| 390x844 / light | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-390x844-light.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-light-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-light-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-light-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-light-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-390x844-light.png) | Passed |
| 390x844 / dark | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-390x844-dark.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-dark-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-dark-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-dark-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-390x844-dark-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-390x844-dark.png) | Passed |
| 1440x900-text200 / light | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1440x900-text200-light.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-light-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-light-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-light-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-light-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1440x900-text200-light.png) | Passed |
| 1440x900-text200 / dark | [Panel crops](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/panels-1440x900-text200-dark.png) | [Form/account](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-dark-1.png), [preview/action](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-dark-2.png), [list/assistant](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-dark-3.png), [drawer controls](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/extras-1440x900-text200-dark-4.png) | [Columns and tooltip](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/attachments-final-1440x900-text200-dark.png) | Passed |

All linked sheets were inspected with the image viewer, supplemented by full-resolution viewport captures, the 1100px title crop, and phone/200% form/table detail crops. Original PNG paths are mapped by `review-sheets.json`, `review-details.json` and `review-attachments.json`. Full-page screenshots include fixture controls and place fixed header/dock at the capture scroll position; the viewport `action-*`, `assistant-*` and final attachment shots establish overlay clearance. The 80 inspected review sheets reference 282 unique original PNGs. The supplemental [200% light focus](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/focus-header-text200-light.png) and [dark focus](../../../../data/devscratch/r6-s8/P31-work/browser/review-final/focus-header-text200-dark.png) sheets contain all seven panels after the final focus-clearance fix. Sheet and original hashes are recorded in `review-manifest.json` and `capture-manifest.json`.

## Files changed

Shared changes are limited to the existing assistant Work badge, the optional native table scroll-viewport/static-heading props, the native CSS-length width type and their Work attachment opt-in. All other changes are Work-owned components/styles, the two hooks, S8 fixtures/specs/configuration and this report. No machine/station file or shared ledger changes.

- `contrib/ui-ux/r6/reports/P31-work.md`
- `src/frontend/lib/types/Tables.tsx`
- `src/frontend/playwright.s8.config.ts`
- `src/frontend/src/components/nav/AIChatDrawer.tsx`
- `src/frontend/src/components/tables/InvenTreeTable.tsx`
- `src/frontend/src/pages/maintenance/WorkListPanel.tsx`
- `src/frontend/src/pages/maintenance/workorders/components/WorkOrderSafetyReadiness.tsx`
- `src/frontend/src/pages/tasks/workorders/CloseoutPanel.tsx`
- `src/frontend/src/pages/tasks/workorders/CloseoutReconciliation.tsx`
- `src/frontend/src/pages/tasks/workorders/FinalUpdateAction.tsx`
- `src/frontend/src/pages/tasks/workorders/PreparedCloseout.tsx`
- `src/frontend/src/pages/tasks/workorders/WorkAttachmentResource.tsx`
- `src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/DiagnoseProjection.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/ExecutionFacts.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/HandledByAimms.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/NativeObservationCapture.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/PreparedPackage.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/WorkContinuity.tsx`
- `src/frontend/src/pages/tasks/workorders/useActionFocus.ts`
- `src/frontend/src/pages/tasks/workorders/useWorkAssistantClearance.ts`
- `src/frontend/src/pages/tasks/workorders/workCompanion.module.css`
- `src/frontend/src/tables/general/AttachmentTable.tsx`
- `src/frontend/tests/s8/pui_p31_work_commands.spec.ts`
- `src/frontend/tests/s8/pui_p31_work_keyboard.spec.ts`
- `src/frontend/tests/s8/pui_p31_work_reflow.spec.ts`
- `src/frontend/tests/s8/workAccessibilityFixtures.ts`
- `src/frontend/tsconfig.s8.json`

## Initial pass checks (before O12 review)

| Check | Result / evidence |
| --- | --- |
| Default TypeScript + every `tsconfig.s*.json` | **9/9 projects passed**: default, S0, S1, S2, S3, S4, S6, S7, S8 (`types-sealed`). The final S8 test additions were rechecked afterward (`types-s8-focus-themes`, no diagnostics). There is no S5 tsconfig in this checkout. |
| Units | **620/620 tests in 61 files passed** on the final source (`units-focus-final`). |
| New S8 set | **79 unique cases passed across targeted runs**: 36 responsive/assistant/list, 28 keyboard/table, 15 native-command recovery. `resumed-focus`: 18 passed; `s8-final`: 49 passed / one timeout; the unchanged timeout case passed alone in `s8-reflow-retry`; `s8-attachment-sealed`: 12 passed; final normal/200% keyboard traversal: 4 passed. Overlaps are deduplicated in `coverage.json`. |
| Existing S1, selected Work/assistant/native attachment/list/material specs | **146/146 passed**, desktop + phone (`s1-final`). Seven spec files; exact selection below. |
| Existing full S4 | **158/158 passed**, desktop + phone on final CSS (`s4-css-final`). The earlier run had 157 passes / one 120-second timeout; its unchanged continuity spec replay passed 16/16, and the final full replay then passed. Both earlier logs and the timeout capture remain. |
| Existing full S6 | **122/122 passed**, desktop + phone (`s6-final`). |
| Existing full S7 | **98/98 passed**, desktop + phone (`s7-final`). |
| Fixture build / browser runtime | **9,772 modules built** (`fixture-focus-header`); **66 active container files matched host hashes** (`fixture-container-hashes`). |
| Source and reviewed artifacts | **27 source/test/config hashes unchanged; 80 inspected sheet hashes verified** (`source-unchanged-final`). |
| Host format / lint | **6 applicable hooks passed** on all 28 changed files (`prek-final-clean`); other hooks had no matching files. The preceding EOF-only report correction is retained as a failed formatting attempt. |
| Whitespace | **0 errors**, `git diff --check` (`diff-final`). Post-hook verification retained the same 27 source/test/config hashes and 80 reviewed-sheet hashes. |
| Backend | No backend fields, endpoints or semantics changed; no Django run required or performed. No application/backend or Azure database was started or accessed. |

That is 603 distinct passing browser cases across these sprint/project sets. It is incremental native-fixture verification: pre-fix, interrupted and timed-out runs below remain failures/incomplete runs, not acceptance. The original 1920px dark workspace timeout was replayed alone unchanged; the final attachment and focus supplements cover the subsequent presentation fixes. The final CSS-only focus adjustment was followed by all four Work traversal cases and the full affected S1/S4/S6/S7 replay.

Capture inventory: **1,952 original browser PNG files**, including retained failed attempts and replays, hashed in `capture-manifest.json`; review crops are excluded from that count. **80 inspected review sheets reference 282 unique originals** in `review-manifest.json`. The other regression images are retained test artifacts; this report does not claim that every one of the 1,952 files was visually reviewed. All required dimensions/themes and 200% text are covered by the linked matrix and supplements above.

Cleanup: `docker stop r6-p31-browser-15232` passed, stopping this run's Linux Chromium and fixture server. No other container was changed. All 28 changed files remain uncommitted; no shared ledger or workflow was edited.

Recommended CI additions (not extra commands executed here): add the native S8 browser job with `AIMMS_HARNESS_PORT=<job-port> node node_modules/@playwright/test/cli.js test --config=playwright.s8.config.ts --reporter=list tests/s8/` and `node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json`, alongside the existing default/all-sprint type checks, unit job and affected Work regressions. Keep the screenshot/trace artifacts and standalone timeout replay logs. Run the repo host hooks on the changed files; do not add container Ruff or a database dependency to this presentation pass.

The fixture setup used the requested preinstalled browser image and ports:

```sh
docker run -d --rm --pull never --name r6-p31-browser-15232 -p 127.0.0.1:15232:3000 --mount type=bind,src=/Users/Aniketkumar/IdeaProjects/InvenTree-r6/p31-work/src/frontend/node_modules,dst=/frontend/node_modules,readonly --entrypoint node ghcr.io/browserless/chromium:latest /frontend/node_modules/playwright/cli.js run-server --port 3000 --host 0.0.0.0 --path /r6-p31 --max-clients 1 --unsafe
docker cp data/devscratch/r6-s8/P31-work/fixture-bundle/. r6-p31-browser-15232:/tmp/r6-p31-fixtures/fixture-bundle/
```

The first command started one dedicated server; subsequent fixture builds were copied into that same container. `serve_fixture.mjs`, `vite.bundle.config.mts` and the saved per-run browser configs record the local-only fixture transport. The renderer was Linux Chromium; no macOS Chromium or physical device render is claimed. The final stop command and literal validation commands/results follow.

## Every recorded validation command

Commands below are literal shell strings passed to `check.py`, run from this checkout. Logs and the JSON journal live in `data/devscratch/r6-s8/P31-work/`. Exit 130 means an intentionally stopped/incomplete run; formatting failures and pre-fix failures are not acceptance.

| Run / log | Exit | Actual result |
| --- | ---: | --- |
| `audit-smoke.log` | 1 | 0 tests: scratch discovery configuration failed |
| `audit-smoke-real.log` | 1 | 1 failed |
| `fixture-build-audit.log` | 0 | Fixture build passed, 9770 modules |
| `prek-initial.log` | 1 | Formatting fixed 12 files |
| `types-initial.log` | 2 | 1 type-only enum import error; subsequently fixed |
| `audit-bundle.log` | 130 | 7 failures (6 command-focus defects, 1 timeout); interrupted before completion |
| `prek-second.log` | 0 | Hooks passed |
| `fixture-build-fixed.log` | 0 | Fixture build passed, 9771 modules |
| `fixed-smoke.log` | 1 | 0 tests: host harness port already occupied |
| `units.log` | 0 | Test Files  61 passed (61); Tests  620 passed (620) |
| `fixed-smoke-ipv6.log` | 130 | 2 transport failures; interrupted |
| `fixed-smoke-loopback2.log` | 1 | 0 tests: macOS rejected alternate loopback bind |
| `fixed-smoke-localhost.log` | 0 | 1 passed (1.1m) |
| `s8-focused.log` | 130 | 2 passed; interrupted to move fixture transport into own container |
| `types-all.log` | 0 | 9 type project(s), no diagnostics |
| `container-smoke.log` | 0 | 1 passed (30.7s) |
| `assistant-red.log` | 1 | 1 failed |
| `prek-third.log` | 1 | Formatting fixed 2 files; 1 lint error, subsequently fixed |
| `fixture-build-refined.log` | 0 | Fixture build passed, 9771 modules |
| `keyboard-first.log` | 1 | 1 failed; 13 passed (5.8m) |
| `prek-refined.log` | 0 | Hooks passed |
| `fixture-build-candidate.log` | 0 | Fixture build passed, 9771 modules |
| `types-final.log` | 130 | 4 projects completed; interrupted during S3 to finish edits |
| `commands-red.log` | 1 | 3 failed |
| `prek-command-focus.log` | 1 | Formatting fixed 5 files |
| `deviation-red-valid.log` | 1 | 1 failed |
| `prek-candidate.log` | 0 | Hooks passed |
| `types-candidate.log` | 0 | 1 type project(s), no diagnostics |
| `refined-smoke.log` | 1 | 1 failed; 3 passed (2.5m) |
| `prek-text-zoom.log` | 0 | Hooks passed |
| `fixture-watch.log` | 130 | Fixture build completed; watch process stopped (130) |
| `assistant-clearance-probe.log` | 1 | 2 failed |
| `types-final-complete.log` | 0 | 9 type project(s), no diagnostics |
| `prek-resumed.log` | 1 | Formatting fixed 2 files |
| `types-resumed.log` | 130 | Interrupted during default project to serialize memory-heavy checks |
| `prek-before-browser.log` | 0 | Hooks passed |
| `fixture-resumed.log` | 130 | Fixture build completed: 9,772 modules; watch process then stopped (130) |
| `prek-specs.log` | 0 | Hooks passed |
| `resumed-focus.log` | 0 | 18 passed (8.5m) |
| `types-final-source.log` | 0 | 9 type project(s), no diagnostics |
| `s8-final.log` | 1 | 1 failed; 49 passed (31.1m) |
| `prek-attachment-spec.log` | 0 | Hooks passed |
| `types-s8-supplement.log` | 0 | 1 type project(s), no diagnostics |
| `s8-reflow-retry.log` | 0 | 1 passed (1.1m) |
| `s8-attachment-keyboard.log` | 1 | 3 failed |
| `prek-static-heading.log` | 0 | Hooks passed |
| `fixture-static-heading.log` | 0 | Fixture build passed, 9772 modules |
| `prek-preview-spec.log` | 0 | Hooks passed |
| `types-heading-final.log` | 2 | Default project: 1 CSS-length width type error; subsequently fixed |
| `s8-attachment-final.log` | 130 | 3 passed / 1 loading-state failure; interrupted during the next case. Explicit fixture-settle wait added |
| `prek-table-types.log` | 0 | Hooks passed |
| `s8-attachment-complete.log` | 1 | 6 passed / 1 tooltip overflow failure / 5 not run; maximum-failure outer error. Work-only tooltip fixed |
| `types-table-final.log` | 0 | 9 type project(s), no diagnostics |
| `units-final.log` | 0 | Test Files  61 passed (61); Tests  620 passed (620) |
| `prek-preview-tooltip.log` | 1 | Formatting fixed 1 file; subsequent clean hook run passed |
| `prek-tooltip-clean.log` | 0 | Hooks passed |
| `prek-tooltip-check.log` | 0 | Hooks passed |
| `fixture-tooltip.log` | 0 | Fixture build passed, 9772 modules |
| `prek-final-tests.log` | 0 | Hooks passed |
| `s8-attachment-bounded.log` | 1 | 1 passed / 1 keyboard scroll timing failure / 10 not run; maximum-failure outer error. Refocus before ArrowRight; standalone and final12 passed |
| `prek-scroll-extents.log` | 0 | Hooks passed |
| `s8-scroll-probe.log` | 0 | 1 passed (1.9m) |
| `types-sealed.log` | 0 | 9 type project(s), no diagnostics |
| `s8-attachment-sealed.log` | 0 | 12 passed (7.2m) |
| `prek-text-traversal.log` | 0 | Hooks passed |
| `types-s8-text-final.log` | 0 | 1 type project(s), no diagnostics |
| `s4-final.log` | 1 | 1 failed; 157 passed (33.8m) |
| `s4-continuity-replay.log` | 0 | 16 passed (2.7m) |
| `s8-text-keyboard.log` | 1 | 1 failed |
| `prek-focus-header.log` | 0 | Hooks passed |
| `types-s8-focus-header.log` | 0 | 1 type project(s), no diagnostics |
| `fixture-focus-header.log` | 0 | Fixture build passed, 9772 modules |
| `prek-focus-captures.log` | 0 | Hooks passed |
| `types-s8-focus-themes.log` | 0 | 1 type project(s), no diagnostics |
| `units-focus-final.log` | 0 | Test Files  61 passed (61); Tests  620 passed (620) |
| `s8-focus-final.log` | 0 | 4 passed (1.8m) |
| `s6-final.log` | 0 | 122 passed (7.5m) |
| `s7-final.log` | 0 | 98 passed (9.1m) |
| `source-freeze-final.log` | 0 | 27 source/test/config files and 66 fixture files hashed |
| `fixture-container-hashes.log` | 0 | 66 active fixture files match host hashes |
| `s1-final.log` | 0 | 146 passed (7.9m) |
| `focus-review-artifacts.log` | 0 | 2 inspected review sheets from 14 original focus captures |
| `s4-css-final.log` | 0 | 158 passed (5.1m) |
| `coverage-final.log` | 0 | 603 unique passing browser cases: S8 79, S4 158, S6 122, S7 98, S1 146 |
| `source-unchanged-final.log` | 0 | 27 source/test/config hashes unchanged; 80 inspected review sheet hashes verified |
| `capture-manifest-final.log` | 0 | 1952 original browser PNG files hashed; review sheets excluded |
| `browser-stop.log` | 0 | 1 own browser/fixture container stopped |
| `prek-final-report.log` | 1 | EOF hook fixed one extra trailing newline in the report; code hooks passed |
| `prek-final-clean.log` | 0 | Hooks passed |
| `diff-final.log` | 0 | 0 whitespace errors |
| `source-post-hooks.log` | 0 | 27 source/test/config hashes unchanged; 80 inspected review sheet hashes verified |

**audit-smoke** — 0 tests: scratch discovery configuration failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep 'workspace.*1100x800-light'
```

**audit-smoke-real** — 1 failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep 'workspace.*1100x800-light'
```

**fixture-build-audit** — Fixture build passed, 9770 modules; exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**prek-initial** — Formatting fixed 12 files; exit 1.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-initial** — 1 type-only enum import error; subsequently fixed; exit 2.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

**audit-bundle** — 7 failures (6 command-focus defects, 1 timeout); interrupted before completion; exit 130.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=audit-bundle node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/ --grep "final command|workspace.*(1100x800-light|390x844-dark|1440x900-text200)"
```

**prek-second** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**fixture-build-fixed** — Fixture build passed, 9771 modules; exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**fixed-smoke** — 0 tests: host harness port already occupied; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=fixed-smoke node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/ --grep "final command|workspace.*390x844-dark|existing Work assistant.*text200-light|My work.*text200-light"
```

**units** — Test Files  61 passed (61); Tests  620 passed (620); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" node node_modules/vitest/vitest.mjs run --maxWorkers=1 --minWorkers=1
```

**fixed-smoke-ipv6** — 2 transport failures; interrupted; exit 130.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=fixed-smoke-ipv6 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/ --grep "final command|workspace.*390x844-dark|existing Work assistant.*text200-light|My work.*text200-light"
```

**fixed-smoke-loopback2** — 0 tests: macOS rejected alternate loopback bind; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=fixed-smoke-loopback2 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "final command.*confirmed at 1440px"
```

**fixed-smoke-localhost** — 1 passed (1.1m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=fixed-smoke-localhost node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "final command.*confirmed at 1440px"
```

**s8-focused** — 2 passed; interrupted to move fixture transport into own container; exit 130.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-focused node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts tests/s8/pui_p31_work_reflow.spec.ts --grep "final command|manual observation|workflow arrow|existing Work assistant.*text200-light|My work.*text200-light|workspace.*390x844-dark"
```

**types-all** — 9 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**container-smoke** — 1 passed (30.7s); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=container-smoke node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "final command.*confirmed at 1440px"
```

**assistant-red** — 1 failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=assistant-red node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "existing Work assistant 390x844-dark"
```

**prek-third** — Formatting fixed 2 files; 1 lint error, subsequently fixed; exit 1.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**fixture-build-refined** — Fixture build passed, 9771 modules; exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**keyboard-first** — 1 failed; 13 passed (5.8m); exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=keyboard-first node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts
```

**prek-refined** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**fixture-build-candidate** — Fixture build passed, 9771 modules; exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**types-final** — 4 projects completed; interrupted during S3 to finish edits; exit 130.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**commands-red** — 3 failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=commands-red node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_commands.spec.ts --grep "saved.*390px"
```

**prek-command-focus** — Formatting fixed 5 files; exit 1.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**deviation-red-valid** — 1 failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=deviation-red-valid node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_commands.spec.ts --grep "changed-condition command saved.*390px"
```

**prek-candidate** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-candidate** — 1 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

**refined-smoke** — 1 failed; 3 passed (2.5m); exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=refined-smoke node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "workspace.*390x844-dark|existing Work assistant.*(390x844-dark|text200-light)|My work.*text200-light"
```

**prek-text-zoom** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**fixture-watch** — Fixture build completed; watch process stopped (130); exit 130.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --watch --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**assistant-clearance-probe** — 2 failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=assistant-clearance-probe node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "existing Work assistant.*(1440x900-light|1440x900-text200-light)"
```

**types-final-complete** — 9 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && for config in tsconfig.s8.json tsconfig.json tsconfig.s[0-7].json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**prek-resumed** — Formatting fixed 2 files; exit 1.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-resumed** — Interrupted during default project to serialize memory-heavy checks; exit 130.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**prek-before-browser** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**fixture-resumed** — Fixture build completed: 9,772 modules; watch process then stopped (130); exit 130.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**prek-specs** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**resumed-focus** — 18 passed (8.5m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=resumed-focus node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_commands.spec.ts tests/s8/pui_p31_work_reflow.spec.ts --grep "command|recovery|workspace.*390x844-dark|existing Work assistant.*(1440x900-light|1440x900-text200-light)"
```

**types-final-source** — 9 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**s8-final** — 1 failed; 49 passed (31.1m); exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts tests/s8/pui_p31_work_keyboard.spec.ts
```

**prek-attachment-spec** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-s8-supplement** — 1 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

**s8-reflow-retry** — 1 passed (1.1m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-reflow-retry node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "workspace.*1920x1080-dark"
```

**s8-attachment-keyboard** — 3 failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-attachment-keyboard node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "native attachment table"
```

**prek-static-heading** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**fixture-static-heading** — Fixture build passed, 9772 modules; exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**prek-preview-spec** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-heading-final** — Default project: 1 CSS-length width type error; subsequently fixed; exit 2.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**s8-attachment-final** — 3 passed / 1 loading-state failure; interrupted during the next case. Explicit fixture-settle wait added; exit 130.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-attachment-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "native attachment table"
```

**prek-table-types** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**s8-attachment-complete** — 6 passed / 1 tooltip overflow failure / 5 not run; maximum-failure outer error. Work-only tooltip fixed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-attachment-complete node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "native attachment table" --max-failures=1
```

**types-table-final** — 9 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**units-final** — Test Files  61 passed (61); Tests  620 passed (620); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" node node_modules/vitest/vitest.mjs run --maxWorkers=1 --minWorkers=1
```

**prek-preview-tooltip** — Formatting fixed 1 file; subsequent clean hook run passed; exit 1.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**prek-tooltip-clean** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**prek-tooltip-check** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**fixture-tooltip** — Fixture build passed, 9772 modules; exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**prek-final-tests** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**s8-attachment-bounded** — 1 passed / 1 keyboard scroll timing failure / 10 not run; maximum-failure outer error. Refocus before ArrowRight; standalone and final12 passed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-attachment-bounded node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "native attachment table" --max-failures=1
```

**prek-scroll-extents** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**s8-scroll-probe** — 1 passed (1.9m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-scroll-probe node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "native attachment table.*1100x800-dark"
```

**types-sealed** — 9 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

**s8-attachment-sealed** — 12 passed (7.2m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=s8-attachment-sealed node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "native attachment table" --max-failures=1
```

**prek-text-traversal** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-s8-text-final** — 1 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

**s4-final** — 1 failed; 157 passed (33.8m); exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s4 AIMMS_RUN=s4-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s4/
```

**s4-continuity-replay** — 16 passed (2.7m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s4 AIMMS_RUN=s4-continuity-replay node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s4/pui_work_continuity.spec.ts
```

**s8-text-keyboard** — 1 failed; exit 1.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s8 AIMMS_RUN=s8-text-keyboard node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "with 200% text"
```

**prek-focus-header** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-s8-focus-header** — 1 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

**fixture-focus-header** — Fixture build passed, 9772 modules; exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

**prek-focus-captures** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**types-s8-focus-themes** — 1 type project(s), no diagnostics; exit 0.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

**units-focus-final** — Test Files  61 passed (61); Tests  620 passed (620); exit 0.

```sh
cd src/frontend && node --input-type=module -e 'import { startVitest } from "vitest/node"; const ctx = await startVitest("test", [], { watch: false }); process.exitCode = ctx?.state.getFiles().some((f) => f.result?.state === "fail") ? 1 : 0; await ctx?.close();'
```

**s8-focus-final** — 4 passed (1.8m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s8 AIMMS_RUN=s8-focus-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "all visible Work panel"
```

**s6-final** — 122 passed (7.5m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s6 AIMMS_RUN=s6-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s6/
```

**s7-final** — 98 passed (9.1m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s7 AIMMS_RUN=s7-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s7/
```

**source-freeze-final** — 27 source/test/config files and 66 fixture files hashed; exit 0.

```sh
python3 data/devscratch/r6-s8/P31-work/freeze.py
```

**fixture-container-hashes** — 66 active fixture files match host hashes; exit 0.

```sh
python3 data/devscratch/r6-s8/P31-work/verify-bundle.py
```

**s1-final** — 146 passed (7.9m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s1 AIMMS_RUN=s1-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s1/pui_work_panels.spec.ts tests/s1/pui_work_assistant.spec.ts tests/s1/pui_work_attachments.spec.ts tests/s1/pui_native_attachments.spec.ts tests/s1/pui_work_list.spec.ts tests/s1/pui_work_materials.spec.ts tests/s1/pui_assistant_context.spec.ts
```

**focus-review-artifacts** — 2 inspected review sheets from 14 original focus captures; exit 0.

```sh
python3 data/devscratch/r6-s8/P31-work/review-focus.py
```

**s4-css-final** — 158 passed (5.1m); exit 0.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s4 AIMMS_RUN=s4-css-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s4/
```

**coverage-final** — 603 unique passing browser cases: S8 79, S4 158, S6 122, S7 98, S1 146; exit 0.

```sh
python3 data/devscratch/r6-s8/P31-work/coverage.py
```

**source-unchanged-final** — 27 source/test/config hashes unchanged; 80 inspected review sheet hashes verified; exit 0.

```sh
python3 data/devscratch/r6-s8/P31-work/verify-source.py
```

**capture-manifest-final** — 1952 original browser PNG files hashed; review sheets excluded; exit 0.

```sh
python3 data/devscratch/r6-s8/P31-work/inspect-captures.py
```

**browser-stop** — 1 own browser/fixture container stopped; exit 0.

```sh
docker stop r6-p31-browser-15232
```

**prek-final-report** — EOF hook fixed one extra trailing newline in the report; code hooks passed; exit 1.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**prek-final-clean** — Hooks passed; exit 0.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

**diff-final** — 0 whitespace errors; exit 0.

```sh
git diff --check
```

**source-post-hooks** — 27 source/test/config hashes unchanged; 80 inspected review sheet hashes verified; exit 0.

```sh
python3 data/devscratch/r6-s8/P31-work/verify-source.py
```


## Final report-only hook check

After including the journal's final results, the report-only command below was run twice: five applicable hooks passed per run; other hooks had no matching files. The second run follows correction of the documented hook count. Logs: `data/devscratch/r6-s8/P31-work/prek-report-seal-first.log` and `prek-report-seal.log`. These two invocations are separate from the 91 journalled validation/artifact/cleanup invocations above.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files contrib/ui-ux/r6/reports/P31-work.md
```

## O12 follow-up command journal

Literal validation commands executed for this follow-up, with results and retained logs. Setup/read-only inspection and capture composition used the scratch helpers recorded above. Failures and formatting attempts remain failures; they are not acceptance.

`review-types-first.log` — exit 0; 23.0s; 1 type project(s), no diagnostics.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

`review-prek-first.log` — exit 1; 1.2s; 5 applicable hooks passed; formatting/lint correction retained in log.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-fixture-first.log` — exit 0; 36.8s; Fixture build passed; 9773 modules.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-prek-specs.log` — exit 1; 1.1s; 5 applicable hooks passed; formatting/lint correction retained in log.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-prek-build.log` — exit 0; 0.7s; 6 applicable hooks passed.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-fixture-candidate.log` — exit 0; 24.6s; Fixture build passed; 9773 modules.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-probe.log` — exit 1; 61.6s; 3 failed; 5 did not run; 5 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-probe node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_review.spec.ts tests/s8/pui_p31_work_reflow.spec.ts --grep "D[4-9]|workspace.*390x844-dark|existing Work assistant.*(360x844-light|text200-light)" --max-failures=3
```

`review-types-all.log` — exit 0; 160.1s; 9 type project(s), no diagnostics.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`review-units.log` — exit 0; 1.8s; 620 tests / 61 files passed.

```sh
cd src/frontend && node --input-type=module -e 'import { startVitest } from "vitest/node"; const ctx = await startVitest("test", [], { watch: false }); process.exitCode = ctx?.state.getFiles().some((f) => f.result?.state === "fail") ? 1 : 0; await ctx?.close();'
```

`review-direct-replay.log` — exit 1; 132.7s; 1 failed; 9 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-direct-replay node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_review.spec.ts --max-failures=3
```

`review-storage-replay.log` — exit 0; 1.5s; 1 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-storage-replay node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_review.spec.ts --grep "D7"
```

`review-prek-final-source.log` — exit 1; 0.6s; 5 applicable hooks passed; formatting/lint correction retained in log.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-prek-source-clean.log` — exit 0; 0.6s; 6 applicable hooks passed.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-types-sealed.log` — exit 0; 89.9s; 9 type project(s), no diagnostics.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`review-prek-table-names.log` — exit 1; 0.5s; 5 applicable hooks passed; formatting/lint correction retained in log.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-prek-native-controls.log` — exit 0; 0.7s; 6 applicable hooks passed.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-s8-all.log` — exit 1; 364.1s; 24 failed; 65 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s8 AIMMS_RUN=review-s8-all node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/
```

`review-fixture-native-controls.log` — exit 0; 22.8s; Fixture build passed; 9773 modules.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-types-complete.log` — exit 0; 102.7s; 9 type project(s), no diagnostics.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`review-native-probe.log` — exit 1; 6.9s; 1 failed; 4 did not run.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-native-probe node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts tests/s8/pui_p31_work_reflow.spec.ts --grep "native attachment table.*(1100x800-light|390x844-dark|text200-light)|My work.*(1440x900-light|390x844-dark)" --max-failures=1
```

`review-prek-overflow-state.log` — exit 0; 0.6s; 6 applicable hooks passed.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-fixture-overflow.log` — exit 0; 13.0s; Fixture build passed; 9773 modules.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-overflow-probe.log` — exit 1; 7.6s; 1 failed; 4 did not run.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-overflow-probe node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts tests/s8/pui_p31_work_reflow.spec.ts --grep "native attachment table.*(1100x800-light|390x844-dark|text200-light)|My work.*(1440x900-light|390x844-dark)" --max-failures=1
```

`review-prek-ref-bridge.log` — exit 1; 1.8s; 5 applicable hooks passed; formatting/lint correction retained in log.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-prek-ref-clean.log` — exit 0; 2.0s; 6 applicable hooks passed.

```sh
PATH=/opt/homebrew/bin:$PATH prek run --files $(cat data/devscratch/r6-s8/P31-work/changed-files.txt)
```

`review-types-ref-bridge.log` — exit 0; 36.6s; 1 type project(s), no diagnostics.

```sh
cd src/frontend && node node_modules/typescript/bin/tsc --noEmit -p tsconfig.s8.json
```

`review-fixture-ref-bridge.log` — exit 0; 45.5s; Fixture build passed; 9773 modules.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-ref-probe.log` — exit 1; 10.9s; 1 failed; 4 did not run.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-ref-probe node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts tests/s8/pui_p31_work_reflow.spec.ts --grep "native attachment table.*(1100x800-light|390x844-dark|text200-light)|My work.*(1440x900-light|390x844-dark)" --max-failures=1
```

`review-prek-columns.log` — exit 0; 1.3s; 6 applicable hooks passed.

```sh
prek run --files src/frontend/lib/components/TableColumnSelect.tsx src/frontend/tests/pui_tables.spec.ts
```

`review-fixture-columns.log` — exit 1; 13.2s; Fixture build failed; 9749 modules.

```sh
cd src/frontend && node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-fixture-columns-development.log` — exit 0; 20.5s; Fixture build passed; 9773 modules.

```sh
cd src/frontend && NODE_ENV=development node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-units-final.log` — exit 0; 4.9s; 620 tests / 61 files passed.

```sh
cd src/frontend && node --input-type=module -e 'import { startVitest } from "vitest/node"; const ctx = await startVitest("test", [], { watch: false }); process.exitCode = ctx?.state.getFiles().some((f) => f.result?.state === "fail") ? 1 : 0; await ctx?.close();'
```

`review-freeze.log` — exit 0; 0.4s; Bundle files: 66.

```sh
python3 data/devscratch/r6-s8/P31-work/review-freeze.py
```

`review-container-hashes.log` — exit 0; 1.0s; 66 active fixture files match host hashes.

```sh
python3 data/devscratch/r6-s8/P31-work/review-verify-bundle.py
```

`review-columns-probe.log` — exit 0; 41.2s; 5 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-columns-probe node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts tests/s8/pui_p31_work_reflow.spec.ts --grep "native attachment table.*(1100x800-light|390x844-dark|text200-light)|My work.*(1440x900-light|390x844-dark)" --max-failures=1
```

`review-types-final.log` — exit 0; 204.0s; 9 type project(s), no diagnostics.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`review-s8-green.log` — exit 0; 528.2s; 89 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s8 AIMMS_RUN=review-s8-green node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/
```

`review-source-and-captures.log` — exit 0; 0.1s; 65 source/test/config hashes unchanged; 81 inspected review sheet hashes verified.

```sh
python3 data/devscratch/r6-s8/P31-work/review-verify-source.py
```

`review-prek-regression-selectors.log` — exit 1; 0.9s; 5 applicable hooks passed; formatting/lint correction retained in log.

```sh
prek run --files src/frontend/tests/s1/pui_native_attachments.spec.ts src/frontend/tests/s7/pui_attachment_recovery.spec.ts src/frontend/tests/pages/pui_part.spec.ts src/frontend/tests/pages/pui_ai_chat_memory.spec.ts
```

`review-prek-selectors-clean.log` — exit 0; 1.1s; 6 applicable hooks passed.

```sh
prek run --files src/frontend/tests/s1/pui_native_attachments.spec.ts src/frontend/tests/s7/pui_attachment_recovery.spec.ts src/frontend/tests/pages/pui_part.spec.ts src/frontend/tests/pages/pui_ai_chat_memory.spec.ts
```

`review-final-freeze.log` — exit 0; 0.5s; Bundle files: 66.

```sh
python3 data/devscratch/r6-s8/P31-work/review-final-freeze.py
```

`review-runtime-unchanged.log` — exit 0; 0.2s; 28 production/config hashes unchanged after S8; 66 fixture file hashes unchanged; only regression selectors changed.

```sh
python3 - <<'PY'
from pathlib import Path
import hashlib,json
p=Path("data/devscratch/r6-s8/P31-work")
a=json.loads((p/"review-source.json").read_text());b=json.loads((p/"review-final-source.json").read_text())
source=[s for s in a if not s.startswith("src/frontend/tests/")]
assert all(a[s]==b[s] for s in source)
assert (p/"review-bundle.json").read_bytes()==(p/"review-final-bundle.json").read_bytes()
print(len(source),"production/config hashes unchanged after S8; 66 fixture file hashes unchanged; only regression selectors changed")
PY
```

`review-types-selector-final.log` — exit 0; 220.8s; 9 type project(s), no diagnostics.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`review-s1-green.log` — exit 1; 614.4s; 2 failed; 144 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s1 AIMMS_RUN=review-s1-green node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s1/pui_work_panels.spec.ts tests/s1/pui_work_assistant.spec.ts tests/s1/pui_work_attachments.spec.ts tests/s1/pui_native_attachments.spec.ts tests/s1/pui_work_list.spec.ts tests/s1/pui_work_materials.spec.ts tests/s1/pui_assistant_context.spec.ts
```

`review-source-final-selector.log` — exit 0; 0.1s; 67 source/test/config hashes unchanged; 81 inspected review sheet hashes verified.

```sh
python3 data/devscratch/r6-s8/P31-work/review-final-verify-source.py
```

`review-native-attachments-replay.log` — exit 0; 20.8s; 18 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s1 AIMMS_RUN=review-native-attachments-replay node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s1/pui_native_attachments.spec.ts
```

`review-diff-check.log` — exit 0; 0.1s; Passed.

```sh
git diff --check
```

`review-s4-green.log` — exit 0; 356.1s; 158 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s4 AIMMS_RUN=review-s4-green node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s4/
```

`review-s6-green.log` — exit 0; 277.6s; 122 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s6 AIMMS_RUN=review-s6-green node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s6/
```

`review-s7-green.log` — exit 0; 230.1s; 98 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s7 AIMMS_RUN=review-s7-green node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s7/
```

`review-s3-green.log` — exit 0; 41.0s; 46 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s3 AIMMS_RUN=review-s3-green node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s3/pui_machine_work_history.spec.ts
```

`review-chat-green.log` — exit 0; 21.0s; 25 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=chat AIMMS_RUN=review-chat-green node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/pages/pui_ai_chat_drawer_layout.spec.ts tests/pages/pui_voice_decisions.spec.ts tests/pages/pui_ui_reflow.spec.ts
```

`review-prek-splitter-bounds.log` — exit 0; 0.5s; 6 applicable hooks passed.

```sh
prek run --files src/frontend/src/components/nav/AIChatDrawer.tsx src/frontend/tests/s8/pui_p31_work_reflow.spec.ts
```

`review-types-bounds-final.log` — exit 0; 74.3s; 9 type project(s), no diagnostics.

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`review-units-bounds-final.log` — exit 0; 2.1s; 620 tests / 61 files passed.

```sh
cd src/frontend && node --input-type=module -e 'import { startVitest } from "vitest/node"; const ctx = await startVitest("test", [], { watch: false }); process.exitCode = ctx?.state.getFiles().some((f) => f.result?.state === "fail") ? 1 : 0; await ctx?.close();'
```

`review-fixture-bounds-final.log` — exit 0; 16.3s; Fixture build passed; 9773 modules.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`review-splitter-bounds-red.log` — exit 1; 2.2s; 1 failed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=review-splitter-bounds-red node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "existing Work assistant 360x844-light"
```

`review-sealed-freeze.log` — exit 0; 0.1s; Bundle files: 66.

```sh
python3 data/devscratch/r6-s8/P31-work/review-sealed-freeze.py
```

`review-bounds-final.log` — exit 0; 30.6s; 17 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s8 AIMMS_RUN=review-bounds-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts tests/s8/pui_p31_work_review.spec.ts --grep "existing Work assistant|D6 |D9 "
```

`review-bounds-delta.log` — exit 0; 0.0s; 27 other production/config hashes unchanged; final production delta is exactly two clamped ARIA bounds.

```sh
python3 data/devscratch/r6-s8/P31-work/review-bounds-delta.py
```

`review-sealed-container-hashes.log` — exit 0; 0.1s; 66 active fixture files match host hashes.

```sh
python3 data/devscratch/r6-s8/P31-work/review-sealed-verify-bundle.py
```

`review-s7-bounds-final.log` — exit 0; 155.5s; 98 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s7 AIMMS_RUN=review-s7-bounds-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s7/
```

`review-chat-bounds-final.log` — exit 0; 21.6s; 25 passed.

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=chat AIMMS_RUN=review-chat-bounds-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/pages/pui_ai_chat_drawer_layout.spec.ts tests/pages/pui_voice_decisions.spec.ts tests/pages/pui_ui_reflow.spec.ts
```

`review-seal-before-cleanup.log` — exit 0; 0.0s; 67 source/test/config hashes unchanged; 87 inspected review sheet hashes verified.

```sh
python3 data/devscratch/r6-s8/P31-work/review-sealed-verify-source.py
```

`review-prek-all-clean.log` — exit 0; 0.7s; 6 applicable hooks passed.

```sh
python3 data/devscratch/r6-s8/P31-work/review-prek-all.py
```

`review-diff-final.log` — exit 0; 0.0s; Passed.

```sh
git diff --check
```

`review-browser-stop.log` — exit 0; 3.3s; r6-p31-browser-15232.

```sh
docker stop r6-p31-browser-15232
```

`review-prek-report-seal.log` — exit 0; 0.4s; 5 applicable hooks passed.

```sh
prek run --files contrib/ui-ux/r6/reports/P31-work.md
```

`review-final-audit.log` — exit 0; 0.3s; 304 reviewed original hashes verified; 68 uncommitted allowed files; own browser container absent.

```sh
python3 data/devscratch/r6-s8/P31-work/review-final-audit.py
```

Report-only final seals after the journal snapshot: `prek run --files contrib/ui-ux/r6/reports/P31-work.md` passed all **5 applicable hooks**; `git diff --check` returned **0 errors**. These followed the final report composition. The source and reviewed-artifact audit remains **67 source/test/config hashes, 87 inspected sheets and 304 reviewed original hashes verified**. The dedicated browser container is absent; all 68 files remain uncommitted.

## Coordinator follow-up 2 — host keyboard regression

Status: **built and tested in the offline native fixture harness**. This follow-up changes three source/test files plus this report; the previous P31 and D1–D9 work remains uncommitted. It is not macOS product acceptance.

Final-source results: **keyboard 30/30; full S8 91/91; selected S1 Work/assistant/attachments 146/146; S4 158/158; S6 122/122; S7 98/98; TypeScript default plus every sprint config 9/9; units 620/620 across 61 files**.

### Finding, root cause and disposition

The coordinator supplied macOS Chromium/GPU results: S1 180/180, S4 158/158, S6 122/122, S7 98/98 and S8 88/89. These are coordinator-supplied results, not commands executed by this run. I read the host error context and trace at `/tmp/aimms-r6-s8-browser-15222/s8-pui_p31_work_keyboard-a-c7262-cted-keyboard-path-at-390px-s8-work/` and inspected its saved screenshot. Focus index 17 is the Dependencies link **WO-74 · Synthetic sibling electrical check**. The trace reports a wrapped link height of 42.796875px and a failed center-point hit check. The screenshot also shows the inline link clipped horizontally by its table viewport while the dock is lower in the image. Thus the generic “covered by fixed UI” failure also involves the nested horizontal viewport; adding bottom space alone would not address the observed clipping.

The product fix gives links inside Work table viewports one wrapping box capped to the measured viewport width, retaining the complete text. The existing resize observer writes `--work-scroll-width` directly on that viewport. On keyboard-visible focus, the Work table helper explicitly reveals the target on both axes with native `scrollIntoView`; the existing measured phone-dock scroll margin then clears the outer document. Pointer focus does not trigger this scrolling. No additional stop, hidden column, text truncation, backend change or dependency was added.

Two deterministic light/dark cases at **390×900** change the table font family, size and line height, deliberately position the nested link under the actual dock, then Tab from the table viewport. They require the whole link box to fit horizontally, its bottom to sit above the measured dock, the center point to hit the focused link, and zero commands. The settled pre-fix cases passed **0/2 (2 failed)** on real horizontal clipping. The existing all-panel keyboard traversal guarantee is unchanged; it passes on the final source.

### Files changed by this follow-up

- `src/frontend/src/components/tables/WorkTableScroll.tsx`: measured link width and keyboard focus reveal in the existing Work-only table helper (also used by Work attachments).
- `src/frontend/src/pages/tasks/workorders/workCompanion.module.css`: Work-scoped wrapping link box; shared stylesheet edit is limited to `[data-work-scroll-viewport] a`.
- `src/frontend/tests/s8/pui_p31_work_keyboard.spec.ts`: two font-metric/dock regressions, geometry attachments and before/after focus captures. Existing assertions preserved.
- `contrib/ui-ux/r6/reports/P31-work.md`: this disposition and evidence.

`hostfix-source.json` seals all 67 existing source/test/config files. `hostfix-delta.json` confirms exactly the three code/test deltas above from the prior review seal; no other agent edits were detected. The total uncommitted file set remains 68.

### Browser environment and captures

All renders performed by this follow-up came from **Linux Chromium 1228 in the dedicated `r6-p31-browser-15232` container**, ws `127.0.0.1:15232/r6-p31`, harness port **15222**. The cached browserless image was started with `--pull never`; no installation or download occurred. The current container IP was `172.17.0.2`; the scratch config and fixture-server allowlist were updated accordingly. The native fixture bundle was built with `NODE_ENV=development`, copied into this container and served there to avoid shared host tunnel asset delays. Backend/API/media/static requests remain fixture-guarded/refused. No Azure, app startup or Django test was used.

The final keyboard captures are under `data/devscratch/r6-s8/P31-work/browser/hostfix-keyboard-final/`, with `dependency-before-390x900-{light,dark}.png` and `dependency-focus-390x900-{light,dark}.png`. I opened and inspected all four originals: the setup places the link behind the dock; after actual keyboard traversal its complete text and focus box are visible above the dock. Additional full S8 captures are under `browser/hostfix-s8/`. I also inspected the 1100px light/dark Plan captures and 200% text light/dark attachment disclosure captures (11 inspected originals recorded: eight final-source images, the host failure and two explicitly labelled intermediate focus captures). Four inspected dependency-table excerpts at 360px and desktop 200% text are recorded with source hashes/crop bounds in `hostfix-excerpts.json`; their horizontal disclosure remains available and keyboard reveal is tested separately. `hostfix-captures-viewed.json` records the precise inspected paths and hashes; it distinguishes the coordinator’s macOS failure screenshot from this run’s final-source Linux captures. The final audit inventories 1,622 retained new browser PNGs (including development attempts and replay captures; not all were visually inspected), verifies all 11 recorded inspected original hashes and four inspected excerpts, and confirms the dedicated container is absent. The original host error context, complete trace and trace fingerprint are retained in `hostfix-host-error-context.md`, `hostfix-host-trace.zip` and `hostfix-host-input.json`.

### Checks and exact validation commands

The following entries are the actual `hostfix-*` command journal, including failed development attempts. Initial positioning failures came from observing the fixture before its fetch/layout settled; a temporary diagnostic also had one parser error. The settled pre-fix cases then reproduced horizontal clipping. Giving links a single box alone still failed, as did explicit reveal before capping a link wider than the viewport. Those logs remain separate from the final passing runs. No timeout or product assertion was weakened. The first cleanup audit expected a capitalized Docker “No such” error, while this host emits lowercase “no such”; that evidence-script comparison was corrected to be case-insensitive and the corrected audit passed. Product code was unchanged by that correction.

| Log | Result | Exit |
| --- | --- | --- |
| `hostfix-regression-before.log` | 2 failed | 1 |
| `hostfix-regression-position.log` | Parser error; 0 tests executed (temporary diagnostic removed) | 1 |
| `hostfix-regression-position2.log` | 1 failed | 1 |
| `hostfix-regression-before-settled.log` | 2 failed | 1 |
| `hostfix-build.log` | Native fixture build passed; existing chunk/dynamic-import warnings retained | 0 |
| `hostfix-regression-after.log` | 2 failed | 1 |
| `hostfix-build-focus.log` | Native fixture build passed; existing chunk/dynamic-import warnings retained | 0 |
| `hostfix-prek-source.log` | 6 applicable hooks passed | 0 |
| `hostfix-regression-focus.log` | 2 failed | 1 |
| `hostfix-build-wrap.log` | Native fixture build passed; existing chunk/dynamic-import warnings retained | 0 |
| `hostfix-types.log` | 9/9 TypeScript configurations passed | 0 |
| `hostfix-units.log` | 620/620 tests; 61/61 files passed | 0 |
| `hostfix-build-final.log` | Native fixture build passed; existing chunk/dynamic-import warnings retained | 0 |
| `hostfix-keyboard.log` | 30 passed | 0 |
| `hostfix-prek-final-source.log` | 6 applicable hooks passed | 0 |
| `hostfix-audit-source.log` | Passed | 0 |
| `hostfix-types-final.log` | 9/9 TypeScript configurations passed | 0 |
| `hostfix-keyboard-final.log` | 30 passed | 0 |
| `hostfix-units-final.log` | 620/620 tests; 61/61 files passed | 0 |
| `hostfix-bundle-audit.log` | Passed | 0 |
| `hostfix-s8.log` | 91 passed | 0 |
| `hostfix-s1.log` | 146 passed | 0 |
| `hostfix-s4.log` | 158 passed | 0 |
| `hostfix-s6.log` | 122 passed | 0 |
| `hostfix-s7.log` | 98 passed | 0 |
| `hostfix-prek-all.log` | 6 applicable hooks passed | 0 |
| `hostfix-audit-source-final.log` | Passed | 0 |
| `hostfix-bundle-final.log` | Passed | 0 |
| `hostfix-browser-stop.log` | Passed | 0 |
| `hostfix-final-audit.log` | Failed; see preserved log | 1 |
| `hostfix-final-audit-corrected.log` | Passed | 0 |

Commands below ran from the checkout root; each Playwright invocation used the cleared environment, harness port 15222 and the scratch Docker connection config. One Playwright command ran at a time. The `check.py` wrapper saves the literal command, exit code, duration and full log to `commands.jsonl`. The related suites were sequenced with `python3 data/devscratch/r6-s8/P31-work/hostfix-related-suites.py`; report composition used `python3 data/devscratch/r6-s8/P31-work/hostfix-report.py`.

`hostfix-regression-before.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-regression-before node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "nested dependency"
```

`hostfix-regression-position.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-regression-position node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "larger font metrics in light"
```

`hostfix-regression-position2.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-regression-position2 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "larger font metrics in light"
```

`hostfix-regression-before-settled.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-regression-before-settled node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "nested dependency"
```

`hostfix-build.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`hostfix-regression-after.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-regression-after node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "nested dependency"
```

`hostfix-build-focus.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`hostfix-prek-source.log`

```sh
prek run --files src/frontend/src/components/tables/WorkTableScroll.tsx src/frontend/src/pages/tasks/workorders/workCompanion.module.css src/frontend/tests/s8/pui_p31_work_keyboard.spec.ts
```

`hostfix-regression-focus.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-regression-focus node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts --grep "nested dependency"
```

`hostfix-build-wrap.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`hostfix-types.log`

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`hostfix-units.log`

```sh
cd src/frontend && node --input-type=module -e 'import { startVitest } from "vitest/node"; const ctx = await startVitest("test", [], { watch: false }); process.exitCode = ctx?.state.getFiles().some((f) => f.result?.state === "fail") ? 1 : 0; await ctx?.close();'
```

`hostfix-build-final.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`hostfix-keyboard.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-keyboard node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts
```

`hostfix-prek-final-source.log`

```sh
prek run --files src/frontend/src/components/tables/WorkTableScroll.tsx src/frontend/src/pages/tasks/workorders/workCompanion.module.css src/frontend/tests/s8/pui_p31_work_keyboard.spec.ts
```

`hostfix-audit-source.log`

```sh
python3 data/devscratch/r6-s8/P31-work/hostfix-audit.py
```

`hostfix-types-final.log`

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`hostfix-keyboard-final.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-keyboard-final node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_keyboard.spec.ts
```

`hostfix-units-final.log`

```sh
cd src/frontend && node --input-type=module -e 'import { startVitest } from "vitest/node"; const ctx = await startVitest("test", [], { watch: false }); process.exitCode = ctx?.state.getFiles().some((f) => f.result?.state === "fail") ? 1 : 0; await ctx?.close();'
```

`hostfix-bundle-audit.log`

```sh
python3 data/devscratch/r6-s8/P31-work/hostfix-bundle-audit.py
```

`hostfix-s8.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=hostfix-s8 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/
```

`hostfix-s1.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s1 AIMMS_RUN=hostfix-s1 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s1/pui_work_panels.spec.ts tests/s1/pui_work_assistant.spec.ts tests/s1/pui_work_attachments.spec.ts tests/s1/pui_native_attachments.spec.ts tests/s1/pui_work_list.spec.ts tests/s1/pui_work_materials.spec.ts tests/s1/pui_assistant_context.spec.ts
```

`hostfix-s4.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s4 AIMMS_RUN=hostfix-s4 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s4/
```

`hostfix-s6.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s6 AIMMS_RUN=hostfix-s6 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s6/
```

`hostfix-s7.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_BASE_SPRINT=s7 AIMMS_RUN=hostfix-s7 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s7/
```

`hostfix-prek-all.log`

```sh
python3 data/devscratch/r6-s8/P31-work/review-prek-all.py
```

`hostfix-audit-source-final.log`

```sh
python3 data/devscratch/r6-s8/P31-work/hostfix-audit.py
```

`hostfix-bundle-final.log`

```sh
python3 data/devscratch/r6-s8/P31-work/hostfix-bundle-audit.py
```

`hostfix-browser-stop.log`

```sh
docker stop r6-p31-browser-15232
```

`hostfix-final-audit.log`

```sh
python3 data/devscratch/r6-s8/P31-work/hostfix-final-audit.py
```

`hostfix-final-audit-corrected.log`

```sh
python3 data/devscratch/r6-s8/P31-work/hostfix-final-audit.py
```

Browser lifecycle commands (setup/copy operations exited 0; only this container was touched):

```sh
docker run -d --rm --pull never --name r6-p31-browser-15232 -p 127.0.0.1:15232:3000 --mount type=bind,src=/Users/Aniketkumar/IdeaProjects/InvenTree-r6/p31-work/src/frontend/node_modules,dst=/frontend/node_modules,readonly --entrypoint node ghcr.io/browserless/chromium:latest /frontend/node_modules/playwright/cli.js run-server --port 3000 --host 0.0.0.0 --path /r6-p31 --max-clients 1 --unsafe
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' r6-p31-browser-15232
docker exec r6-p31-browser-15232 mkdir -p /tmp/r6-p31-fixtures
docker cp data/devscratch/r6-s8/P31-work/fixture-bundle/. r6-p31-browser-15232:/tmp/r6-p31-fixtures/fixture-bundle/
docker cp data/devscratch/r6-s8/P31-work/serve_fixture.mjs r6-p31-browser-15232:/tmp/r6-p31-fixtures/serve_fixture.mjs
docker exec -d -e AIMMS_HARNESS_PORT=15222 r6-p31-browser-15232 node /tmp/r6-p31-fixtures/serve_fixture.mjs
```

The fixture bundle copy command was repeated after each build, only between Playwright commands. Read-only diagnostics included `git diff --stat`, `git diff --numstat`, `rg`, targeted source/report/config reads, Python ZIP/JSON inspection of the host trace, and image inspection. A diagnostic `ps -eo pid,etime,command` was denied by the sandbox; no backend test was attempted.

### Open items and CI

The final source has not been replayed on the coordinator’s macOS/GPU browser by this run because this sandbox uses the supplied Docker browser setup. The missing fact is that final host replay result; Linux fixture passes and reviewed screenshots are not substituted for it. Retain the existing host all-panel traversal test and add the two new font-metric cases to S8 CI on both macOS and Linux. No live deployment, assistive-technology session or physical phone acceptance is claimed. The wider Work uplift and machine/station P31 part B remain outside this change.

Final report-only seals after composing this section are recorded in `hostfix-prek-report.log` / `hostfix-prek-report-corrected.log` (5 applicable hooks passed each) and `hostfix-diff-final.log` / `hostfix-diff-final-corrected.log` (0 errors each), with their literal commands and exit codes in `commands.jsonl`:

```sh
prek run --files contrib/ui-ux/r6/reports/P31-work.md
git diff --check
```

## Coordinator follow-up 3 — measured header and complete dependency state

Status: **built and tested with the requested targeted native fixture checks**. Final macOS/GPU replay remains with the coordinator. Everything remains uncommitted. The dedicated browser container is stopped and confirmed absent.

The coordinator supplied host results of S7 **98/98** and S8 **89/91**, with both 1440×900 / 200% text reflow cases failing. I read the host trace/error context and inspected both light and dark failure screenshots. The two-row sticky header covers the top of **Save deviation**, and the Dependencies **IN PROGRESS** badge crosses the table viewport's right clip. The original screenshots, error contexts and complete traces are retained under `data/devscratch/r6-s8/P31-work/headerfix/host-{light,dark}-*`; hashes are in `headerfix/host-inputs.json`. These host results are supplied evidence, not runs executed by this agent.

### Review findings and dispositions

- **Measured sticky clearance:** the native shared Header observes its border box and publishes `--app-sticky-header-height` directly on the document root, including its sticky top offset. It updates after wrapping, text resizing, viewport changes and changes to header contents. The effect restores the previous property on cleanup and publishes zero obstruction when the native header is not sticky/fixed. Work's controls and workflow scroll target use this measurement plus native Mantine spacing. The old fixed 100px / 5em approximation and phone workflow's 80px approximation are removed. No other screen consumes the new variable in this change.
- **Complete dependency state:** the Dependencies table uses fixed column layout with explicit proportional widths; the work-order title wraps, the state column has room for its badge, and the badge label can shrink/wrap inside it. All four columns and full text remain available. The existing named native horizontal scroll viewport remains for narrow widths and large text; no state or column is removed.
- **Regression coverage:** two light/dark cases at 1440×900 with 200% text force the real native sticky header to 240px and then 320px. They check the live scroll margin, reuse the unchanged five-point `uncovered()` guarantee, compare the entire action box against the actual header bottom and attach geometry/captures. They require the complete state text to survive both the badge-root clip and the table viewport clip. The final hardening scrolls the table viewport vertically and asserts horizontal scroll remains **zero**, so automatically revealing the badge cannot mask clipping at the initial table position. The existing reflow and keyboard guarantees are preserved. Ordinary matrix cases also save viewport captures immediately after checking Save deviation.

### Files and scope

- `src/frontend/src/components/nav/Header.tsx` — the only newly touched shared source file: a header ref, resize observer and geometric CSS variable; no header layout/theme change.
- `src/frontend/src/pages/tasks/workorders/workCompanion.module.css` — Work-scoped measured clearance, workflow clearance and dependency-table layout/label wrapping.
- `src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx` — only the dependency table class and four column widths.
- `src/frontend/tests/s8/pui_p31_work_reflow.spec.ts` — two regressions, stronger state clipping check, geometry attachments and action captures; already registered by the S8 config.
- `contrib/ui-ux/r6/reports/P31-work.md` — this disposition and evidence.

`headerfix-before-source.json`, `headerfix/source.json` and `headerfix/delta.json` establish exactly these four code/test deltas from the preceding handoff, with the other 64 source/test/config hashes unchanged. The final source seal covers 68 files; the total uncommitted file set is 69 including this report. Host `prek` passed all six applicable hooks on those 69 files. No machine/station source, shared ledger, dependency or backend field/endpoint was changed. No app/backend/Django run or Azure access occurred.

### Checks and captures

The complete requested S8 replay passed **68/68: 38 reflow and 30 keyboard cases**, including both original 200% text cases, all seven panels, the lists, attachments and assistant at the existing width/theme matrix. The last change after that run only strengthened the two new tests to require full state at horizontal scroll zero and added geometry attachments; these two cases were then replayed **2/2**. Product code was unchanged after the complete run. Final default plus every `tsconfig.s*.json` passed **9/9**. Units passed **620/620 across 61 files** on the same production source. Prior S1/S4/S6/S7 and full-S8 results earlier in this report remain historical to those earlier checks; this follow-up reran the two S8 specs requested by the coordinator.

The pre-fix marker-only baseline passed **0/2**: scroll margin remained **172px** after the header grew to 240px. A development replay then reached actionability but the new synthetic form setup had omitted its required reason, leaving Save deviation disabled. I stopped only my browser container to end that invalid attempt, retained its log/traces, filled the required synthetic draft and replayed the cases successfully. The existing specs and `uncovered()` were not weakened; no production command was submitted by the new regressions.

All new renders used **Linux Chromium 1228 in `r6-p31-browser-15232`**, ws port **15232**, harness port **15222**, with the same cached image (`--pull never`), scratch config and container-local native fixture transport described above. The container IP remained `172.17.0.2`. The bundle audit verifies **66 current fixture files** match the browser container. No installation or download occurred. Only one Playwright command ran at a time.

Capture directories:

- `browser/headerfix-s8-reflow-keyboard/`: full matrix captures and the new `deviation-action-<width>x<height>[-text200]-<scheme>.png` viewport captures. I inspected both natural 1440×900 / 200% text action captures and both 390×844 phone action captures; the action clears the actual header and the phone dock.
- `browser/headerfix-regressions-corrected/`: inspected 240px and 320px header action captures and dependency-state captures in both themes, using the final production source.
- `browser/headerfix-initial-state-regressions/`: final stricter state-at-scroll-zero captures and header/action geometry attachments. I inspected both final state captures; all of **IN PROGRESS** is visible.
- `headerfix/viewed.json`: **14 inspected original images**, with paths/hashes and explicit macOS versus Linux provenance (12 Linux originals and the two host failures). The final audit verifies all 14 inspected image hashes and six host artifacts, and inventories 390 retained new browser PNGs. Retained development attempts are not treated as acceptance; not all retained images were visually inspected.

### Exact validation commands and results

Every entry below is an actual `headerfix-*` invocation from `commands.jsonl`, including the failing baseline and interrupted development run. Commands ran from the checkout root; each Playwright command cleared the environment and passed harness port 15222. Logs retain complete output and counts.

| Log | Result | Exit |
| --- | --- | --- |
| `headerfix-prek-test.log` | 6 applicable hooks passed | 0 |
| `headerfix-baseline-build.log` | Native fixture build passed (existing chunk warnings retained) | 0 |
| `headerfix-before.log` | 2 failed | 1 |
| `headerfix-prek-source.log` | 6 applicable hooks passed | 0 |
| `headerfix-units.log` | 620/620 tests; 61/61 files passed | 0 |
| `headerfix-build.log` | Native fixture build passed (existing chunk warnings retained) | 0 |
| `headerfix-types.log` | 9/9 configurations passed | 0 |
| `headerfix-regressions.log` | 2 failed | 1 |
| `headerfix-development-stop.log` | Passed | 0 |
| `headerfix-regressions-corrected.log` | 2 passed | 0 |
| `headerfix-s8-reflow-keyboard.log` | 68 passed | 0 |
| `headerfix-prek-final-source.log` | 6 applicable hooks passed | 0 |
| `headerfix-initial-state-regressions.log` | 2 passed | 0 |
| `headerfix-types-final.log` | 9/9 configurations passed | 0 |
| `headerfix-bundle-audit.log` | Passed | 0 |
| `headerfix-prek-all.log` | 6 applicable hooks passed | 0 |
| `headerfix-browser-stop.log` | Passed | 0 |
| `headerfix-final-audit.log` | Passed | 0 |

`headerfix-prek-test.log`

```sh
prek run --files src/frontend/tests/s8/pui_p31_work_reflow.spec.ts
```

`headerfix-baseline-build.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`headerfix-before.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=headerfix-before node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "Work clears a resized sticky header"
```

`headerfix-prek-source.log`

```sh
prek run --files src/frontend/src/components/nav/Header.tsx src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx src/frontend/src/pages/tasks/workorders/workCompanion.module.css src/frontend/tests/s8/pui_p31_work_reflow.spec.ts
```

`headerfix-units.log`

```sh
cd src/frontend && node --input-type=module -e 'import { startVitest } from "vitest/node"; const ctx = await startVitest("test", [], { watch: false }); process.exitCode = ctx?.state.getFiles().some((f) => f.result?.state === "fail") ? 1 : 0; await ctx?.close();'
```

`headerfix-build.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" NODE_ENV=development AIMMS_HARNESS_PORT=15222 node node_modules/vite/bin/vite.js build --config ../../data/devscratch/r6-s8/P31-work/vite.bundle.config.mts
```

`headerfix-types.log`

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`headerfix-regressions.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=headerfix-regressions node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "Work clears a resized sticky header"
```

`headerfix-development-stop.log`

```sh
docker stop r6-p31-browser-15232
```

`headerfix-regressions-corrected.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=headerfix-regressions-corrected node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "Work clears a resized sticky header"
```

`headerfix-s8-reflow-keyboard.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=headerfix-s8-reflow-keyboard node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts tests/s8/pui_p31_work_keyboard.spec.ts
```

`headerfix-prek-final-source.log`

```sh
prek run --files src/frontend/src/components/nav/Header.tsx src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx src/frontend/src/pages/tasks/workorders/workCompanion.module.css src/frontend/tests/s8/pui_p31_work_reflow.spec.ts
```

`headerfix-initial-state-regressions.log`

```sh
cd src/frontend && env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15222 AIMMS_RUN=headerfix-initial-state-regressions node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-s8/P31-work/browser.config.mts --reporter=list tests/s8/pui_p31_work_reflow.spec.ts --grep "Work clears a resized sticky header"
```

`headerfix-types-final.log`

```sh
cd src/frontend && for config in tsconfig.json tsconfig.s*.json; do echo "$config"; node node_modules/typescript/bin/tsc --noEmit -p "$config" || exit; done
```

`headerfix-bundle-audit.log`

```sh
python3 data/devscratch/r6-s8/P31-work/headerfix-bundle-audit.py
```

`headerfix-prek-all.log`

```sh
python3 data/devscratch/r6-s8/P31-work/review-prek-all.py
```

`headerfix-browser-stop.log`

```sh
docker stop r6-p31-browser-15232
```

`headerfix-final-audit.log`

```sh
python3 data/devscratch/r6-s8/P31-work/headerfix-final-audit.py
```

Browser lifecycle operations (all setup/copy operations exited 0; the start/setup sequence was repeated once after ending the invalid development run):

```sh
docker run -d --rm --pull never --name r6-p31-browser-15232 -p 127.0.0.1:15232:3000 --mount type=bind,src=/Users/Aniketkumar/IdeaProjects/InvenTree-r6/p31-work/src/frontend/node_modules,dst=/frontend/node_modules,readonly --entrypoint node ghcr.io/browserless/chromium:latest /frontend/node_modules/playwright/cli.js run-server --port 3000 --host 0.0.0.0 --path /r6-p31 --max-clients 1 --unsafe
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' r6-p31-browser-15232
docker exec r6-p31-browser-15232 mkdir -p /tmp/r6-p31-fixtures
docker cp data/devscratch/r6-s8/P31-work/fixture-bundle/. r6-p31-browser-15232:/tmp/r6-p31-fixtures/fixture-bundle/
docker cp data/devscratch/r6-s8/P31-work/serve_fixture.mjs r6-p31-browser-15232:/tmp/r6-p31-fixtures/serve_fixture.mjs
docker exec -d -e AIMMS_HARNESS_PORT=15222 r6-p31-browser-15232 node /tmp/r6-p31-fixtures/serve_fixture.mjs
```

The fixture bundle was copied again after the product build, between Playwright commands. Read-only inspection used `git diff --stat`, `rg`, targeted source/config/report reads, Python ZIP/JSON/hash inspection and `view_image`; manifests preserve the inspected originals. Report composition used `python3 data/devscratch/r6-s8/P31-work/headerfix-report.py`. All changes remain uncommitted.

### Open items and CI

The missing fact is a final macOS/GPU replay on the coordinator's host; this sandbox's supplied browser setup renders Linux Chromium. These targeted fixture passes do not claim macOS, deployment, assistive-technology or physical-phone acceptance. Add the two native resized-header/state-at-scroll-zero cases to the existing S8 CI jobs on macOS and Linux, preserving the complete keyboard/reflow assertions. The broader Work uplift and machine/station part B remain out of scope.

Final report-only seals after composition are recorded in `headerfix-prek-report.log` (5 applicable hooks passed) and `headerfix-diff-final.log` (0 errors), with exact commands/exit codes in `commands.jsonl`:

```sh
prek run --files contrib/ui-ux/r6/reports/P31-work.md
git diff --check
```
