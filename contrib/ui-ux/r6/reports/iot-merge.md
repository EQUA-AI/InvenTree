# IOT-Merge integration — 21 October 2026

Coordinator-started merge on `IOT-Merge`: OURS
`975b3cac6a9efe343c1233aa284902cdf6d51031`, THEIRS
`2b533a6274dc219cc8b1a6c44901e2996cdc0da6`, base `fe0d73a4a`.
Worktree resolutions remain unstaged. No Git write command, Playwright, server,
Azure connection, or read of `data/azure-db.env` was performed. No issue, PR,
deployment, rendered-screen verification or product acceptance is claimed.

## Conflict decisions

| Conflicted file | Resolution |
| --- | --- |
| `ai/core/agui/models.py` | Accept both optimistic `expected_focus_version` and signed `work_focus_receipt`; retain their bounds and defaults. |
| `ai/core/agui/routes.py` | Carry both request coordinates; retain Work receipt refusal before streaming and conversation-focus error/redaction handling. |
| `ai/core/app.py` | Keep both endpoint families, thread/turn focus projections, Work receipt verification and current-scope response redaction. |
| `ai/core/tests/test_capability_broker.py` | Union tool catalog and all test methods; update exact catalog/manifest expectations to include both parents' tools and authorizers. |
| `ai/core/tools/capabilities.py` | Keep focus capabilities and investigation capabilities with native authorizers and O24 scope requirements. |
| `ai/core/turn/execution.py` | Preserve conversation context reauthorization, capture/scope binding, Work/request upload routing and both model context projections. |
| `ai/core/turn/intake.py` | Pass both coordinates into atomic intake; conversation version stays in fingerprint, expiring receipt stays outside it; retain both original snapshots on replay. |
| `ai/core/turn/state.py` | Retain separate typed conversation snapshot/state and verified per-turn Work snapshot. |
| `ai/core/turn_service.py` | Forward both focus inputs and preserve both error/result paths. |
| `ai/core/workflows/wf8_lookup.py` | Keep full native read tools, conversation continuity guidance and verified Work context; neither is a permission grant. |
| `aichat/management/commands/generate_wire_contract.py` | Emit complete Conversation focus and Work focus sections and both response/request fields from backend definitions. |
| `aichat/media_stream.py` | Keep O24 native attachment-owner checks and Markdown DOC-ingestion requirement, revision/byte integrity and existing media fences. |
| `aichat/services/threads.py` | One row lock admits conversation version and verifies Work receipt; both immutable snapshots persist/replay, with Work identity comparison and current native authority checks. |
| `aichat/tests/test_evidence_media_stream.py` | Retain all tests and the isolated byte-drift fixture; original mismatch rejection is unchanged. |
| `lib/types/AimmsWire.generated.ts` | Regenerated offline using the resolved repository generator; no manual merge. |
| `components/nav/AIChatDrawer.tsx` | Keep conversation control and typed/voice preflight alongside Work chips, capability/receipt acknowledgement and draft retention on refusal. |
| `hooks/AguiTransport.ts` | Send both focus coordinates together and retain both typed error families. |
| `hooks/UseAIChat.tsx` | Keep numeric conversation-version callers and Work-options callers; both legacy/AGUI wires, hydration fences, capabilities, result linkage and conflict handling survive. |
| `pages/maintenance/MaintenanceBoard.tsx` | Retain canonical board-card conversation targeting and integrated R6 board identity/layout. |
| `pages/tasks/workorders/WorkOrderDetail.tsx` | Keep R6 layout and attachment resource, signed Work launcher, and durable canonical-PK conversation launcher; distinguish labels without changing original test IDs. |
| `states/AIChatState.tsx` | Retain both hint types and entry actions; clear both across thread/session/close boundaries and retain request-version progression/focus restoration. |

Backend paths above are relative to `src/backend/InvenTree/`; frontend paths
are relative to `src/frontend/` or `src/frontend/src/` as appropriate.

## Coexistence and scope

Conversation focus is an owner-updated continuity target with an optimistic
version. Work focus is a signed, reauthorized per-turn identity. Analysis scope,
native permissions and O24 rules remain independent authorization fences.
Neither focus expands them. A verified Work target can answer a pronoun when
there is no conversation target. With both targets present, their machine and
exact parent Work coordinates must agree for anaphoric routing; a conflict or
revoked conversation target yields clarification before model/tool execution.
Matching Work provides the more specific route. Explicit target switches retain
the conversation-focus command path. Both historical snapshots and response
redaction remain intact.

Additional integration fixes are in `ai/core/focus/commands.py` and
`ai/core/turn/routing.py`. `aichat/services/work_focus.py` now applies the same
DOC-only Markdown rule as the merged stream provider before acknowledgement or
receipt revalidation, without reading file bytes. New regressions cover these
boundaries, both snapshots, stale version admission and combined frontend wires.
No existing test was removed or newly skipped. Test inventory evidence is
`data/devscratch/iot-merge/test-preservation-final.json` and
`card-test-preservation.json`.

The old pytest redaction positive controls now receive a real, read-only native
Work-view role through `focus_test_db.grant_work_view`. O24 requires that role
before checking machine-client grants. These remain ordinary users; every
original denial, revocation and redaction assertion is retained. This fixes the
merged fixture contract without relaxing production authorization. Independent
agent source review of intake, context, routing and all response adapters found
no additional actionable merge regression; it is not human acceptance.

The checks preserve the ordinary-user authorization boundary and the
attachment/XSS defenses discussed in
[the threat model](../../../../docs/docs/concepts/threat_model.md). No telemetry
control operation, administrator/plugin trust assumption or deployment policy
was changed. This integration report follows the repository
[security guidance](../../../../docs/docs/SECURITY.md); no vulnerability issue
or public disclosure was filed.

## Migration graph and generated wire

Added only `aichat/0056_merge_focus_and_investigation`, with `operations = []`
and dependencies on `0054_investigation_proposal_action` and
`0055_chatthread_focus_db_defaults`. Existing migrations, including the
production-applied investigation migration, retain their original names/bytes.

Offline `showmigrations` and `MigrationLoader.detect_conflicts()` pass; there
are no multiple leaves in any installed app. Relevant leaves are aichat 0056,
tasks `0041_db_defaults_for_old_code`, users
`0026_ruleset_operating_authority_db_default`. The fresh isolated runtime DB
has no migrations applied; this is graph evidence, not production DB status.

`makemigrations --check --dry-run` exits 1 identically on both parents and the
merged source: six memory-model ID fields and `MemoryFact.supersedes` need the
same existing alterations. The proposed migration is numbered 0056 on OURS,
0055 on THEIRS, and 0057 on the merge. No new model drift is introduced, and no
unrequested drift-fix migration was generated. Logs: `management/`,
`management-ours/`, `management-theirs/` under the evidence root below.
Normalized field-operation signatures also match across all three sources
(`drift-comparison.json`); only process-local callable/object addresses were
normalized. The direct command's migration-specific currency handling differs
from shell introspection, whose additional currency-field operations also match
both parents. `migration-preservation.json` confirms all 355 OURS and 370 THEIRS
migration files retain their bytes.

Direct `manage.py generate_wire_contract` stops at the native empty-database
startup guard. Offline `manage.py shell -c` calling the actual generator via
`call_command('generate_wire_contract')`, followed by its `check=True` mode,
passes. Its output was copied bytewise into the generated TypeScript artifact.
This uses the baseline's clean temporary SQLite/config/source bootstrap and
the same network-disabled image; no startup guard or product code was changed.

## Verification

Evidence root: `data/devscratch/iot-merge/`. Backend runner image:
`sha256:89981388aff5f8329596f78ec9ce1bfb141ff9369e52fc7f9ae19305440fa96e`.
The unchanged baseline runner creates disposable containers with network none,
read-only source/root, clean environment and temporary SQLite/config/media.
Both parent snapshots use exactly that same runner/profile and repository
extras, allowing direct failure comparison.

| Check | Actual result |
| --- | --- |
| Focused aichat Work/component/evidence/media | 110 tests passed, including three new integration regressions; `aichat-focus/`. |
| Focused frontend focus/state/transport | 109 tests / 6 files passed, including three new integration regressions. |
| Full backend `ai aichat tasks voice users` | Initial merged run: 2,959 tests, 23 failures, 89 errors, 97 existing skips; exit 1. Nine merge-caused board-card fixture cases were then repaired and pass the focused module replay. The remaining 103 failure entries are inherited; case classification below. |
| Board-card identity replay after repair | 10 tests passed; `backend-card-identity/`. All 10 original test bodies are AST-identical to OURS (`card-test-preservation.json`). |
| Focused AI focus/redaction pytest island | 193 passed across 10 modules, including nine combined-focus regressions; `ai-island-focus-security/`. |
| Capability broker pytest | 1,852 passed / 15 inherited failures; OURS 1,846 passed / same 15 failures, THEIRS 228 passed. `ai-island-broker-comparison.json`: no merge-only failing nodes. |
| `tsconfig.json` and every `tsconfig.s*.json` | All nine pass (s0/s1/s2/s3/s4/s6/s7/s8 plus default); `frontend/tsc-results.json`. |
| Full `npx vitest run` | 924 tests / 92 files passed; final replay after TypeScript repairs is retained in `frontend/vitest.log`. |
| Changed-file prek, tracking, whitespace and marker scan | All 881 changed merge/supporting files pass after Ruff formatting; tracking, whitespace and source marker scans pass. Final logs: `prek-final-all.log` and `final-checks.json`. |

The initial TypeScript run identified a notification import lost at the merge
and a Work ID reference moved outside its scope by the R6 refactor. The Drawer
import and canonical `workOrder.id` reference are restored; all nine configs
then pass. Initial failing logs remain under `frontend/initial-types/`.

The development baseline image lacks pytest, so its Django discovery cannot
verify the function-based AI tests. Those additional tests use the already
cached `aimms-merged-image:latest`, image
`sha256:ab1b7ca2e4582f28eb9d155e3f8edb95c2463e646661a502c1d9afdac136afee`,
with the same offline/read-only/container isolation. The stock island launcher
hits native empty-database startup classification; a scratch launcher calls
`pytest.main` with the native `test` argv marker during bootstrap. Product
startup guards, settings and repository runners remain unchanged. Failed
launcher attempts and intermediate test failures are retained separately; the
193-pass and broker results above name the completed verified runs.

The full Django command was:

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/iot-merge/backend ai aichat tasks voice users
```

The five labels are separate arguments. Both parents use the same command and
image against read-only `git archive` snapshots under `parents/`, with their
own output/SQLite directories. OURS completes 2,110 tests with 19 failures,
87 errors and 97 existing skips. The optional full THEIRS replay was stopped
in expensive migration restoration after more than 21 minutes of CPU work;
it is explicitly **incomplete**, with no final test total or success claim.
Only its exact task-owned, network-disabled container was stopped, after
checking the source mount. `backend-parents/theirs/interrupted.json` retains
that decision; its partial log and runner manifest remain available.

Nine merge-caused cases in `tasks.tests.test_kanban_card_identity` were missing
the native O24 Work-view role in their positive-control fixture. Added only a
real read-only group/ruleset to the ordinary actor; all existing test bodies,
client grants, scope-intersection, deletion, wrong-kind and revocation checks
are unchanged. The complete module then passes 10/10 with the same offline
baseline runner. The long full suite was not repeated after this fixture-only
repair; its initial non-clean result remains recorded above.

`test_approval_bridged_proposal_is_not_warned_on_the_chat_rail` is inherited
THEIRS: O24's authorization-before-approval-bridge check reaches native
`work_order.add`, which its fixture does not grant. All three relevant files
are byte-identical to THEIRS; this existing failure is retained. Remaining
shared OURS failures were compared by exception chains and normalized source
frame sequences as well as node names, preserving runner/fixture failures.
The existing categories include timezone/naive-window fixtures, attachment
transaction cascades, omitted media fixtures/dependencies and stale permission/
schema expectations. No new skip, weaker assertion or product-security bypass
was introduced to make the baseline appear clean.

The case ledger accounts for all 112 initial merged failure entries: nine
merge-caused card cases now pass; 102 retain the completed OURS exception
chains/source stacks; one additional notification failure is observed in
THEIRS with the same relevant source. There are **zero unresolved merge-only
cases** after the focused repairs, while 103 inherited baseline entries remain.
`backend-comparison.json` preserves the raw initial comparison and
`backend-final-classification.json` records the resolved/inherited disposition.
The extra incomplete THEIRS run is not needed to infer missing pass results:
the complete merged run, complete OURS run, targeted repair replays and that
specific observed THEIRS failure establish each disposition individually.

The sprint backlog parent statuses are preserved. `HANDOFF.md`, S1 execution
ledger and D13/D16/D19 contract evidence record this integration separately
from prior rendered/deployment acceptance. The coordinator must review, stage
and commit the worktree resolutions; the index intentionally remains unmerged.


## Host browser fixes

21 October 2026 — bounded follow-up on the uncommitted IOT-Merge tree. The
partial frontend merge edits were inspected before changes. No Git write
commands, Azure access or deployment actions are authorized in this pass.
Evidence root: `data/devscratch/iot-host-fixes/`.

The missing focus GET fixture and machine preflight refusal/admission repairs
are implemented; final browser replays are in progress. The shared GET/PUT
projection is `{ focus, focus_version, focus_unavailable }`, including the
unfocused `null / 0 / false` state. The R6 protected-request guard remains
strict. Test scheduling now delays only the original canonical scope hydration
read so the merged preflight can independently resolve canonical machine IDs.
The drawer layout refusal assertion follows the single inline Alert; the
blocked-send, draft and hint expectations remain unchanged.

The isolated browser is Linux Chromium in the task-owned
`r6-iotfix-browser-3008` container, WebSocket `3008`; fixture-only Vite runs on
`15241` using `vite.s0.config.ts` (dotenv and backend proxy disabled). Rendered
screenshots have been inspected. This is fixture evidence, not macOS-native
Chromium, real login, provider/audio/storage, deployment or product acceptance.
Final per-suite counts and cleanup evidence will be appended after execution.

The full S1 replay also exposed an inherited R6 label assertion: both the
Machines source and S1 test initially matched MERGE_HEAD bytewise, but
`common.tsx` already labels offline health as “No current data” while S1
expected “Offline”. S8 already expects the newer wording. S1 now checks the
existing visible text plus `data-condition="offline"`; canonical offline,
coverage and unknown/unavailable distinctions remain asserted. No product
wording was changed. This is an assertion alignment for unchanged behavior.

RV08/RV09's native thread-change locators also used internal test IDs as
accessible names. R6's existing controls expose “Select conversation” and
“New conversation”; the old tokens remain `data-testid` values. Updated only
these role/name locators, retaining exact matching and native menu actions.
Thread creation, persisted selection, consumed hints, request counts, exact
message bytes and acknowledgement/version assertions are unchanged. No
production control or visible text changed. Initial stale-locator timeout
artifacts are retained separately from final replays.
