# Next session: state on 23 October 2026

Repo `/Users/Aniketkumar/IdeaProjects/InvenTree`. `IOT-Merge` and
`inventTree-aniket` are identical at the 23 October UI polish merge, 38 commits
ahead of GitHub and **not pushed**: push both after the owner's go-ahead.
Localhost (http://localhost:5173, backend :8000, local copy of Azure) runs the
same product code from the `viewer` worktree.

## Done on 22-23 October

- UI rounds 2-11 from the owner's feedback and three advisory Hyperagent reviews
  (Fable 55 → 68 → 76, Gemini 64 → 78): station and pump mimics coloured by
  state, pump labels beside the drawing, alarm pump identity, Health freshness,
  Performance KPI cards, truthful Parts fit-basis labels, production wording on
  every machine tab, phone layouts, keyboard reach of missing/failed reasons.
  Details: `contrib/ui-ux/r6/reports/ui-r2.md`.
- Outdated Playwright specs updated to the new wording by three parallel test
  lanes. Last full Mac run before the final fixes: 2,330 passed, 7 failed; the
  final targeted re-runs all pass.
- Data (owner-approved): demo-data cleanup, data tidy and readable signal names
  on Azure and the local copy; provisional bearing alarm limits and a local
  `operator` login in the local copy only. Backups and restore scripts:
  `data/devscratch/{local-cleanup,local-tidy,local-facts,local-limits,local-config,azure-*}`.

## Pending

1. Push `IOT-Merge` and `inventTree-aniket` (owner go-ahead).
2. Done: follow-up 11 fixed the phone Filters scroll reset; on the Mac the
   explorer spec passes 60/60 (twice) and S0 112/112.
3. Owner input: real serials, station locations for Maple Grove, Millbrook and
   Pine Hollow, vibration unit, engineering alarm limits (provisional bearing
   limits on Azure only on request), field-verified parts.
4. R8 procurement: owner decisions in `contrib/ui-ux/r8-procurement/owner-decisions.md`;
   backend wave 1 in worktrees `InvenTree-r6/r8-p1..p3` (branches `r8/p1..p3`).
5. Parked: `r6/backend-fix-a` (pre-existing backend test failures).
6. aimms-dev deployment by the deployment team per `deployment-handover.md`
   (no new migrations since the aichat 0056 note).
