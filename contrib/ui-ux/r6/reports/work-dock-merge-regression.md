# Merged Work dock focus regression — 21 October 2026

Bounded repair on `inventTree-aniket`, starting at `cef890495`. Applies
`masterdesignprompt.md` and `designprompt1.md` to the native Work screen.
Primary user: maintenance technician recording person-work time. Objective:
reach the actual time controls above the phone navigation using keyboard focus.
Data remains the native Work overview, operational-authority projection and
recorded-time commands; routes, permissions and safety wording are preserved.

## Safe-area follow-up

The coordinator reports macOS GPU S4 **232/232** and S6 **256/256** pass with
the initial focus repair. Their S8 safe-area replay then consistently fails:
clearance **161px** versus required **185.59px**. The S8 test measures the right
thing: Playwright's bounding box is the full rendered border box, and setting
`--work-safe-area-bottom: 34px` correctly emulates bottom padding rising from
8px to 34px. No S8 or S4 test changes are warranted or made.

The installed Mantine hook reads border-box size, then defers publishing it
through `requestAnimationFrame` and React state. The host trace shows the old
145px navigation variable during the assertion; it becomes 171px only afterward.
Thus the failure is stale sizing, not confusion between content and border boxes.
The original full-box calculation avoids double counting after it settles, but
cannot keep clearance synchronous with a CSS inset change.

The final product observes the border box so inset changes still update the
inline-navigation fallback, but publishes **`entry.contentRect.height`** (content
only) directly to a CSS variable. Workspace and dock share the same CSS padding
expression and border size. Reserved height is now rounded-up content + bottom
padding + top border; clearance adds Mantine spacing. For the reported geometry:
`ceil(135.59375) + 34 + 1 + 16 = 187px`, clearing the 170.59375px physical dock
with no duplicate padding or border. Insets update both dock and clearance in
CSS immediately. Wrapped authority text remains measured; the zero-duration
interactive-context repair remains; short/keyboard viewports retain inline nav.

Applied the installed `equa-mantine-inventree` and `equa-browser-testing` skills
alongside both design prompts. Only `WorkOrderDetail.tsx` and its existing CSS
module change in product code. All source, fixture, timeout and assertion checks
are retained. No dependency, role, API, authority or safety semantics change.

Follow-up evidence is `data/devscratch/r6-s8/dock-safe-area-followup/`:

| Check | Actual follow-up result | Evidence |
| --- | --- | --- |
| Supplied host safe-area trace | Confirms stale 145px total after inset change; updates to 171px later | `host-trace-review.json` |
| Own Linux baseline before follow-up | 1/1 passes; host failure is retained separately | `browser-before.log` |
| Unchanged safe-area case after follow-up | **3/3 passed** | `browser-focused-after.log`, `focused-after/` |
| Full s8-work project, including uplift spec | **162/162 passed**, uplift spec **52/52** | `browser-s8-work.log`, `s8-work/` |
| Full S4 time-recording spec | **18/18 passed**, both projects | `browser-s4-time.log`, `s4-time/` |
| Same-operation inset + native focus probe | **1/1 passed**: 0/34/72px changes and 34px time-control focus | `browser-geometry-probe.log`, `geometry-probe/` |
| All requested TypeScript projects | **9/9 passed** | `typecheck.log` |
| Relevant pure units | **24/24 passed**, three files | `unit.log` |
| Product-file hooks | Passed | `prek-product.log` |

Renders are Linux Chromium in the owned browserless container, WS **3005** and
fixture-only Vite **15236**. The focused safe-area and native time-focus captures were inspected: warning,
all native tabs, emulated bottom padding and unobstructed controls remain visible.
Three additional final-run phone-authority/enlarged-header images were inspected
independently (`independent-visual-review.json`); no high-impact regression was
found. Final source hashes and both unchanged specs are verified; changed-file hooks, tracking and whitespace checks pass. Owned browser/container
and harness are stopped; both ports are clear (`cleanup.json`, `browser-stop.log`). Both specs were compared
byte-for-byte with HEAD; final source hashes are in `source-manifest.json`.
The supplemental probe is ignored diagnostic code; neither original spec changed.
It mutates each inset and reads border-box height and computed scroll margin in
one synchronous browser evaluation, allowing no observer delivery or animation
frame. Actual results: 0px inset → 144.59px dock / 161px clearance; 34px →
170.59px / 187px; 72px → 208.59px / 225px. Clearance beyond the dock stays
16.41px throughout, proving no duplicate padding/border. At 34px inset, native
focus moves time controls from below the dock; all three remain above dock top
673.41px and all center hit-tests succeed. The test's original S8 result likewise
records 170.59375px height and 187px clearance (`s8-work-summary.json`).

P31 remains In progress and P32 Ready for review; the backlog was audited without
promoting parent dispositions. Earlier evidence below is historical, not fresh
verification of this follow-up. No deployed/product acceptance or Azure use.

## Initial cause and product repair

The supplied macOS failures and an isolated Linux reproduction show that the
merged authority wording alone does not explain the overlap. The dock already
has a live `useElementSize` measurement. In the supplied trace its measured
144.59px matches the actual dock. During disclosure expansion, Mantine creates
an `overflow: hidden` container with a 200ms height transition. Focusing Start
work scrolls that temporary container internally while its geometry is moving,
leaving controls clipped or behind the dock. The supplied desktop replay also
failed hit-testing despite passing the vertical bound. Local red-before:
phone control bottom 729.17px, dock top 699.41px.

`WorkOrderDetail.tsx` now opens the interactive context Accordion immediately
(`transitionDuration={0}`), preserving its mounted form state while eliminating
the temporary scroll container. Dock measurement explicitly observes the border
box, including wrapped authority wording, safe-area padding and border. Its
rounded-up height feeds the existing CSS clearance/padding variable directly;
safe-area padding is no longer added twice. Before measurement, the existing CSS
fallback applies. The inline-navigation fallback remains intact.

No test, assertion, timeout, fixture or dependency was changed. In particular,
the original geometry and `elementFromPoint` assertions remain unchanged.

## Initial verification

Evidence is retained locally in `data/devscratch/r6-s8/dock-clearance-merge/`.
Browser runs use **Linux Chromium in the owned browserless container**, WebSocket port **3005**, fixture-only Vite
port **15236**, one worker and mocked native routes. No macOS GPU rerun or
deployment acceptance is claimed.

| Check | Actual result | Evidence |
| --- | --- | --- |
| Original focused case before repair | Desktop passes; phone reproduces overlap | `browser-before.log`, `before/`, `trace-review.md` |
| Entire unchanged `pui_work_time_recording.spec.ts` | **18/18 passed**, both s4-desktop and s4-phone | `browser-final-spec.log`, `final-spec/` |
| Final focused case repeated five times per project | **10/10 passed** | `browser-final-repeat.log`, `final-repeat/` |
| `tsc --noEmit --project <config>` | **9/9 passed**: default plus s0/s1/s2/s3/s4/s6/s7/s8 | `typecheck.log` |
| Relevant pure units: panel state / attention / projection | **24/24 passed**, three files | `unit.log` |
| Product-file prek hooks | Passed | `prek-product.log` |

The full command inventory is `commands.md`; final source hashes are in
`source-manifest.json`. The original spec was also compared byte-for-byte with
HEAD using read-only `git show`. Final changed-file hooks, tracking and whitespace
checks pass; see `prek-final.log` and `tracking-final.log`. Cleanup is recorded in
`cleanup.json` and `browser-stop.log`; both owned ports are clear and the owned
container is removed.

Inspected the red-before phone capture and both final regression screenshots:
the Start/Pause/Stop controls are visibly above the fixed dock, the full physical
authority warning is readable, and the unchanged hit-testing assertions pass.
Both final project captures of this 390×844 case are byte-identical. Additional
light controls and dark supervisor-correction region captures were inspected;
the latter includes sticky-header overlap from the existing regional screenshot
method and is not presented as a full-screen acceptance image.

The intermediate six-case focused replay passed while the final border-box
cleanup was being applied; it is diagnostic evidence, not the final-source
acceptance run. Separate final-source logs and screenshots are retained.

P31 remains In progress and P32 Ready for review; `sprint-backlog.csv` was audited
without changing parent dispositions. D04/D07 provider and authority semantics
are unchanged. O22 scoring, deployed checks and product acceptance stay separate.
No Git write command, issue/PR, Azure connection, backend/database operation,
dependency installation or other container/worktree modification is authorized
or performed by this repair.
