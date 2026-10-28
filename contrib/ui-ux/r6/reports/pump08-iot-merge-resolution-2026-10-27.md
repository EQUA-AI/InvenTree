# Pump08 local changes with latest IOT-Merge

Date: 27 October 2026. Owner authorizes careful conflict resolution while
preserving the local recording features.

## Source integration

The current `inventTree-aniket` checkout fast-forwards from `721c72f08` to
`7e33232c7`, bringing in all eighteen fetched IOT-Merge commits. Existing
local modifications and untracked files are reapplied. All six conflicted
paths are resolved; the index is returned to its original unstaged state.
No new commit, push, PR, deployment or recording is performed.

Recovery materials are retained at
`data/devscratch/pump08-iot-merge-20261027/20261027T200010Z/`:
private working-file archive, binary tracked patch and per-file SHA256
manifest. Recovery stash `5b50332be88623b9c2494034888e4c3a216f319e`
is retained. At snapshot time there are63tracked edits and31untracked files,
including the inventory report added after the earlier30-file inventory.
The separate dirty exercise worktree and external recorder assets are untouched.

## Conflict decisions and preserved behavior

| Path | Resolution |
| --- | --- |
| HANDOFF.md | Both sets of execution history and upstream qualification evidence remain; current integration status is recorded separately. |
| ai/core/entities.py | Retains upstream actor-authorized observed machine/work resolution and the local bounded observed-work helper. |
| ai/core/turn_service.py | Uses upstream fresh actor/scoped record resolution, including revoked-access handling; retains local diagnostic focus selection and avoids adding a fallback to earlier authorization. |
| ai/core/workflows/wf8_lookup.py | Composes both native stock/catalog grounding rules and local station operating-state rules for every non-clarification agent. Existing credential refusals and correction/source rules remain. |
| ConversationFocusControl.tsx | Retains upstream asynchronous recovery, target version checks and shared overlay ownership. These supersede the local parent-controlled picker workaround; busy dismissal and Escape isolation remain covered by browser tests. |
| AIChatDrawer.tsx | Keeps the new workspace, drafts, uploads, charts, responsive geometry and overlay handling; retains complete local machine hints. Restores typed-send focus after both context confirmation and model completion without taking focus from another active control. |

Twelve other overlapping paths merge textually. Their combined behavior is
included in the focused checks. Non-overlapping event-owned alarms, staffing
escalation, packet lifecycle, separate winding readings, exercise verification,
forebay presentation, alarm charts and local controlled-document/provider code
are retained. Local and incoming safety/authorization gates are not relaxed.

An older context test expected the previous launcher/menu names and a locked
draft during target confirmation. The current UI uses Open AI chat and the
header New conversation action. Tests now assert those controls and the new
editable-draft behavior while continuing to require disabled duplicate sending,
exact context acknowledgement and zero premature requests. Existing upstream
draft-revision/admission tests verify that editing cancels an obsolete send.
The focus-restoration assertion is retained and exposed a missing focus-token
assignment, which is restored in the integrated drawer. The native launcher
test now verifies refusal before an explicit picker selection, then proves
that the newly confirmed selection supersedes the earlier page hint while
preserving the draft. This preserves the upstream intent-supersession behavior.

## Preservation evidence

- At the post-merge checkpoint all76local non-overlapping files match their
  pre-integration SHA256 hashes. At final verification74still match; the two
  explicit later revisions are the runtime ledger and context integration tests.
- All219incoming non-overlapping files match the fetched commit byte-for-byte.
- AST comparison across37modified Python files finds no missing definitions
  added by either local edits or incoming commits relative to the shared base.
- No unmerged index entries remain. The original untracked files are present.
- All94original snapshot paths remain. Both tracking CSVs have consistent
  columns and unique IDs:72backlog rows and42contract rows.
- These checks establish source preservation, not universal feature acceptance.

## Validation ledger

Evidence root: `data/devscratch/pump08-iot-merge-20261027/`.

| Check | Actual result |
| --- | --- |
| Application TypeScript | Pass after installing the incoming locked dependencies. |
| Focused frontend units | 125pass across11files, covering charts/drafts/voice and local verification/packet helpers. |
| Backend components | 263pass across11modules, including operating-state routing, exact lookup, local provider, diagnostics, targets, entity manifests, native grounding and presentation. |
| Fresh native DTO producers | 78pass across4overlapping modules; fourteen synthetic native DTO artifacts produced from this integrated source. These overlap the component selection and are not additive coverage. |
| Chat picker/modal/workspace/visualization browser run | 40pass and34initially skipped for missing DTOs; skips are coverage gaps, not passes. |
| Complete visualization browser rerun | 57pass with newly generated native DTOs and mandatory DTO input enabled; covers the initially skipped visualization cases. |
| Draft/admission browser run | Final post-focus-fix run:8pass, including editing during held confirmation and exact admitted snapshot consumption. |
| Native machine context browser | Complete final run:36pass on desktop/phone; retains scope refusal, exact acknowledgements, picker Escape/isolation, intent supersession, duplicate-send and restored-focus checks. Earlier failures remain in separate logs. |
| Event-owned work and Pump08 intake browser | Initial59pass/1failure opening the dark intake dialog. All4configured-provider light/dark desktop/phone cases pass unchanged on focused recheck; all60unique cases are exercised successfully with rerun qualification. |
| Forebay and station mimic browser | 33pass across state, count and desktop/tablet/phone checks. |
| PostgreSQL native lifecycle and voice migration | 112pass using native production settings, a complete migrated schema and an internal-only disposable PostgreSQL16 container. Includes event-owned intake, replay guards/history, separate winding checks, native closeout readers/transitions, station states, retention and transport-owner migration. |
| Lingui catalog compilation | Pass. |
| Presentation artifact drift | Pass. |
| Whitespace/conflict checks | Final pass; no unmerged index entries or staged changes remain. |

Actual screenshot inspection covers the390px compact chat/voice controls,
desktop native-shell focus picker and phone forebay/legend. The phone mimic
uses its existing horizontal scrolling; this is not evidence that all fourteen
pumps fit into a phone frame or that the original film's missing pixels are fixed.

Backend source is mounted read-only. The existing development image and
dependency environment are used with task-local pytest packages. Initial
attempts identify absent pytest and a wrong relative test path; these are
setup failures, not application passes. The first PostgreSQL test attempt
applies the full schema and passes system checks, then its optional slow-test
reporter tries writing into the read-only source mount. The follow-up uses
Django's standard runner in task-local settings. Original attempts are retained.
The new voice0012migration is independently observed in the disposable database
after the successful run. Owned browser server, PostgreSQL container and internal
test network are stopped/removed after verification; recovery materials remain.

## Runtime boundary

Only the new disposable test database receives migrations or synthetic test
records. The recording/primary databases and deployment configuration are not
updated. The incoming voice transport-owner migration remains to be applied
through the normal recording-runtime preparation path. Browser checks here
exercise native components with mocked auth/API and fresh synthetic DTOs.
They do not prove an authenticated admin walkthrough, live model/provider
answers, physical equipment authority, full-film audio review or final V5
script acceptance. Those remain separate recording-readiness work.
