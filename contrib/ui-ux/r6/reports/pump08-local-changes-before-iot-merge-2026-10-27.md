# Pump08 recording: local changes and IOT-Merge comparison

Date: 27 October 2026. Inventory captured before adding this report.

## Result

The remote fetch succeeded. Current checkout `inventTree-aniket` remains at
`721c72f08869d52f52fdd3f126d64fbe9e4e72c2`. Remote `origin/IOT-Merge` is now
`7e33232c7d09a79d27fa76e8dcecd84cb2d09e42`, eighteen commits ahead with no
committed divergence. The remote changes are fetched, not merged or deployed.
The existing local IOT-Merge worktree is clean at the older baseline.

There are 63 tracked modified files and 30 untracked files in this checkout.
Tracked changes total 4,457 additions and 319 deletions; untracked contents
are additional. These are the complete local edits, not an assertion that
every edit belongs to recording or was authored by this session.

## Local changes to preserve

| Area | Existing changes and purpose |
| --- | --- |
| Alarm and event-owned work | Feature-gated automatic work/repair intake from synthetic test alarms; duplicate protection; assignment metadata and supervisor escalation when trade requirements are unknown. |
| Governed repair lifecycle | Native packet work transitions and receipts, intake bindings, work lifecycle controls and tests. |
| Pump08 verification | Separate W5=94 and W6=96 exercise readings, server-derived maximum96, three-check evidence and closeout validation. These are prepared software-exercise values. |
| Mimic and performance | Forebay water illustration and recorded-source labels; test alarm controls and history rows; critical/recovery chart overlays; related types and browser/unit tests. |
| Local AI/provider work | Development-only local provider/model configuration, local controlled PDF retrieval, diagnostic target/citation work, observed operating-state routing and closeout/history readers. Runtime acceptance was incomplete; preserve evidence and review compatibility with the incoming chat changes. |
| Assistant context | Machine hints and focus-picker behavior plus regression tests; these overlap the newer upstream chat workspace. |
| Tracking | Handoff, backlog/contracts and eight operational reports. |
| External recorder workspace | Native Cap projects/receipts, browser controller, canonical script/audio, derived narration, editing tools, captions, MP4s and QA evidence live under ~/EQUA-AIMMS-Recorder. A Git pull in this repository does not version or merge them. |
| Local runtime/data | Separate exercise source worktree, database/media, accounts, mail and runtime configuration are operational artifacts. A source merge alone does not update or validate the recording runtime. |

The separate sandbox source worktree at
`/Users/Aniketkumar/EQUA-AIMMS-V5-Sandbox-20261027/source` is also dirty at the
old baseline. Its tracked changes additionally include `tasks/api.py` and
`tasks/services/staff_assignment.py`; it is not interchangeable with this
checkout. Do not discard it as an assumed duplicate.

## Incoming AI-chat changes

The eighteen commits change237paths. They include the chat workspace and
composer/viewport behavior, read-only OpenUI/chart rendering, scoped analysis
and follow-ups, exact stock/catalog grounding, machine context, voice recovery
and process ownership, tests and qualification evidence. Frontend dependency
and lockfile changes are included. There is one incoming migration:
`voice/0012_voicesession_transport_owner.py`.

The upstream qualification report records passing focused checks and three
backend errors reproduced on its unchanged baseline. Its browser evidence
includes fixtures and does not establish Pump08 script acceptance or live
voice acceptance. No incoming tests or end-to-end rehearsal were run here
during this inventory.

## Merge assessment actually performed

A read-only three-way text comparison used current working files as local,
HEAD blobs as base and fetched remote blobs as incoming. No checkout, index
or application file was changed by this comparison. Eighteen tracked paths
overlap; twelve merge textually without conflicts. Six require resolution:

| File | Conflict regions |
| --- | ---: |
| contrib/ui-ux/r6/HANDOFF.md | 1 |
| src/backend/InvenTree/ai/core/entities.py | 2 |
| src/backend/InvenTree/ai/core/turn_service.py | 2 |
| src/backend/InvenTree/ai/core/workflows/wf8_lookup.py | 1 |
| src/frontend/src/components/aichat/ConversationFocusControl.tsx | 2 |
| src/frontend/src/components/nav/AIChatDrawer.tsx | 3 |

No untracked path collides with an incoming tracked path. Textual merge
success does not prove semantic compatibility. In particular, the incoming
drawer is substantially refactored and the local operating-state/diagnostic
changes must be reconciled with its new context and lookup pipeline.

Recommended integration sequence: preserve a snapshot of both dirty
worktrees and external recorder assets; prepare an isolated candidate from
the fetched commit; port the required recording changes while resolving
the six overlaps; run targeted backend/frontend and migration checks; then
verify the actual admin browser and the original V5 script scene by scene
before making a new final capture. No recording is started by this inventory.

## Recording revision acceptance

Owner requests exact adherence to the supplied V5 script, full scripted
system coverage, admin browsing permissions, shorter pauses and fully visible
mimic screens. Incoming chat fixes now need assessment for restoring the
scripted AI scenes; earlier no-chat deferral is retained as historical scope,
not silently treated as final approval of this new capture.

The earlier9m21 and9m27 review exports remain historical artifacts. Prior
analysis found about4m18 outside the selected voice spans in the9m21 cut and
a critical mimic frame clipped in the source capture. A source merge cannot
repair missing pixels. Preserve the originals and replace deficient captures
with properly framed native footage. Complete human listening has not been
performed; automated decode/caption checks must not be described as listening.

## Complete tracked inventory at inspection

- `contrib/ui-ux/r6/HANDOFF.md`
- `contrib/ui-ux/r6/contracts.csv`
- `contrib/ui-ux/r6/sprint-backlog.csv`
- `src/backend/InvenTree/ai/core/agents/factory.py`
- `src/backend/InvenTree/ai/core/agents/routing.py`
- `src/backend/InvenTree/ai/core/capabilities/closeout_binding.py`
- `src/backend/InvenTree/ai/core/config.py`
- `src/backend/InvenTree/ai/core/entities.py`
- `src/backend/InvenTree/ai/core/integrations/azure_openai_client.py`
- `src/backend/InvenTree/ai/core/integrations/controlled_document_corpus.py`
- `src/backend/InvenTree/ai/core/integrations/model_pins.py`
- `src/backend/InvenTree/ai/core/reasoning/luna_diagnostics.py`
- `src/backend/InvenTree/ai/core/tests/test_entity_manifest.py`
- `src/backend/InvenTree/ai/core/tests/test_lookup_answer_contract.py`
- `src/backend/InvenTree/ai/core/tests/test_luna_diagnostics.py`
- `src/backend/InvenTree/ai/core/tests/test_wf8_voice_readonly.py`
- `src/backend/InvenTree/ai/core/tools/capabilities.py`
- `src/backend/InvenTree/ai/core/tools/inventree/read/machines.py`
- `src/backend/InvenTree/ai/core/tools/inventree/read/maintenance.py`
- `src/backend/InvenTree/ai/core/turn/execution.py`
- `src/backend/InvenTree/ai/core/turn_service.py`
- `src/backend/InvenTree/ai/core/workflows/wf8_lookup.py`
- `src/backend/InvenTree/aimms_flags.py`
- `src/backend/InvenTree/assets/ai_read.py`
- `src/backend/InvenTree/assets/tests/test_ai_read.py`
- `src/backend/InvenTree/machine_health/api.py`
- `src/backend/InvenTree/machine_health/serializers.py`
- `src/backend/InvenTree/machine_health/services/test_alarm_work.py`
- `src/backend/InvenTree/machine_health/services/test_alarms.py`
- `src/backend/InvenTree/machine_health/test_alarm_api.py`
- `src/backend/InvenTree/machine_health/tests/test_test_alarm_work.py`
- `src/backend/InvenTree/repair/api.py`
- `src/backend/InvenTree/repair/intake_binding.py`
- `src/backend/InvenTree/repair/services.py`
- `src/backend/InvenTree/repair/test_spine.py`
- `src/backend/InvenTree/repair/test_start_repair.py`
- `src/backend/tasks/ai_read.py`
- `src/backend/tasks/exercise_verification_api.py`
- `src/backend/tasks/tests/test_pump08_exercise.py`
- `src/backend/tasks/work_execution/exercise_verification.py`
- `src/frontend/lib/types/MachineHealth.tsx`
- `src/frontend/src/components/aichat/ConversationFocusControl.tsx`
- `src/frontend/src/components/nav/AIChatDrawer.tsx`
- `src/frontend/src/locales/en/messages.po`
- `src/frontend/src/pages/assets/TestAlarmControl.tsx`
- `src/frontend/src/pages/assets/health/mimic/StationSchematic.module.css`
- `src/frontend/src/pages/assets/health/mimic/StationSchematic.tsx`
- `src/frontend/src/pages/assets/performance/PerformancePanel.tsx`
- `src/frontend/src/pages/assets/performance/PerformanceTestAlarms.tsx`
- `src/frontend/src/pages/assets/performance/TrendChart.tsx`
- `src/frontend/src/pages/assets/performance/exerciseValues.test.ts`
- `src/frontend/src/pages/assets/performance/exerciseValues.ts`
- `src/frontend/src/pages/assets/performance/testAlarmRows.test.ts`
- `src/frontend/src/pages/assets/performance/testAlarmRows.ts`
- `src/frontend/src/pages/assets/performance/usePerformanceData.ts`
- `src/frontend/src/pages/repair/RepairPacketDetail.tsx`
- `src/frontend/src/pages/tasks/workorders/execution/ExerciseVerificationPanel.tsx`
- `src/frontend/tests/mimic/vite.config.ts`
- `src/frontend/tests/performance/fixtures.ts`
- `src/frontend/tests/performance/pui_performance.spec.ts`
- `src/frontend/tests/s1/pui_assistant_context.spec.ts`
- `src/frontend/tests/s6/pui_automatic_test_work.spec.ts`
- `src/frontend/tests/s6/pui_pump08_intake.spec.ts`

## Complete untracked inventory at inspection

- `contrib/ui-ux/r6/reports/azure-local-refresh-and-pump08-files-2026-10-26.md`
- `contrib/ui-ux/r6/reports/computer-1-recorder-setup-2026-10-26.md`
- `contrib/ui-ux/r6/reports/forebay-fill-restoration-2026-10-27.md`
- `contrib/ui-ux/r6/reports/procurement-demo-video-2026-10-26.md`
- `contrib/ui-ux/r6/reports/pump-demo-video-2026-10-26.md`
- `contrib/ui-ux/r6/reports/pump08-v5-continuation-2026-10-27.md`
- `contrib/ui-ux/r6/reports/pump08-v5-opening-2026-10-27.md`
- `contrib/ui-ux/r6/reports/pump08-v5-runtime-2026-10-27.md`
- `src/backend/InvenTree/ai/core/integrations/local_controlled_documents.py`
- `src/backend/InvenTree/ai/core/reasoning/targets.py`
- `src/backend/InvenTree/ai/core/tests/test_local_provider.py`
- `src/backend/InvenTree/ai/core/tests/test_operating_status_routing.py`
- `src/backend/InvenTree/ai/core/tests/test_reasoning_targets.py`
- `src/backend/InvenTree/assets/tests/test_ai_station_operating.py`
- `src/backend/InvenTree/machine_health/management/__init__.py`
- `src/backend/InvenTree/machine_health/management/commands/__init__.py`
- `src/backend/InvenTree/machine_health/management/commands/pump08_v5_replay.py`
- `src/backend/InvenTree/machine_health/services/v5_pump08_replay.py`
- `src/backend/InvenTree/machine_health/tests/test_test_alarm_history.py`
- `src/backend/InvenTree/machine_health/tests/test_v5_pump08_replay_guard.py`
- `src/backend/InvenTree/repair/test_local_controlled_documents.py`
- `src/backend/InvenTree/repair/test_work_transition.py`
- `src/backend/tasks/tests/test_ai_read_closeout.py`
- `src/frontend/src/pages/repair/components/PacketWorkLifecycle.module.css`
- `src/frontend/src/pages/repair/components/PacketWorkLifecycle.tsx`
- `src/frontend/src/pages/repair/components/packetWorkPolicy.test.ts`
- `src/frontend/src/pages/repair/components/packetWorkPolicy.ts`
- `src/frontend/src/pages/repair/components/packetWorkReceipt.test.ts`
- `src/frontend/src/pages/repair/components/packetWorkReceipt.ts`
- `src/frontend/tests/mimic/pui_forebay_fill.spec.ts`

## Checks for this inventory

Remote fetch, commit comparison, complete status listing and three-way text
comparison completed. Existing worktree whitespace check passed. No source
merge, application change, dependency install, database migration, business
mutation, new recording, external upload, commit or push occurred.
