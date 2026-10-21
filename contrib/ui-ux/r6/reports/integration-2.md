# Integration 2 — P31 Work merge

Status: **built and tested for the bounded merge and compatibility fixes**. All requested browser cases have passing evidence after the narrow fixture corrections below. S1/S7's initial full runs and their subsequent passing whole-spec replays are reported separately. This is native synthetic-fixture engineering evidence, **not deployment, R6/product acceptance, hardware validation or an independent human audit**.

Main checkout/branch: `/Users/Aniketkumar/IdeaProjects/InvenTree`, `inventTree-aniket`. HEAD: `ae4595ab80e3a0f268bcc62cd8532338496ecce5`; incoming P31: `b79e604b0004619d1595e46df7e4c219a4675415`; merge base: `873a104e4f4e6f7ca8a8d9a92c7b0730021b836b`. **All changes remain uncommitted; the index is untouched.** The coordinator stages the six resolved paths and completes the merge after review.

## Scope and conflict decisions

Read the supplied machine memories, AGENTS/CONTRIBUTING, handoff, both design prompts, parallel brief, owner decisions, existing ticket reports, authoritative R6 references and operational semantics. Apply the master design prompt/designprompt1 to the native Work, Plan & Safety, Closeout, observation and assistant surfaces for technicians and engineers. Existing React/Mantine components, canonical identities, permissions, endpoints and unknown authority/service states remain authoritative. No available image/OpenAI/skill-authoring skill applies to this merge; no dependency, Figma or external integration was needed.

Both branch versions and the merge base were inspected with read-only `git show`; copies are retained in [versions](../../../../data/devscratch/r6-integration/integration-2/versions/). The two S8 configs did not exist at the merge base.

| Conflicted file | Resolution |
| --- | --- |
| `src/frontend/playwright.s8.config.ts` | Keep 120-second test and 15-second expect budgets. Desktop and 390px phone projects ignore `pui_p31_work_*`; `s8-work` matches those four specs once and preserves their own viewports. Discovery and the complete run confirm 78 permission/guard + 93 Work cases. |
| `src/frontend/tsconfig.s8.json` | Union both file lists: permission matrix/guard and all four P31 Work specs; imported helpers are typechecked transitively. |
| `src/frontend/src/components/nav/AIChatDrawer.tsx` | Retain component/evidence/Work labels, matching remove-focus accessible names and translated request suffix. Keep P31's keyboard disclosure, complete wrapping title/badge with small radius, translated controls and full-width composer without an occluding tooltip. Explicitly handle P02's nullable Work ID; reference/evidence focus gains no invented Work ID. |
| `src/frontend/src/pages/maintenance/workorders/components/WorkOrderSafetyReadiness.tsx` | Keep useEffect/useState and the authority-expiry clock, plus WorkTableScroll around the complete safety table. The S4 expiry regression passes at both widths. |
| `src/frontend/src/pages/tasks/workorders/CloseoutPanel.tsx` | Return update.execute's promise **with packetId**. Preserve captureEnabled, P32 capability checks, captureAvailable-specific recovery and E1's “Attach original audio in Dictate…” text. Retain P31 result-focus handlers/groups, accessible names, heading levels and table scrolling. |
| `src/frontend/src/pages/tasks/workorders/execution/NativeObservationCapture.tsx` | Keep OriginalMedia and useActionFocus. Preserve original-media receipts, explicit command result focus, dropdown-first Escape handling, trigger focus restoration and retained drafts. |

## Additional compatibility files

No product authorization or source rule was weakened, and no original behavior assertion was removed.

- `src/frontend/tests/s1/pui_reference_focus.spec.ts`: six send/close locators use the existing data-testid hooks, matching P31's translated accessible names. Identity, receipt, refusal, send and return assertions stay intact.
- `src/frontend/tests/s8/pui_permission_matrix.spec.ts`: one stale “Fit not verified” expectation becomes O15's existing “Slot assignment not verified”. Unavailable/no-stock-link/no-write assertions remain.
- `src/frontend/tests/s8/workAccessibilityFixtures.ts`: a shared fixture presents explicit command permissions through the native UserState store, retaining CRUD roles and avoiding the public setter's unrelated chat/focus side effects.
- `src/frontend/tests/s8/pui_p31_work_commands.spec.ts`, `pui_p31_work_keyboard.spec.ts`, `pui_p31_work_reflow.spec.ts`, `pui_p31_work_review.spec.ts`: positive scenarios receive closeout.capture for reading/story/custody refresh, closeout.review for decisions, or workorder.complete for completion focus and enabled-action layout checks. All keyboard, focus, result, write-count and containment assertions remain; P32's negative matrix keeps its original permissions.
- `src/frontend/tests/s7/pui_monitoring_continuity.spec.ts`: locate “Original completed account”, the E2 label for preserved legacy fields, instead of implying an accepted frozen snapshot exists. Keep the exact original-result, recurrence identity/body, one-write and containment assertions.
- `src/frontend/tests/s1/pui_work_materials.spec.ts`: match O15's current distinction that fitted identity requires an exact slot and recorded serial/lot; material use alone does not establish it. Preserve stage quantities/recorders, stock/native links, missing facts and no-manual-chore assertions.
- This report, `contrib/ui-ux/r6/reports/integration-2.md`.

These are **16 integration-edited files**, listed in [ticket-files.json](../../../../data/devscratch/r6-integration/integration-2/ticket-files.json), separate from the inherited P31 merge changes. No backend, shared ledger, CI workflow, dependency or other checkout was edited. No Git write command, issue, PR, external discussion or Azure operation ran.

## Checks actually run

Evidence root: **`data/devscratch/r6-integration/integration-2/`**. The [complete command ledger](../../../../data/devscratch/r6-integration/integration-2/commands.md) records every validation command, environment, exit and count, including failed/interrupted attempts; [commands.jsonl](../../../../data/devscratch/r6-integration/integration-2/commands.jsonl) is its machine-readable source. [Supporting commands](../../../../data/devscratch/r6-integration/integration-2/infrastructure-commands.md) record Git/source inspection, Docker setup, probes, copies and cleanup limitations.

| Check | Actual result |
| --- | --- |
| `node node_modules/typescript/bin/tsc --noEmit`, then each `--project tsconfig.s*.json` | **9 projects pass**, zero diagnostics: default, S0, S1, S2, S3, S4, S6, S7, S8. S1/S7/S8 checked again after fixture edits. |
| Clean-environment startVitest, config vitest.config.ts, run:true, envFile:false | **693/693 tests, 68/68 files pass**. |
| Full S8 | **171/171 pass** in one complete final command, including every P31 guarantee and both P32 widths. |
| Full S7 | **168 pass / 4 fail**, all four the old E2 label lookup. Subsequent isolated **whole monitoring-continuity spec: 42/42 pass**, including all four repaired cases. |
| Full S6 | **256/256 pass** in one command. Includes original media/frozen reports, calibration, routine findings, reconciliation, final update and independent service states. |
| Full S1 | **202 pass / 2 fail**, both the old fitted-identity sentence. Subsequent isolated **whole Work-materials spec: 14/14 pass**, including both repaired cases. |
| Full S4 | **232/232 pass** in one command. Includes authority expiry/roles/history, observation recovery, hold/handover, scope, time and verification. |
| Host `prek run --files` on all 16 integration files | **6 applicable hooks pass / 12 skipped** on the final clean run. Earlier formatter-changing exits are retained. |
| Repository marker/index/whitespace audit | **0 conflict-marker lines**, diff whitespace clean. Exactly the original **6 expected index-U paths** remain because staging is prohibited. |
| Bundle/source verification before browser cleanup | **170 active container bundle files match host hashes; 1,048 source/fixture hashes checked with zero drift**. |

The five requested full suites contain **1,035 distinct cases**. All have passing evidence across the full runs and the two whole-spec replays. **S1/S7 were not rerun in full after their wording corrections**; their initial failures remain visible above. No failed or partial attempt is called acceptance. Backend tests were not run: this task changes frontend merge glue and fixtures only, with no backend field/endpoint change.

All browser commands use harness port **15173**, one Playwright command at a time. The common executable is `node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-integration/integration-2/browser.config.mts --reporter=list`, with AIMMS_BASE_SPRINT set to s8/s7/s6/s1/s4 and a unique AIMMS_BROWSER_OUTPUT. The two replays append `tests/s7/pui_monitoring_continuity.spec.ts` and `tests/s1/pui_work_materials.spec.ts`, respectively. Exact env -i prefixes, Vitest program, individual type invocations and every hook path are in the command ledger.

Retained diagnostics include the initial nullable translation-placeholder type error (fixed), a no-files hook invocation (not lint evidence), scratch NODE_ENV/CSS-compiler and dropped-export setup failures, interrupted slow-module runs and positive P31 fixtures lacking the newly required command grants. No test deadline was increased and no assertion was relaxed for load. The two failed full-suite specs were rerun alone with both original and passing logs retained.

## Browser environment and capture review

Renders came from **Linux Chromium in the dedicated `r6-integration-browser-15210` Docker container**, WS 15210, the specified Linux executable and exposeNetwork `<loopback>`. The scratch config extends the actual sprint configs, preserving their deadlines and single worker. No backend/dev/Azure application was started; API/media/static guards remain active.

The initial Vite loopback/direct-host attempts were retained separately. For the completed replay, native development fixture entries were compiled from this checkout using installed Vite and NODE_ENV=development, following P31's existing bundle pattern. Rollup preserves the UserState entry exports and shared store used by permission/phone fixtures. The owned container serves that bundle internally at `172.17.0.2:15173`; only that fixture origin receives localhost-equivalent secure-origin treatment. Native HTML/store probes return 200 and `/api/` returns 503. The bundle's large-chunk warning is retained, not represented as a production-build certification.

The host Vite fixture server remains for S1's existing evidence-page guard, which accepts only loopback. The scratch config partitions that spec into otherwise identical desktop/phone projects; all other S1 cases retain original project names/viewports. Every original case is discovered once at each intended width. The config retains the requested frontend webServer cwd and explicitly reuses the verified same-checkout Vite process.

Saved **3,340 PNGs**, including historical setup/partial attempts. The five requested full runs and two isolated replays contain **2,686 PNGs**:

| Capture directory under browser/ | PNGs |
| --- | ---: |
| s8-complete | 576 |
| s7-full | 474 |
| s6-full | 581 |
| s1-full | 321 |
| s4-full | 562 |
| s7-monitoring-retry | 152 |
| s1-materials-retry | 20 |

Personally opened **23 actual captures**; paths and individual observations are in [inspected-captures.json](../../../../data/devscratch/r6-integration/integration-2/inspected-captures.json). They cover desktop/390px, light/dark and enlarged text: assistant context/composer/authority notice, P02 reference/evidence chips, manual observation/media metadata, Closeout source/review/update, attachment columns, authority records and the actual expiry-readiness table. The final phone update sits above the dock; long context wraps; absent physical/service facts stay explicit; native tables retain scrolling. No further product change was needed from these inspected captures. This is sampled visual review, not inspection of every PNG or accessibility certification.

Every capture's dimensions and hash are in [capture-manifest.json](../../../../data/devscratch/r6-integration/integration-2/capture-manifest.json). [verification-manifest.json](../../../../data/devscratch/r6-integration/integration-2/verification-manifest.json) records source revisions, final file hashes, counts and evidence boundaries. Partial attempt directories and traces are not relabelled as completed runs.

## Cleanup, open items and CI

`docker stop r6-integration-browser-15210` **passed**, stopping only the owned browser and internal fixture server; port 15210 is clear. The interrupted initial wrapper left its own host Vite process **PID 22056 on 15173**. lsof confirms main checkout src/frontend as its cwd. Host kill of that owned process is sandbox-denied (`operation not permitted`), so it remains for host-side cleanup. This is recorded in the supporting-command ledger; no other process/container was stopped.

Coordinator review, staging and merge completion remain intentionally manual. Existing deployment, authenticated-session, real storage/codec/hardware, broader P31 machine/station and provider acceptance gates remain outside this bounded integration; no missing authority or physical fact is supplied by these tests. No Hyperagent score or independent human review is claimed.

CI should collect the two permission projects plus the single P31 Work project without duplicates; retain the union S8 types, default/all sprint types, all pure units and full S8/S7/S6/S1/S4. Keep explicit positive command grants alongside P32 negatives, original-media/legacy-account/frozen-report distinctions, exact P02 identities/receipts and packet routing/locks. Preserve failed-plus-isolated logs and source/capture hashes. No shared ledger or workflow was edited.
