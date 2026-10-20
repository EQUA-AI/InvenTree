# S4 merge reconciliation — inventTree-aniket

Status: **built and tested for the requested merge reconciliation**. 20 October 2026. This report covers reconciliation of the coordinator-started merge, not implementation or acceptance of all underlying tickets. The existing provider/physical/deployed acceptance limits in the individual ticket reports remain in force.

## Preserved behavior

| File | HEAD retained | S4 retained |
| --- | --- | --- |
| `src/frontend/src/pages/tasks/workorders/WorkOrderDetail.tsx` | Closeout link, P43 revision notices, service restoration, hold/handover/related-work continuity, monitoring continuity, authorization refusal and last-known draft recovery, native Closeout and packet navigation | Outcome-led `WorkBrief`, original problem/impact and task paths, assignment details. `HandledByAimms` renders once inside the brief. |
| `src/frontend/src/pages/tasks/workorders/execution/InvestigationRequests.tsx` | Shared artifact renderer, formatted times, display-name fallbacks, zero/absent distinction, per-reading qualifiers, unavailable original-file disclosure, unresolved pending-external label, suppression of stale reopened blockers | Secondary native observation capture, explicit saved Work/request/evidence IDs, resolved blocker retained as gray history, truthful older-server fallback. No second payload renderer or assistant. |
| `src/frontend/src/pages/tasks/workorders/execution/PreparedPackage.tsx` | Effective/latest scope boundary, retained revision comparison and dependency/authority limitations; conservative 408/429 uncertainty guard | Catalogue name/read-time drift, immutable instructions, current Part names with existing permissions, readable deviation facts, detailed native refusals, legible read-only uncertain drafts and no blind append retry. |
| `src/frontend/tests/s1/pui_work_attachments.spec.ts` | Offline font fixture and stricter GET/search/non-count response plus rendered-row synchronization | Both fixes' essential ordering: select only after search response and filtered rows. The HEAD synchronization is the single implementation. |

Integration corrections extend beyond the conflict files. `execution/workBriefProjection.ts` now reuses the existing `effectiveScope` helper, so Work cannot name an effective scope that Plan & Safety rejects. `execution/workBrief.test.ts` covers explicit unresolved projection and older-current fallback; its new case failed before the fix. `tests/s4/pui_work_diagnose.spec.ts` now asserts the preserved assistant's complete current refusal wording. `tests/s4/pui_work_package.spec.ts` covers retained 408/429 append uncertainty at both widths; the incoming 429 classification failed the new regression. This preserves HEAD's stricter boundary and prevents a blind repeat after a rate-limited response. The current merge disposition supersedes the older P21 report's 429-refusal classification.

## Ticket/file map for this reconciliation

- P19: `WorkOrderDetail.tsx`, `execution/workBriefProjection.ts`, `execution/workBrief.test.ts`, `tests/s4/pui_work_diagnose.spec.ts`; shared attachment synchronization retained.
- P21: `execution/PreparedPackage.tsx`, `tests/s4/pui_work_package.spec.ts`, `tests/s7/pui_draft_recovery.spec.ts` (shared recovery assertion).
- P23/P27: their already-merged verification/disposition/attempt-note/assignment changes remain; no additional ticket-specific product edit in this reconciliation.
- P29: `execution/InvestigationRequests.tsx`; shared `WorkOrderDetail.tsx` and attachment synchronization.
- P41: `execution/InvestigationRequests.tsx`; shared Diagnose wording assertion.
- P43: `WorkOrderDetail.tsx`, `execution/PreparedPackage.tsx`, `execution/workBriefProjection.ts`, `execution/workBrief.test.ts`.

Paths under `execution/` are relative to `src/frontend/src/pages/tasks/workorders/`; test paths are relative to `src/frontend/`. The complete new edit scope is the four conflict files plus five integration/regression files and these reports. The fifth additional file, `tests/s7/pui_draft_recovery.spec.ts`, asserts the preserved complete refusal message and native error; its draft, exact-attempt count and lost-response lock assertions are unchanged. Incoming staged changes are not new edits by this reconciliation.

## Actual verification

Evidence: `data/devscratch/r6-main-merge/S4-reconciliation/`. `commands.jsonl` records exact argv, working directory, exit status, duration and log for every validation invocation, including failures. All renders come from **Linux Chromium in `r6-main-browser-15210`**, with fixture harness **15173**, one Playwright command at a time. No dependency or browser download.

- Initial default plus all seven `tsconfig.s*.json` projects: **8/8 pass**. The same eight projects pass again after the product fixes. S7 types pass once more after the final test-copy alignment, all with zero diagnostics.
- Work-area plus Work-focus units: initial **142/142**, new scope regression **2 pass / 1 expected failure**, final **143/143 across 16 files**.
- Initial full S4: **150 pass / 4 fail of 154**, all four from the old Diagnose wording assertion. Logs/traces retained. No load timeout was involved.
- New deviation guard red-before: **2 pass / 2 expected fail of 4**; only 429 failed at each width. Final replay includes all four cases.
- Host `prek run --files`: initial four-file hook formatted one file, then passed. Expanded eight-file hook formatted three files, then passed; **6 applicable hooks pass** in the final expanded run.
- Final full S4 **158/158**, full S6 **80/80**, full S7 **64/64**, four required S1 Work specs **60/60**, S0 native Work **18/18**: **380/380 browser cases pass**, zero skips. S4 breakdown: continuity 16, Diagnose 14, AIMMS ledger 12, requests 34, observation 20, outcome 8, package 24, scope 10, assignment 8, verification 12. S1: assistant 20, attachments 12, materials 14, panels 14.
- First full S7: **62 pass / 2 fail of 64** from its old short refusal wording. The final assertion requires the complete S4 message and native error; all original draft/uncertainty assertions remain. The complete fresh S7 replay passes. This was a copy mismatch, not a shared-host load timeout; both logs/traces are retained.
- Final nine-source-file host hooks pass: **6 applicable hooks**, 12 nonmatching hooks skip. Final source/report hooks and whitespace/merge/evidence checks are recorded in the final command inventory.

**27 final-source captures** were inspected: desktop and 390 px phone Work outcome/continuity, package, saved/manual observation, resolved historical blocker, uncertain deviation, saved-answer focus/status, dark refusal and native attachment preview. Light/dark were reviewed for the changed coloured surfaces. `browser/reviewed/index.md` links convenient byte-identical copies; `captures-preferred.json` records exact original/copy paths, dimensions, hashes and review. Nine final images match the initially inspected originals byte-for-byte; the other final images were opened directly or inspected through unchanged-pixel phone crops. Eight tall-phone crop sources/offsets/hashes are in `review-crops.json`; original PNGs stay unchanged. No new high-impact visual defect remained. This is fixture rendering, not deployed login/storage, hardware, physical authority or R6 acceptance.

## Boundaries and handoff

No backend code was edited by this reconciliation and no Django test was requested/run here; earlier backend evidence remains historical in each ticket's report. No Azure access, app startup against Azure, migration, existing database write, shared-ledger edit, dependency install, git write, commit or PR occurred. Missing physical authority, service/acceptance, original media, durable drafts, atomic asset binding, affected-dependency and authoritative labor providers remain as described in the ticket reports.

The Git index intentionally remains unmerged until the coordinator reviews and stages the resolved files. Removing source markers is not a merge commit. The owned browser shutdown and closed-port checks for 15173/15210 are recorded in `final-verification.json` and the command inventory.

CI should retain all eight type projects, Work units, full S4/S6/S7, the four required S1 Work specs and S0 native Work. Add the effective-scope and 408/429 regressions and corrected assistant-copy assertion; keep failure traces and desktop/390 px light/dark capture review. No CI workflow or shared ledger is edited.
