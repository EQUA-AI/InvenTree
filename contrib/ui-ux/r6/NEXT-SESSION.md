# Next session: pending tasks (22 October 2026)

Repo `/Users/Aniketkumar/IdeaProjects/InvenTree`. `IOT-Merge` and `inventTree-aniket`
are identical at `c55227c95` locally. Localhost (http://localhost:5173, backend
:8000, local copy of Azure from 21 Oct 17:03 UTC) runs that commit.

## Done today

- O27: recorded Cosmos month (1 Jul–1 Aug 2025) shown as live, switch
  `AIMMS_RECORDED_TELEMETRY_AS_LIVE` (default on).
- UI polish, following "avoid secondary labels and descriptions; let the layout
  explain":
  - station mimic and pump-unit mimic (`r6/ui-mimic`);
  - machines list, Health signals and Parts tables (`r6/ui-tables`).

## Pending

1. **Push** `IOT-Merge` and `inventTree-aniket` (local is 4 commits ahead of
   GitHub on both), after the owner's go-ahead.
2. **Update the failing tests** that still assert removed wording. The list is at
   the top of `contrib/ui-ux/r6/HANDOFF.md` ("UI polish checkpoint"). Then re-run
   S0-S8 on the Mac GPU (`src/frontend/playwright.gpu-s*.config.ts`, one suite at a
   time).
3. **Hyperagent design review** of the polished screens (see the prompt below),
   and fix what both agents flag until both score 95+.
4. Remaining "Unknown" / "not supplied" places the owner may still spot: apply the
   same rule (a dash plus a tooltip, no extra captions).
5. Parked: `r6/backend-fix-a` (79 pre-existing backend test failures); the R8
   procurement demo (`contrib/ui-ux/r8-procurement/gap-analysis.md`).
6. Deployment of aimms-dev: by the deployment team, per
   `contrib/ui-ux/r6/deployment-handover.md`. Azure has 988 migrations; an
   IOT-Merge build adds only the no-op aichat 0056.

## Prompt for the new session (hyperagent-frontend skill)

> Use the hyperagent-frontend skill. Review the AIMMS UI polish on branch
> IOT-Merge (commit c55227c95) in /Users/Aniketkumar/IdeaProjects/InvenTree,
> running at http://localhost:5173. Screens: Machines list
> (/web/machines/index/), Cedar Creek station Pumphouse mimic, a pump unit
> mimic (e.g. P7), Health, Performance, and the station and Pump 08 Parts tabs.
> Changed files: `git diff 8a8335959 c55227c95 -- src/frontend`. Reports:
> contrib/ui-ux/r6/reports/ui-mimic.md and ui-tables.md. Owner rules: follow
> contrib/ui-ux/r6/masterdesignprompt.md and designprompt1.md; avoid secondary
> labels and descriptions and let the layout explain; never invent values
> (missing = dash plus tooltip). Have "Mantine & InvenTree Frontend Engineer
> (Fable)" and "(Gemini)" each score the screens out of 100 from fresh desktop
> (1440x900) and phone (390x844) screenshots in light and dark mode. Fix their
> findings and repeat until both give 95+. Send only code, diffs and fixture or
> local screenshots, never credentials or Azure data.
