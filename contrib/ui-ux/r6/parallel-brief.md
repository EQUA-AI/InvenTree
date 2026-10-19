# Parallel stream brief (O13)

For an agent working one R6 stream in its own worktree. Read this first, then
`HANDOFF.md` (read order and rules), `designprompt1.md` (applies to every
screen), `owner-approval.md` (O11-O13) and the sprint ledger for your tickets
(`s2.md`, `s3.md`, ...). Authoritative R6 pack:
`/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/EQUA_AIMMS_Handoff_R6_FINAL_UI_UX`
(backlog row in `EQUA_AIMMS_Implementation_Backlog_R6.csv`, screens in
`EQUA_AIMMS_Numbered_Screen_Inventory_R6.md`, acceptance groups in
`EQUA_AIMMS_Acceptance_and_Delivery_R6.md`, areas in
`EQUA_AIMMS_UI_UX_Blueprint_R6.md`).

## Bar: built and tested

A ticket is finished when its UI and any backend are built on the native
architecture, covered by targeted tests that pass, and its real rendered
captures (desktop and 390 px phone; light and dark where colour matters) have
been inspected and the findings fixed. Anything that needs a provider, live
data, Azure, hardware or an owner decision that does not exist is built up to
that edge, shows an honest state in the UI, and is reported **Blocked** with the
missing fact named. Never claim R6 acceptance; never invent physical, domain or
safety facts; unknown is never shown as zero, none or healthy.

## Where you work

- Only inside your worktree (`/Users/Aniketkumar/IdeaProjects/InvenTree-r6/<stream>`).
  Never edit, commit or run git commands that change state in the main checkout
  `/Users/Aniketkumar/IdeaProjects/InvenTree`.
- Commit on your branch `r6/<stream>` as you finish each ticket:
  `git -c user.name=aniketdev2018 -c user.email=aniket22122017@gmail.com commit`.
  Commit messages carry no Co-Authored-By or "Generated with" lines. No push,
  no pull request, no other branches.
- Do **not** edit the shared ledgers (`sprint-backlog.csv`, `screen-plan.csv`,
  `contracts.csv`, `HANDOFF.md`, `owner-approval.md`, `s*.md`) or
  `.github/workflows/frontend.yaml`. Instead write
  `contrib/ui-ux/r6/reports/<ticket>.md` per ticket: status (Done technically /
  Blocked / In progress), what was built, files, the exact checks run with
  counts, captures inspected and what was fixed, open items with the missing
  fact, and any CI steps or contract rows that should be added.
- No new dependencies or downloads. If a ticket truly needs one, stop that
  ticket and report it.
- No Azure, no existing database, no other containers. Backend tests only via
  `python3 contrib/ui-ux/r6/backend_baseline.py --output <dir> <labels>` from
  your worktree (offline container on its own SQLite; safe in parallel).
  Evidence goes under your worktree's `data/devscratch/r6-<stream>/` (ignored).

## Running the frontend checks

From `src/frontend` in your worktree, always with your own port (given in your
task) so streams do not collide:

```
env -i PATH="$PATH" HOME="$HOME" AIMMS_HARNESS_PORT=<port> node node_modules/@playwright/test/cli.js test --config=playwright.s3.config.ts --reporter=list <spec>
node node_modules/typescript/bin/tsc --noEmit --project tsconfig.s3.json
node node_modules/typescript/bin/tsc --noEmit
```

Captures land in `$TMPDIR/aimms-r6-<sprint>-browser-<port>/`; with `env -i`
that is `/tmp/...`. Unit tests: `node --input-type=module -e 'import {startVitest} from "vitest/node"; ...'`
as in `data/devscratch/r6-s2/2026-10-19/p13-parts/run-frontend.sh` of the main
checkout (read only). Format with the pinned tools only:
`node /tmp/aimms-r6-s0-tools/npm/_npx/db52655fa99ef31e/node_modules/@biomejs/biome/bin/biome check --write <files>`
and `/tmp/aimms-r6-s0-tools/python/bin/ruff check --preview` / `ruff format --preview` on files you changed.

Per ticket run: types (default and your sprint's tsconfig), your new and the
directly affected specs and units, Biome/Ruff on changed files, and the backend
modules you touched. The full replay across all sprints runs at merge, not per
ticket. Machines are shared: if a browser case times out under load, rerun that
spec alone and keep both logs.

## Harness patterns

S0-S3 harness pages live in `src/frontend/playwright/` (`s0-native.html?surface=machine&entry=/machines/machine/18/<panel>/`,
`role=viewer|editor`, `scheme=dark`); fixtures in `tests/s0/machineFixtures.ts`,
`tests/s2/equipmentFixtures.ts`, `tests/s3/performanceFixtures.ts`;
`nativeBaseline()` fails any unmocked request. Mantine 9: `Grid` uses `gap`;
SegmentedControl/Switch inputs are visually hidden (click the label / switch).
Add new specs to the sprint tsconfig's `files`.
