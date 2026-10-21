# Delegated review — merged wave 1 (P10 P37 P38 P39 P40 P41 P42 P21 P23 P27 P24 P25)

**20 October 2026. Status: agent review under owner delegation (O12); fixes in progress.** Not an independent human audit or R6 acceptance.

Method as in `s3s4-review.md`: a read-only workflow at `4db5f2992`, one reviewer per area (Health, Performance, Investigation, Work, Closeout) against the R6 pack and project rules, then a skeptic per area that kept only findings it could trace in the code. Full findings: session scratchpad `review-wave1.json`; each goes to Codex with that area's next run, its disposition into the ticket report.

| Area | Tickets | High | Medium | Low | Refuted |
|---|---|---|---|---|---|
| health | P10 P37 | 1 | 1 | 7 | 0 |
| performance | P38 P39 P40 | 1 | 3 | 5 | 0 |
| investigation | P41 P42 | 0 | 3 | 9 | 0 |
| work | P21 P23 P27 | 0 | 3 | 3 | 0 |
| closeout | P24 P25 | 0 | 2 | 4 | 0 |

High and medium findings:

- **P37 (high)** Summary shows a green Normal when no signal was evaluated, while every row says Not assessed (`src/backend/InvenTree/machine_health/services/summary.py`).
- **P10 (medium)** Freshness says 'older than the budget' for a signal that was never observed or is dated in the future (`src/frontend/src/pages/assets/health/SignalEvidence.tsx`).
- **P40 (high)** Fault History investigation links send recorded plant-clock bounds to a series read that expects display-clock bounds (`src/frontend/src/pages/assets/investigation/OccurrenceInvestigation.tsx`).
- **P40 (medium)** Internal provenance links use raw <Anchor href> and skip the router basename (`src/frontend/src/pages/assets/investigation/LinkedEvidence.tsx`).
- **P38 (medium)** Batch coherence check requires exact equality of per-request server values, so multi-batch history fails entirely (`src/frontend/src/pages/assets/performance/historyBatches.ts`).
- **P40 (medium)** A partial batch failure shows failed channels as 'No readings in this window' and leaves them out of coverage counts (`src/frontend/src/pages/assets/performance/historyBatches.ts`).
- **P41 (medium)** Approving a photo/report whose description is over 250 characters always returns a 500 on PostgreSQL (`src/backend/InvenTree/aichat/services/investigation_proposals.py`).
- **P41 (medium)** Only one artifact per chat turn can ever be published; sibling proposals and the next request-focused send are refused after the first approval (`src/backend/InvenTree/aichat/services/investigation_proposals.py`).
- **P41/P42 (medium)** New PROTECT FKs break native attachment deletion with a 500 and make Work deletion refuse after other attachment files are already gone (`src/backend/tasks/investigation_models.py`).
- **P23 (medium)** Marking a recorded step not applicable shows it as Failed and credits the old reading to whoever marked it (`src/frontend/src/pages/tasks/workorders/execution/verificationProjection.ts`).
- **P21 (medium)** `drift_status` is never written, so the panel always says the applied package is current (`src/backend/tasks/work_execution/package.py`).
- **P23 (medium)** The note saved with each attempt is dropped, and the UI says it was never kept (`src/backend/tasks/work_execution/verification.py`).
- **P25 (medium)** A definitive refusal during reconciliation leaves the update 'uncertain' with no way out, even after the Work is completed (`src/frontend/src/pages/tasks/workorders/useWorkRecordUpdate.ts`).
- **P25 (medium)** 'Use reviewed account for a new update' erases a manual account draft (`src/frontend/src/pages/tasks/workorders/CloseoutPanel.tsx`).

## Outcome

All 42 confirmed findings were fixed by Codex with dispositions in the ticket reports (Health 9, Performance 9, Investigation 12, Work 6, Closeout 6) and re-checked by the coordinator on the host browser in each worktree (Health S3 208/208, S0 95/95; Performance S3 208/208, S0 95/95 incl. the 500-channel case that timed out only in Docker; Investigation S4 111/112 then 16/16 x3 after a test-race fix, S1 177/178; Work S4 122/122, S0 18/18, S1 178/180 - the remaining failures are the known intermittent assistant-context width check). Merged at `d693591fe`; full host replay: backend 937 run - 934 passed, 2 SQLite skips, 1 error in repair.test_generation.InProcessEvidenceIntegrationTest ('Cannot operate on a closed database'; identical on the pre-R6 base fe0d73a4a, so an offline-SQLite runner limitation, not a regression); units 620/620; browser S0 95/95, S1 180/180, S2 75 + 3 skipped by design, S3 228/228, S4 158/158, S6 80/80, S7 64/64, native drawer 17/17 (evidence data/devscratch/r6-merge/2026-10-20b/).
