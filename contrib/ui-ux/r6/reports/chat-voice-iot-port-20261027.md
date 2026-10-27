# Selective chat UI and voice port to IOT-Merge

Date: 2026-10-27.

Status: local integration implemented and focused qualification completed.
Human review and a decision on inherited backend failures remain required
before publication. This is not a full-green backend release or live-voice
acceptance report.

## Scope and provenance

- Published target baseline:
  `721c72f08869d52f52fdd3f126d64fbe9e4e72c2`.
- Published source:
  `4c60de27544dc5adf12467e1f1f11b6637871c47`.
- Local branch: `port/equa-chat-voice-to-iot-20261027-candidate`.
- Copied application-series head:
  `a36ec826261573617d6eabb467e2a482ad6bb57c`.
- The subsequent qualification commit adds this ledger, the handoff entry,
  and four native regression tests. It does not change application behavior.

Seventeen source commits were applied in dependency order using Git's
cherry-pick machinery. Original authors/dates and full source-SHA provenance
are retained in each resulting commit, along with the required attribution.
No existing commit was amended and no branch history was rewritten.

| Source | Local copy | Change |
|---|---|---|
| `c9701f0fb` | `dcb9bd06d` | Chat workspace and read-only OpenUI |
| `657e70b0e` | `ff4931d58` | Lookup routing and record context |
| `4db9cdf73` | `480afe952` | Scoped visualizations and native reader counterpart |
| `fc58212ff` | `5b558b39f` | Scoped analysis and explicit catalog search |
| `f54999264` | `3c80b1c87` | Voice lifecycle and native inventory grounding |
| `165a82a5f` | `570789c27` | Grounding fixture isolation |
| `2ea4f4327` | `6817fbb43` | Critical identifiers and exact part revisions |
| `7c7bd9dff` | `2d3e85269` | Complete category/location reads |
| `e211add68` | `e05e4e369` | Unknown BOM qualifiers |
| `fc0f28796` | `3b8b5303c` | Shrinking-viewport composer |
| `2f86eb188` | `ec9e20fa4` | Assertion-level reporting |
| `98b1e45ee` | `2a5d8505d` | Exact stock/catalog reads |
| `171937355` | `977c073cd` | Process ownership and explicit voice recovery |
| `a8984f915` | `71a5e27ff` | Compact machine scope and resize containment |
| `fcf7f74ec` | `c91001718` | Raw-evidence qualification rules |
| `1b923cb33` | `01b88ecad` | Exact voice reads and retest repairs |
| `4c60de275` | `a36ec8262` | Screen-only investigation publishing policy |

The Docker-only `f3baa865f` was intentionally omitted. Older source WIP and
the synchronization merge were not replayed: the synchronization tree and
common ancestor are identical. This was not a whole-branch merge.

The copied series changes 234 paths. All 229 non-overlap paths are
byte-identical to the pinned source. Paths outside the approved scope and
the target Dockerfile remain identical to the target, apart from the explicitly
added qualification documentation/tests.

## Conflict resolutions and preservation

- **Handoff:** Git's union merge preserved the independent source and target
  additions. A content-order check verified every original target handoff
  line remains. This new entry distinguishes the local port from historical
  source/deployment reports.
- **English catalog:** merged by parsed message identity/context and plural
  shape, preserving target translations and appending missing source messages.
  Verified all active source messages are present, all active target
  translations are unchanged, and 7,280 active identities are unique.
  All catalogs compile using the existing Lingui tooling.
- **Contracts CSV:** all 28 target rows are unchanged; six source extension
  rows produce 34 unique rows.
- **Backlog CSV:** all 60 target rows are unchanged; four source extension
  rows produce 64 unique rows. Parent statuses/dispositions were not promoted.
- **Retention:** retained the target's bounded operations receipt and
  session-by-session protected-voice cleanup, together with the source's
  thread-versus-owner/client erasure namespace isolation.
- **Native work reads:** retained the source changes to
  `src/backend/tasks/ai_read.py`, including exact machine filtering after
  authorization, numeric identity lookup and board-status projection.
- **Target-only behavior:** preserved alarm/work intake, governed closeout,
  trade-aware assignment, approvals, notifications, mimic state propagation,
  retained-table flush and the structural migration checkpoint.
- **Build:** kept the target Docker dependency stages, MariaDB handling,
  build identity labels and configurable build-only 6144 MB Node heap.

## Qualification results

These are separate evidence lanes, not counts to add into one acceptance total.
Component follow-ups and DTO-producing runs overlap the main component suite.

| Check | Result and boundary |
|---|---|
| Chat/voice/presentation/action-policy component selection | 841 passed across 46 modules |
| Reverse-order native grounding/presentation check | 40 passed; overlaps component coverage |
| Candidate-native DTO producers | 73 passed; generated 14 fresh synthetic ORM DTO files |
| Frontend changed-source and target-preservation units | 276 passed across 24 files |
| Chat browser fixtures | 155 unique cases exercised successfully: 121 in the initial run and 34 initially skipped for missing native DTOs, then passed with fresh candidate DTOs |
| Voice/evidence browser fixtures | 80 passed across desktop/mobile recording-only fixtures |
| Focused IoT browser preservation | Initial run: 18 passed, 2 timed out; the same 2 cases passed on focused recheck with unchanged code/assertions/timeouts |
| New/related PostgreSQL retention and migration selection | 48 passed, including all four new native regressions |
| Broader target-native PostgreSQL selection | 267 tests: 263 passed, 3 errors, 1 inherited irreversible-rewind skip |
| Report compiler | 10 passed |
| Voice topology guard tests | 2 passed |
| Application TypeScript | Passed |
| Presentation artifact drift | Passed |
| Generated backend/frontend wire contract | Passed |
| Lingui catalog compilation | Passed |
| Frontend production build | Passed with build-only `NODE_OPTIONS=--max-old-space-size=6144` |
| Repository hooks | Passed for every copied commit and for the complete copied range |

### New native regressions

[Retention scope and voice tests](../../../../src/backend/InvenTree/aichat/tests/test_retention_scope_and_voice.py)
prove together that:

1. Oversized detailed reports still persist a bounded native operations receipt.
2. Thread deletion neither executes nor probes owner/client erasure obligations
   whose string references happen to collide.
3. A protected voice operation retains its own session without preventing
   deletion of an unrelated session belonging to the deleted thread.

[Transport ownership migration test](../../../../src/backend/InvenTree/voice/tests/test_transport_owner_migration.py)
uses real PostgreSQL and the actual `0012` AddField operation. It verifies
upgrade preservation of an existing session and an old-model writer that
omits the new column after upgrade. The database supplies the empty owner
default. Fresh PostgreSQL setup also applied the complete target migration
graph, including its structural checkpoint and the new voice migration.

### Backend failures reproduced on the unchanged target

The following were not introduced or fixed by this port:

1. `aichat.tests.test_proposal_notifications.ProposalNotificationSweepTests.test_approval_bridged_proposal_is_not_warned_on_the_chat_rail`
   raises `ProposalStateConflict` because the repair-work-package fixture
   does not provide an integer `machine_id`.
2. `tasks.tests.test_workorder_voice_readback.WorkOrderVoiceExtendedTests.test_assign_reads_names_and_rejects_ambiguous_names`
   raises the native exact-assignee-name refusal.
3. `tasks.tests.test_workorder_voice_readback.WorkOrderVoiceExtendedTests.test_assignment_name_drift_disarms_and_target_correction_preserves_parameters`
   raises the same exact-assignee-name refusal.

Control evidence:

- The unchanged target's five-module PostgreSQL baseline ran 84 tests:
  the notification error reproduced, with one inherited rewind skip.
- A separate unchanged-target PostgreSQL run of the two exact assignment
  cases reproduced both errors and their messages.
- The candidate's broad run retained the same errors. No native assignment,
  scope or approval gate was relaxed to make these fixtures pass.
- The whole-voice rewind skip is the target's existing refusal to reverse
  the irreversible structural checkpoint. The new focused additive-migration
  test passed without reversing that checkpoint.

These failures still require an explicit owner decision before publication;
previous aimms-dev CI exceptions do not automatically apply here.

### Setup, ordering and browser limitations retained honestly

- The first native-runner attempt used a development image without globally
  installed Django. Subsequent runs used the existing project dependency
  environment mounted read-only into the same image, with fresh internal-only
  PostgreSQL and owned scratch configuration. No global dependencies or
  production settings were installed/changed.
- Adding the unchanged `test_voice_experience.py` module to the component
  selection produced 28 fixture-setup errors: its Django database setup collided
  with tables already created by the component-native fixtures. The same
  two-module ordering reproduced this on the published source. The 46-module
  main selection passed; an optional standalone experience run was stopped
  during database preparation and is not qualified here.
- The direct unmigrated `manage.py` wire invocation was refused by the native
  startup guard. The successful static check used the same isolated registry
  bootstrap and explicit backend import precedence as the component runner.
  No runtime migration guard was disabled.
- An initial over-broad IoT layout matrix was stopped after early navigation
  timeouts and later successful cases. It is not a completed full-layout result.
- The focused IoT run's two desktop alarm-dialog cases exhausted their
  45-second test budget. An unchanged-target control passed; both candidate
  cases subsequently passed with the same timeout and assertions, without
  code changes. Original timeouts remain retained; the focused result is
  rerun-qualified, not first-attempt green.
- Initial chat skips were coverage gaps, not passes. All 34 were subsequently
  executed against newly generated candidate-native DTOs, without historical
  source snapshots or invented expected values.

## Safety and remaining release work

The original dirty worktree's staged and unstaged binary-diff hashes remained
unchanged. Existing source/target worktrees were not reset, cleaned or switched.
The stale local `IOT-Merge` branch was not used as the integration base.
An initial one-commit trial branch remains separate; the final candidate
contains the full provenance/attribution-complete sequence without rewriting
the trial.

Test databases, networks and browser servers were task-owned. No real business
records, role grants, Azure resources or shared service lifecycles were changed.
No source/target push, PR, issue, ACR upload, container-app update, model change,
live migration or live voice/action campaign occurred.

Before publication:

1. Human review of the local candidate and the conflict decisions.
2. Owner disposition of the reproduced backend failures and retained browser
   timeout observations; this ledger does not grant an exception.
3. Recheck both published heads; reconcile drift and rerun affected checks.
4. Explicit approval of the publication route. No force-push or automatic PR.

Deployment, runtime action/model gates, session drain, replica/process routing,
live WebRTC, permission-based action execution and human/physical audibility
are separate release/qualification work. Copying these commits does not enable
the aimms-dev runtime configuration in IoT.
