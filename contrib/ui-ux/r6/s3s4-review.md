# S3 / S4 delegated review — merged stream tickets (P09 P11 P12 P18 P20)

**19 October 2026. Status: agent review under owner delegation (O12); fixes pending.** Not an independent human audit, accessibility certification or R6 acceptance; it waives no prerequisite.

## Method

A read-only review workflow at the merged tip `bb732dd1d`: one independent reviewer per ticket read its commits, report and the R6 pack (backlog row, blueprint, screens, acceptance groups) against the project rules; a second agent then tried to refute every finding and kept only those it could trace in the code, adjusting severity. Captures were inspected by each stream when it built the ticket; this pass reviewed code and claims. Full findings with scenarios and suggested fixes: session scratchpad `review-merged.json`; each is sent to Codex with its ticket's next run, and its disposition goes into the ticket report.

## Confirmed findings

| Ticket | High | Medium | Low | Refuted |
|---|---|---|---|---|
| P09 | 0 | 1 | 4 | 1 |
| P11 | 1 | 2 | 6 | 0 |
| P12 | 0 | 3 | 7 | 1 |
| P18 | 2 | 2 | 4 | 1 |
| P20 | 1 | 1 | 2 | 2 |

High and medium findings:

- **P09 (medium)** A source outage or a row missing from the batch still reads 'No trend', the same label as a source with no connector (`src/frontend/src/pages/assets/health/SignalTrend.tsx`).
- **P11 (high)** A 'highest of' KPI tile no longer shows a warning or critical state from a sensor in another unit; it can read Normal over a critical channel (`src/frontend/src/pages/assets/performance/kpis.ts`).
- **P11 (medium)** Bay cards say 'No history in this window' when the request failed, is still loading, or the bay's key is not mapped (`src/frontend/src/pages/assets/performance/StationPerformance.tsx`).
- **P11 (medium)** A failed or denied bay-list read is silent: the station renders as if it had no bays (`src/frontend/src/pages/assets/performance/StationPerformance.tsx`).
- **P12 (medium)** Retained snapshot window and sample times are moved by a per-request display shift, so evidence is not 'returned as captured' (`src/backend/InvenTree/assets/machine_history.py`).
- **P12 (medium)** Withheld (out-of-scope) owning work is shown as 'No owning work recorded' (`src/frontend/src/pages/assets/faults/FaultOccurrences.tsx`).
- **P12 (medium)** Any work-summary failure leaves the default Open work view as a loading skeleton that never resolves, a regression for client-less legacy equipment (`src/frontend/src/pages/assets/maintenance/MachineMaintenancePanel.tsx`).
- **P18 (high)** Human-entered closeout proposals (extractor='manual') are listed as AIMMS-drafted, handled actions (`src/backend/tasks/work_execution/handled.py`).
- **P18 (high)** Pending closeout effects are shown as 'AIMMS is handling · Working' although no deployment can execute them (`src/backend/tasks/work_execution/handled.py`).
- **P18 (medium)** 'N part line(s) ... saved' counts generator suggestions, including unresolved parts that were never saved (`src/frontend/src/pages/tasks/workorders/execution/HandledByAimms.tsx`).
- **P18 (medium)** Proposals for superseded revisions or abandoned captures read 'waiting for review' (`src/frontend/src/pages/tasks/workorders/execution/HandledByAimms.tsx`).
- **P20 (high)** Analysis basis reads schema defaults through coerce_diagnosis, so missing provenance shows as 0 snapshots, 0 stale signals, 'No data available' and 'A threshold configured in AIMMS' (`src/backend/tasks/work_execution/investigation.py`).
- **P20 (medium)** UI tells technicians to correct, resolve and agree findings/checks in the assistant, which has no write path for any of them (`src/frontend/src/pages/tasks/workorders/execution/ContinueInvestigation.tsx`).

## Outcome

All 36 confirmed findings were sent to Codex with each ticket's next run and fixed, each with its disposition in the ticket report (P09: 5, P11: 9, P12: 10, P18: 8, P20: 4); fixes merged in `4ff346734`..`4db5f2992`. Re-checked by the coordinator on the host browser: Health S3 108/108 + S0 95/95; Performance S3 146/146 + S0 95/95; S4 52/52, S1 work 44/44, S0 work 18/18; main S6 36/36 and S3 machine-work 46/46 before the closeout-disclosure follow-up, whose S0 95/95, S1 178/178 and S6 44/44 are Codex's runs in Docker Chromium; the coordinator's full host replay on the merged tip `4db5f2992` is recorded separately. P09, P11, P18 and P20 are **Ready for review**; P12 is **Blocked** on its named missing providers.
