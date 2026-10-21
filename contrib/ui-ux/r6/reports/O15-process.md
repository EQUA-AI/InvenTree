# O15 — process decisions C1–C4

## Review findings and dispositions

### Coordinator follow-up 2 — seven findings

Status: **built and tested for the seven supplied follow-up findings**. This section supersedes the prior amendment-during-rework open limitation and adjacent-only rebinding description. Earlier checkpoints below remain historical. The coordinator's explicit O17 instruction is preserved: without an acceptance process, closure acceptance is unknown and recurrence creates linked corrective Work, never reopens. These are offline/fixture checks, not deployment, independent human or R6 acceptance. Changes remain uncommitted.

| Finding | Native fix and conservative disposition | Covering regression |
| --- | --- | --- |
| F2.1 (low) | Amendment proposal and decision now share the verification guard: current lifecycle must be completed and the latest completion/reopen event must not be a recurrence reopen. Replayed committed commands remain receipts; new writes to retained history are refused. The replacement regression proves refusal during rework, successful reclosure and retained original history, then permits amendments to the newly completed closure. | `test_followup2_amendment_refused_during_rework_and_reclosure_succeeds`; `test_followup2_amendment_decisions_require_current_completion` |
| F2.2 (low) | Binding identity sets are compared with their last appearance across the entire approved-scope history. Reappearance after absence requires evidence newer than the reappearing scope even if the identity is unchanged. Component/procedure changes remain selective. | `test_followup2_binding_reappearance_through_approval_requires_fresh_evidence` |
| F2.3 (low) | The locked prior-Work lookup uses scoped_work_orders(actor). Foreign, missing and inactive prior records all produce the existing identical generic 400 refusal. The earlier distinguishable service assertion was corrected to that required refusal; HTTP response equality and no-write assertions cover the boundary. | `test_followup2_foreign_missing_and_inactive_recurrence_refuse_identically`; `test_review_recurrence_foreign_parent_and_subset_permissions` |
| F2.4 (medium) | The recurrence command bounds its title to 200 Unicode code points, preserving the full title through the exact occurrence link. Native Work creation validates type and the model title limit before new writes, returning 400 instead of a database 500. Validation follows stored-receipt replay so a later rule cannot discard a committed receipt. | `test_followup2_long_titles_refuse_before_writes_and_bounded_link_succeeds`; `S7 followup2 long recurrence title and linked response, light/dark desktop/phone` |
| F2.5 (low) | Under the native browser lock, review re-reads storage and compares terminal status, command kind, Work, target and idempotency key before and after the fresh authorized read. A stale mounted instance adopts a newer operation, including uncertain intent, instead of clearing it. Capture inspection also corrected the recurrence Alert body and button wrapping; the uncertain reconciliation control now stays within its phone Alert. | `Unit review cannot discard a newer uncertain or terminal operation`; `Unit review requires the same command, Work and target even with the same key`; `S7 followup2 stale panel review preserves the newer uncertain recurrence, light/dark desktop/phone`; `S7 uncertain Alert button/label containment, light/dark desktop/phone` |
| F2.6 (low) | Only a verified escalation receipt supplies confirmedVersion. The local confirmation line requires that exact current request version and an escalation record. Historical or old-format receipts remain inspectable through review without confirming a later routing. | `S4 O15 persistent contradiction routes to one expert owner: first confirmation, later owner/checkpoint/recorder/version, reload and no stale confirmation, light/dark desktop/phone` |
| F2.7 (low) | Unknown acceptance copy now follows row action and server can_create. Linked rows say corrective Work is already recorded; readers without creation authority see the role requirement; only actionable create_linked rows receive the creation instruction. Unknown acceptance still never permits reopening (O17 instruction). | `S7 followup2 long recurrence title and viewer role, light/dark desktop/phone`; `S7 review refused recurrence survives reload and can be reviewed then retried`; `S7 followup2 stale panel review preserves the newer uncertain recurrence` |

The original incorrect dead-end test was replaced as requested: it now proves that a new amendment is refused during reopened execution and the Work can subsequently close. Decision tests cover approval/rejection against retained history and a lifecycle value of completed whose newest event is still a reopen; a genuine later completion permits the current closure's amendments. No amendment/effect/capture record is deleted or silently superseded. Foreign/missing/inactive prior-Work responses are asserted identical at HTTP level with no Work created or reopened. No new schema migration, flag, dependency, native header or assistant change is introduced by this follow-up.

### Actual follow-up checks

Every validation invocation, including failed or non-checking attempts, is recorded with literal argv/cwd/time/exit in `data/devscratch/r6-o15/process/review2/commands.jsonl` and `validation-commands.md`. Full backend labels remain the same 27 unique modules, using only `backend_baseline.py`. The previous owned disposable SQLite is reused through the offline runner at `review/backend-final-fresh/`; it automatically archives prior logs/manifests under `history/`. It is not a dev/customer database. All backend runs remain serial, network disabled. The sandbox denied the attempted `ps aux | grep '[m]anage.py test'` guard; confirmed prior owned-runner exits and the private test path are recorded, not a fabricated passing process check.

| Command group | Actual result |
| --- | --- |
| Final offline backend, 27 modules | 363 cases; exit 0 |
| Types default | 0 diagnostics; exit 0 |
| Types S4 | 0 diagnostics; exit 0 |
| Types S7 | 0 diagnostics; exit 0 |
| Units, 62 files | 627 passed; exit 0 |
| Initial focused expert routing, both themes/widths | 2 passed, 2 failed; exit 1 |
| Investigation spec isolated replay after capture timeouts | 42 passed; exit 0 |
| Initial focused recurrence probe | 0 observed passes / 2 observed failures; interrupted, remaining cases not verified; exit 130 |
| Focused recurrence and retained-operation regressions, corrected exact-row and alert-containment assertions | 14 passed; exit 0 |
| Interrupted S4 before phone layout correction | 1 passed; exit 130 |
| Corrected mobile probe interrupted by credit pause | 3 observed passes / 0 observed failures; interrupted, remaining cases not verified; exit None |
| Initial long S4 attempt, capture timeouts and interruption | 29 passed, 2 failed; exit 130 |
| Initial post-reset isolated investigation, connection failures | 11 passed / 10 failed / 0 skipped; exit 1 |
| Full S4, partitioned by spec/project | 180 passed across serial native spec/project runs; exact complete suite membership verified |
| Full S7, partitioned by spec/project | 86 passed across serial native spec/project runs; exact complete suite membership verified |
| Host hooks, all 51 current changed files | 9 passed / 9 skipped; exit 0 |
| Explicit pinned Python test/migration hooks | 2 passed / 0 skipped; exit 0 |
| git diff --check | see log; exit 0 |

Renders are **Linux Chromium in owned Docker `r6-process-browser-15216`**, harness **15206**, WebSocket **15216**. Captures: `data/devscratch/r6-o15/process/review2/browser/`; saved PNGs including diagnostic attempts: **1008**; inspected selected originals: **24**, indexed with hashes/dimensions and notes in `review2/capture-index.md`. Desktop 1280×720 and phone 390×844, light/dark where color matters. Full suites use serial native spec/project commands with fresh browser connections; `browser-suite-results.md` verifies exact full-suite membership, not sampled coverage. The initial long S4 attempt retained 29 passes, two capture timeouts, one interrupted case and 148 not run. The affected investigation spec is replayed alone for both projects; no assertions or time limits change. The locally bundled native fixture uses development English fallbacks; this does not establish production catalog or deployed behavior. The owned container's start/stop commands and results are recorded in the invocation inventory. An interrupted probe previously left the owned static preview PID 91228; sandbox SIGTERM was refused and that attempt is retained. On resumption the coordinator explicitly reported removing all leftover containers and servers; a local listen-port check found neither 15206 nor 15216 listening. Each final browser command owns a fresh preview (`reuseExistingServer: false`), and the owned Docker renderer was restarted and stopped for this resumed run. The final cleanup check found zero listeners on15206/15216. Real phone capture inspection found an internally clipped recurrence alert/button: native Alert body shrink and wrapping button styles correct it, with a bounding-box/label containment regression. The earlier S4 run was interrupted before this UI change and is retained as a diagnostic, not a full-suite pass. No other existing container, Azure, shared ledger or git write was used.

### Files changed

- `src/backend/tasks/services/closeout_amend.py`
- `src/backend/tasks/services/recurrence.py`
- `src/backend/tasks/services/scheduling.py`
- `src/backend/tasks/tests/test_process_decisions.py`
- `src/backend/tasks/work_execution/scope_revision.py`
- `src/frontend/src/pages/tasks/workorders/execution/ExpertEscalation.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/RecurrenceActions.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/processCommand.test.ts`
- `src/frontend/src/pages/tasks/workorders/execution/processCommand.ts`
- `src/frontend/src/pages/tasks/workorders/execution/useProcessCommand.ts`
- `src/frontend/tests/s4/pui_work_investigation.spec.ts`
- `src/frontend/tests/s7/pui_monitoring_continuity.spec.ts`

Across this follow-up, **12 source paths changed since the first review snapshot**, plus report updates. The whole uncommitted O15 batch remains 46 source files plus five reports (51 paths); the complete per-ticket map and 12 shared integration paths remain in `data/devscratch/r6-o15/process/files-by-ticket.md`. P44 has no additional functional source change in this follow-up, but shared scheduling/process tests remain in the aggregate map.

### Remaining limits and CI

The earlier amendment dead end is resolved by refusal before any new retained-history amendment can be written. Existing packet/capture/effect or historical dependent provenance still requires its native owning revision flow; it is not discarded. Scope evidence ids still refer only to this Work's immutable InvestigationArtifact namespace; the missing contract is whether bindings may also cite RepairInvestigationFinding records and how that namespace is typed. Until supplied, repair finding ids are not accepted as that namespace. Additional unowned deviations during rework remain explicitly unowned/unavailable for the owning Work process, and archived Work is not implicitly unarchived. PostgreSQL locking/concurrency, real storage/login, production localization, physical/site authority, expert delivery/acceptance and complete immutable accepted-report provider integration remain unverified. No A17/A21/A22/A23 or R6 acceptance is claimed.

CI should retain the 27-module offline set, default/S4/S7 types, units and full S4/S7; add the title-bound-before-write/receipt-replay, amendment/reclosure, binding-gap approval-to-overview, uniform foreign refusal, stale mounted review and stale routing confirmation regressions. Keep explicit Python test/migration hook inclusion. PostgreSQL concurrency/title-capacity and production catalog lanes remain integration work. No CI file was edited.

<!-- O15-REVIEW2-END -->

### Prior review — historical verified dispositions and evidence

Status: **built and tested for the requested review fixes**. This review update supersedes the initial implementation descriptions below. Parent provider/site/R6 acceptance remains unfinished. All changes are uncommitted for the coordinator.

| Finding | Native fix / conservative disposition | Covering regression |
| --- | --- | --- |
| 1 (P44) | Explicit unassigned release skips the active-incoming check, keeps version/event and responsible owner, and creates no handover. | `aichat.tests.test_process_unassigned.GovernedUnassignedRegressionTest.test_confirmed_proposal_dispatch_releases_assignment_without_handover` |
| 2 (P44) | Handovers cascade with Work after their complete field summary is frozen in governed deletion. User links use SET_NULL with recorded display names; deleted historical ids cannot be resurrected as current owners or live foreign keys. | `test_review_assign_then_delete_keeps_handover_snapshot; test_review_accepted_handover_delete_and_deleted_user_attribution; test_review_deleted_owner_is_not_resurrected_by_later_assignment` |
| 3 (P44) | Reassignment withdraws the prior pending proposal; returning to the responsible owner creates no self-handover. | `test_review_reassign_back_has_no_self_acceptance; test_review_handover_return_after_acceptance_and_time_panel` |
| 4 (P44) | Local confirmation requires the current accepted handover id. A later pending handover cannot display an earlier acceptance as its own. | `S4 O15 incoming owner explicitly accepts handover: accepted receipt, reload, then different pending id with no confirmation` |
| 5 (P44) | The time/assignment UI references the canonical continuity handover; the time provider explicitly points there and keeps its existing two-query budget. Copy is conditional and dependency cards say Assignee. | `test_review_handover_return_after_acceptance_and_time_panel; S4 O15 incoming owner explicitly accepts handover checks both time states` |
| 6 (P42) | Routing reason has its own escalation_reason field. Escalation preserves recorded status and blocked_reason for every unresolved outcome. | `test_review_escalation_preserves_cannot_perform; test_review_escalation_preserves_all_recorded_unresolved_outcomes; test_escalation_retains_previous_outcome_and_evidence_boundary` |
| 7 (P42) | Missing configured group returns available:false/group_not_configured, with separate UI; no GET creates it or claims zero members. | `test_review_missing_expert_group_is_unavailable; S4 review missing expert group is configuration unavailable` |
| 8 (P42) | Added normal-user expert, independent view-role/scope, inactive/past/resolved/key-conflict and HTTP GET/POST boundaries. Migration 0035 now follows the historical user-role grant migrations, so the new expert group cannot inherit their blanket grants. | `test_review_expert_validation_and_http_permission_boundaries; test_review_escalation_preserves_all_recorded_unresolved_outcomes; test_review_fresh_default_group_has_no_legacy_role_grants` |
| 9 (P50) | Default verification off means acceptance unknown and create_linked. Only a supported enabled verification policy plus a clean positively awaiting-verification closure permits reopen. All provenance blockers offer linked Work. The existing policy is registered on the native Django flag bridge, default off; optional compatibility and supervisor are supported. | `test_review_default_verification_off_routes_linked; test_review_recurrence_http_receipt_and_unknown_acceptance; test_review_all_retained_provenance_blockers_offer_linked_work; test_review_verification_setting_is_registered_default_off` |
| 10 (P50) | Refused/confirmed operations have Review latest recurrence; only a successful latest read clears them. Server can_reopen and the command require Work change and execution permission. | `S7 review refused recurrence survives reload and can be reviewed then retried; test_review_recurrence_foreign_parent_and_subset_permissions` |
| 11 (P50) | Reopen clears live actual_completed_at, retaining closure time in the event and immutable closeout. Recurrence uses retained closure time; duration readers no longer count the reopened Work as completed. | `test_review_reopen_clears_completion_and_distinguishes_unowned_and_canceled; test_reclosure_archives_unaccepted_account_and_blocks_acceptance_while_reopened` |
| 12 (P50) | Already reopened requires ownership of that occurrence and an actual reopen event. Unowned deviations during rework are distinct and unavailable; canceled Work is unavailable. | `test_review_reopen_clears_completion_and_distinguishes_unowned_and_canceled` |
| 13 (P50) | A registered component with a dictionary point bound to a native signal is required; unknown/unbound references are unavailable, not empty. | `test_review_unbound_and_archived_recurrence_are_unavailable` |
| 14 (P50) | Inactive archived prior Work is excluded by command and projection; no implicit unarchive or hidden open Work. | `test_review_unbound_and_archived_recurrence_are_unavailable` |
| 15 (P50) | Corrected the misleading acceptance-only test name and added actual foreign-parent recurrence, permission subsets, HTTP receipt, default-policy verification and amendment-after-reopen refusal. PostgreSQL concurrency remains unverified under the mandated SQLite runner. | `test_review_recurrence_foreign_parent_and_subset_permissions; test_review_recurrence_http_receipt_and_unknown_acceptance; test_review_amendment_after_reopen_blocks_reclosure_without_losing_history` |
| 16 (P50) | Confirmation derives reopened versus linked-created from the exact command receipt and names the resulting Work. No unaccepted closure is called accepted. Source caption is Recorded closeout source. | `S7 O15 same-component recurrence create_linked/reopen, both themes/sizes; S7 review refused recurrence confirms WO-80 and reviews confirmed state` |
| 17 (P43) | Binding-level component/procedure rebinding is a change source. Both old/new changed identities are tracked without invalidating an unchanged sibling binding of a shared dependency. | `test_review_binding_rebinds_and_removed_or_invalid_evidence_never_retain; test_review_shared_dependency_rebind_preserves_unrelated_component_evidence` |
| 18 (P43) | Removing or editing evidence ids cannot reset the change boundary. Retention requires nonempty existing immutable Work artifacts; clearing reassessment also requires evidence recorded after the triggering approval. Missing/unvalidated evidence stays unknown, not executable. | `test_review_binding_rebinds_and_removed_or_invalid_evidence_never_retain; test_later_unrelated_revision_cannot_revive_unreassessed_evidence; test_review_shared_dependency_rebind_preserves_unrelated_component_evidence` |
| 19 (P43) | Unmapped changes have an explicit unknown-applicability warning and changed component/procedure identities. Unrelated valid bound evidence remains retained per C4. | `S4 review unmapped changes and obsolete-only bindings stay explicit; test_procedure_binding_and_unmapped_change_do_not_invalidate_other_bindings` |
| 20 (P43) | Impact availability and notice/tiles count only the effective current scope. Historical-only bindings do not establish current impact; conflicting effective scope rows say effective_unknown, not obsolete. | `test_review_scope_approval_endpoint_validates_and_overview_keeps_current_boundary; S4 review unmapped changes and obsolete-only bindings stay explicit` |
| 21 (P43) | Approval strips refs and resolves component on the packet machine, revision among this Work applications, and evidence among this Work immutable investigation artifacts. Unknown/foreign ids are refused. | `test_review_scope_approval_endpoint_validates_and_overview_keeps_current_boundary` |
| 22 (P43) | Rows include line_sequence/binding_index and React keys include both. Duplicate kind/id on a line is refused; dependencies/evidence lists capped at 50, binding ids/refs at 64 characters. Repeated dependencies across different lines remain representable. | `test_review_scope_approval_endpoint_validates_and_overview_keeps_current_boundary; S4 review unmapped changes and obsolete-only bindings stay explicit checks repeated keys and console` |
| 23 (P43) | Added native approval-POST to overview-GET coverage, foreign/nonexistent links, current plain scope after bound scope, conflicts and browser unknown-impact states. | `test_review_scope_approval_endpoint_validates_and_overview_keeps_current_boundary; test_review_binding_rebinds_and_removed_or_invalid_evidence_never_retain; S4 review unmapped changes and obsolete-only bindings stay explicit` |

The latest review evidence is `data/devscratch/r6-o15/process/review/`; the earlier O15 evidence below is historical. Every review validation invocation is recorded with argv/cwd/time/exit in `commands.jsonl` and the readable `validation-commands.md`. Failed attempts remain retained.

| Review check | Actual result |
| --- | --- |
| Offline backend, original 23 modules plus time/assignment, both AI proposal modules and native flag-bridge tests | 359 cases; exit 0 |
| Types default / S4 / S7 | 0 / 0 / 0 exits; zero diagnostics on passing runs |
| Units | 625 passed; exit 0; 62 test files |
| Targeted S4 | 20/22 first attempt; two original uncertain-handover fixture mismatches retained; isolated corrected replay 2 passed; exit 0 |
| Final coherent handover capture replay | 4 passed; exit 0 |
| Targeted S7 | 10 passed; exit 0 |
| Full S4 | 180 passed; exit 0 |
| Full S7 | 74 passed; exit 0 |
| Host hooks, all 51 changed paths | 9 hooks passed, 9 skipped; exit 0 |
| Explicit pinned Python test/migration hooks | 2 hooks passed, 0 skipped; exit 0 |
| git diff --check | 0 whitespace errors; exit 0 |

All renders are **Linux Chromium in the owned Docker container `r6-process-browser-15216`**, harness **15206**, WebSocket **15216**. Captures are under `review/browser/`; `review/capture-index.md` lists the **34 original captures actually inspected**, their dimensions and hashes. Desktop and 390 px phone, both themes, cover accepted and later-pending handovers, saved and unavailable expert routing, unmapped/current-unbound scope, linked/reopen policy and refusal recovery. Text wraps within the phone; no stale acceptance, invented group membership or hidden unknown applicability is presented. The frozen locally bundled native fixture uses the same development English fallbacks as the original dev harness; no production-localization or deployment acceptance is claimed. The owned renderer stop is recorded as `browser-stop` in the command inventory.

The initial four-case red backend run reproduced the original defects (three failures, one governed-delete error). Intermediate backend failures and their corrections are retained in the command inventory: fixture collisions/imports, the exact two-query time contract, the expanded native AI command catalogue, and the real migration-order permission leak. Fresh migration replay supplied 29 labels / 27 unique modules (358 cases, one fixture error); its default-group permission regression passed, and the foreign-scope fixture was corrected to the native iterable resolver contract without changing denial assertions. The final 27-label replay additionally covers deleted-owner responsibility, full native actor labels and recurrence deletion-link snapshots. The test database was fresh and network disabled. All later backend runs remain serial on the owned disposable SQLite, using only the offline runner. `ps aux | grep '[m]anage.py test'` was denied by the sandbox; prior owned runner exits are the serial-execution proof. No Azure, existing dev database/container, dependency download, shared ledger or git write.

Final backend command (checkout root):

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/r6-o15/process/review/backend-final-fresh tasks.tests.test_process_decisions tasks.tests.test_work_execution_continuity tasks.tests.test_work_execution_scope_revision tasks.tests.test_investigation_requests tasks.tests.test_workorder_services tasks.tests.test_workorder_api tasks.tests.test_scheduling_service tasks.tests.test_scheduling_api tasks.tests.test_closeout_completion_integration tasks.tests.test_closeout_amendments tasks.tests.test_closeout_effects tasks.tests.test_workorder_complete_api tasks.tests.test_packet_finalization tasks.tests.test_work_execution_package tasks.tests.test_kanban_overview tasks.tests.test_workorder_rbac tasks.tests.test_client_scope tasks.tests.test_granted_scope_resolver tasks.tests.test_legacy_scope_api tasks.tests.test_closeout_security repair.test_investigation repair.test_duplicate_repairs repair.test_spine tasks.tests.test_work_execution_time_assignment aichat.tests.test_ui_command_parity aichat.tests.test_process_unassigned aichat.tests.test_flag_bridge
```

Files are listed per ticket in `data/devscratch/r6-o15/process/files-by-ticket.md` and each ticket report; shared integration files are listed separately.

Open questions and verification limits: scope `evidence_ids` currently identifies immutable `InvestigationArtifact` rows on this Work; repair-finding ids are a different namespace and need an explicit typed contract before being supported. An additional unowned deviation during rework stays visibly unowned/unavailable pending its owning Work response; it is never labelled handled. Archived Work is not implicitly unarchived. If dependent amendment/capture/effect provenance is added after a permitted reopen, reclosure conservatively refuses and preserves the old closure and its records: the owning closure-revision contract for that later provenance is missing. The amendment-during-reopen regression covers that refusal. PostgreSQL locking/concurrency, production localization, site/physical authority, expert delivery/acceptance, full evidence-applicability and complete immutable accepted-report provider integration remain unverified/unavailable. These checks are fixture/offline verification, not deployment, independent human or R6 acceptance.

CI should retain all 27 backend modules, default/S4/S7 types, all units, full S4/S7, pinned host hooks with explicit test/migration inclusion, and the new permission/HTTP/retention/recovery/rebinding regressions. Add PostgreSQL concurrency, native flag-registry island and production catalog-generation lanes at integration. Local migration 0035 must be reviewed with parallel migration heads before any deployment; it was never applied to Azure. No CI file was changed.

<!-- O15-REVIEW-END -->

## Initial O15 batch — historical evidence

Status: **partial — C1/C2/C4 built and tested; C3 linked creation and plain reopening built and tested, dependent-provenance reopening Blocked**. These are isolated fixture/offline checks, not deployment, site-process, independent human or R6 acceptance. Everything remains uncommitted for coordinator review.

## Built and mapped

| Decision / ticket | Implementation and native boundary |
| --- | --- |
| C1 / P44 | Typed assignment creates an attributed, versioned handover proposal. `POST /api/tasks/work-orders/<id>/accept_handover/` checks the active incoming actor, native execution permission, current scope/version and stable idempotency intent before accepting. The previous owner remains responsible until acceptance. Acceptance records actor/time/version and the original durable receipt. A read receipt or opening Work/chat never accepts. Reassigning the same already accepted owner adds no new acceptance chore. Replacing an unaccepted intermediary retains the actual outgoing responsible owner and the real previous assignment in the audit. Hold, requests, sibling Work and physical authority keep their separate states. |
| C2 / P42 | Existing investigation commands accept explicit `escalate` intent with exactly one active, currently Work-authorized member of the configured group, a future checkpoint and a reason. The request remains disputed/unresolved. Saved routing shows group, owner, checkpoint, state, recorder and time. Before/after request facts and the retained artifact boundary are saved in the audit, so the new reason cannot erase the earlier unresolved outcome. `AIMMS_EXPERT_GROUP_NAME` defaults to `Reliability engineers`; only migration 0035 creates it if absent, without granting roles or membership. Routing is separate from delivery/expert acceptance. |
| C3 / P50 | Read-only `recurrence_policy.v1` matches the exact authorized machine/component slot through the deviation's bindings and original first-observed time within `AIMMS_RECURRENCE_DAYS` (default 30) after closure. Monitoring consumes this projection; only the existing explicit Work-creation command writes. Native supervisor `WorkOrderCloseout.verified_by` / `verified_at` are the available closure-acceptance source in this checkout; an incomplete acceptance pair is treated conservatively as accepted. Accepted closure creates new linked corrective Work with the occurrence identity and atomically assigns its native anomaly Work owner; the accepted account/hash remain untouched. An occurrence already owned by another native Work or repair packet is excluded and refused, preventing duplicate corrective ownership. A plain unaccepted standalone closure can reopen and retain its prior fields in an attributed event before reclosure. Packet ownership or dependent capture/effect/amendment provenance blocks reopening until its owning retained-revision flow exists. GET/page load never creates or reopens Work. |
| C4 / P43 | Existing approved scope JSON retains optional explicit component/procedure and typed dependency/evidence bindings. `scope_revision.v1` reassesses only matching bound tasks, materials, resources, criteria/tests and permissions; unrelated bound evidence is retained, unbound applicability stays unknown and obsolete scope is marked not executable in the projection. A later unrelated revision cannot revive an older dependency while its same evidence is still pending reassessment. This read projection does not grant server execution or physical authority. |

## Files and integration

The exact per-ticket map is `data/devscratch/r6-o15/process/files-by-ticket.md` and `.json`: **40 changed source files**, including **10 shared integration files**, plus these five reports. Each ticket's list is reproduced in its O15 report update. No shared roadmap ledger, workflow, dependency or lockfile was edited.

`tasks.0035_process_decisions` depends on reserved `tasks.0032` and the existing asset/anomaly and auth schema. It was applied only by the offline runner to this run's disposable SQLite. No repair schema migration is needed: scope bindings remain in the existing immutable scope JSON. Reconcile parallel migration heads at coordinator integration, and align C3 with any later E2 accepted-report snapshot authority before deployment. No Azure read/write, migration, dry run or application start occurred; no existing dev backend/container/database was used.

The local master design prompt is absent in this checkout. Its existing owner copy at `/Users/Aniketkumar/IdeaProjects/InvenTree/contrib/ui-ux/r6/masterdesignprompt.md` was read without changing that checkout. Both design prompts were applied to the incoming-owner brief, expert-routing request, selective scope review and recurrence disclosure using the native React/Mantine/Work architecture. No Figma or new visual dependency.

## Executed checks

The checks below are shared executions across the four tickets, counted once. Literal expanded argv, cwd, timing, exits and logs are in `data/devscratch/r6-o15/process/commands.jsonl`; the readable inventory and earlier manual-command logs are in `validation-commands.md`.

| Check | Actual result |
| --- | --- |
| Default, S4 and S7 TypeScript | 0 diagnostics each on final app source; S7 repeated after the final native occurrence fixture, also 0. |
| Full discovered units | 625/625 in 62 files. |
| Final offline backend, 23 modules | 322/322, zero skips; includes all 22 new process tests and the requested continuity/scope/investigation and permission suites. |
| Targeted incoming acceptance | 6/6, desktop/390 px. |
| Targeted expert escalation | 4/4 before the saved-form layout refinement; final refinement is covered by the full S4 run. |
| Targeted selective scope mapping | 4/4, light/dark desktop/390 px. |
| Targeted recurrence | 8/8; the final editor-role, unowned same-component/time-window fixture is covered by full S7. |
| Full S4 | 172 passed; exit 0 |
| Full S7 | 72 passed; exit 0 |
| Host hooks | 45 changed source/report paths pass the repository hooks; both new Python test/migration pass the forced-inclusion pinned Ruff hooks; git diff --check exits 0. |

Final backend command, run at checkout root:

```sh
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/r6-o15/process/backend-initial tasks.tests.test_process_decisions tasks.tests.test_work_execution_continuity tasks.tests.test_work_execution_scope_revision tasks.tests.test_investigation_requests tasks.tests.test_workorder_services tasks.tests.test_workorder_api tasks.tests.test_scheduling_service tasks.tests.test_scheduling_api tasks.tests.test_closeout_completion_integration tasks.tests.test_closeout_amendments tasks.tests.test_closeout_effects tasks.tests.test_workorder_complete_api tasks.tests.test_packet_finalization tasks.tests.test_work_execution_package tasks.tests.test_kanban_overview tasks.tests.test_workorder_rbac tasks.tests.test_client_scope tasks.tests.test_granted_scope_resolver tasks.tests.test_legacy_scope_api tasks.tests.test_closeout_security repair.test_investigation repair.test_duplicate_repairs repair.test_spine
```

Frontend commands use cwd `src/frontend`:

```sh
node node_modules/typescript/bin/tsc --noEmit
node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s4.json
node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s7.json
node node_modules/vitest/vitest.mjs run
```

Final full browser commands (sequential, same frozen native fixture bundle):

```sh
env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15206 AIMMS_BROWSER_RUN=full-s4 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-o15/process/browser.refined.config.mts --reporter=list tests/s4/
env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=15206 AIMMS_BASE_SPRINT=s7 AIMMS_BROWSER_RUN=full-s7 node node_modules/@playwright/test/cli.js test --config=../../data/devscratch/r6-o15/process/browser.refined.config.mts --reporter=list tests/s7/
```

The sandbox denied `ps aux | grep '[m]anage.py test'`. Each prior owned runner was confirmed exited before the next started, and all runs used this run's private offline SQLite with network disabled. The runner's `--keepdb` reuse is confined to that finished disposable test database. Its built-in `history/` preserves every earlier backend log/manifest.

## Retained failures and actual limits

- Initial backend: 65 cases, 61 pass / one inherited 7.5-second query-time assertion under load / three new test-helper errors. Fixed the schema-editor helper and used the native User-to-Group membership direction; inactive acceptance now rejects before scope resolution. Two broader 296-case runs each had 291 pass / five fixture errors until the test used the real registered station and pump helpers. Corrected process-only run: 19/19. Then all 296/296 passed; the final escalation-retention regression adds one case and the retention run passes 297/297. The final native-ownership guard adds two process cases and two repair modules: 322/322 pass, including 22 new process tests. No permission/assertion threshold was removed or relaxed. Two later S7 type runs caught widened string unions and nullable observation time in the new fixture; explicit native union literals and its actual non-null first observation fixed them, followed by a clean S7 type run.
- Dev harness: first O15 run 1/14 pass with 13 navigation/action timeouts; the required continuity spec alone then had 2/6 pass and four navigation/reload timeouts. Traces retain the roughly 1,000 dev-module requests per page. A bundled fixture removes that transport cost while retaining the native components and strict mocked-backend guard.
- Initial bundled smoke: six failures from unsuitable production Lingui message stripping (and the deliberately cleared invalid harness). The final bundle uses the native fixture's development English fallbacks. This is not a successful production-localization artifact or deployment claim; standard catalog extraction/compilation remains a release integration step.
- One saved-form refinement build failed on JSX syntax, caught by hooks/build and corrected. Initial monitoring run 4/8 pass: linked-work assertions looked for a collapsed original value as standalone text. The corrected test opens the native original snapshot and asserts its labelled result separately from the effective amended result; 8/8 then pass. The final normal editor capture context avoids the unrelated superuser banner. All diagnostic logs/traces remain.

## Captures and review

All renders are **Linux Chromium in Docker `r6-process-browser-15216`**, browser port **15216**, harness **15206**, desktop **1280×720** and phone **390×844**. Evidence is under `data/devscratch/r6-o15/process/browser/`. Early clipped section screenshots exposed the native sticky-header artifact; explicit full-page plus offset viewport captures fixed it. Scope dependencies use a compact desktop grid and stack on phone. Saved expert routing collapses its disabled form and uses the readable `Review latest routing` action. Capture indexes record inspected originals, dimensions and SHA-256; no screenshot pixels are edited. Full suite/capture review completion and owned-container shutdown are recorded in the final verification addendum.

## Open facts and CI

P42 still lacks actual delivery/receipt/expert acceptance and original-media immutability proof; group routing does not certify technical qualification. P43's complete diagnosis-to-scope/evidence-validity and native execution/physical authority graph remains incomplete; only the requested mapping projection is built. P44's physical/service/duty/cross-crew authority providers remain unavailable. P50 still lacks sourced post-repair observation requirements and a complete immutable accepted-report provider; the inherited closeout-source label is not the policy acceptance authority (the command uses native supervisor verification), which must be aligned with E2; captured/effect/amended/packet unaccepted reopening remains explicitly blocked on the owning retained-revision command. These gaps are not filled with healthy/zero/done states.

CI should run the three type projects, all units, S4/S7 browser lanes with strict unmocked-request guards, the 23-module offline backend command, migration-state/configured-group/normal-user permission checks and the pinned host hooks including forced-included Python tests/migration. Retain exact-retry, actor revocation, stale version, no-read-write, same-component/time-window, accepted immutability, provenance refusal and multi-revision evidence-retention tests. Add normal Lingui catalog generation for production and future PostgreSQL concurrency/provider integration checks. No CI file was changed.

## Final verification addendum

Full S4: 172 passed; exit 0. Full S7: 72 passed; exit 0. The final offline backend passes 322/322 in 23 modules (22 new process tests), with zero skipped tests. Default/S4/S7 types have zero diagnostics and all 625 units pass in 62 files. P42/C2, P43/C4 and P44/C1 are built and tested within the requested process boundaries. P50/C3 remains partial on the explicitly blocked dependent-provenance reopening paths.

20 original screenshots were visually inspected across the four decisions, desktop/390 px, light/dark; `capture-index.md` and `.json` list every inspected original, dimensions, SHA-256 and findings. All other captures/traces remain evidence without a blanket inspection claim. The same final app bundle was used for both full suites; the final S7 fixture uses an actual unowned synthetic occurrence, matching component binding and first-observed time, and the native post-command owner.

Owned renderer shutdown: exit 0; only `r6-process-browser-15216` was stopped. Final command inventory, source hashes and repository-diff checks are under `data/devscratch/r6-o15/process/`. Early host-hook file argument selections were not serialized; their result logs are retained, and the recorded final 45-path hook command covers the complete changed set. That historical argument limitation is not claimed as exact command evidence.
