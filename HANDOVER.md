# Handover: pump-station monitoring

State on 2026-10-11 at commit `4a92f54cc` (`inventTree-aniket` = `IOT-Merge`).
This is the short current-state page. The detail is in documents that already
exist, listed in section 8; read this first and those when you need depth.

## 1. What is built

Recorded pump-station telemetry is read from Azure Cosmos, turned into the
latest value per signal, checked against reviewed limits, and shown on a
station drawing (the "mimic"), with alarms a maintenance user can act on.

Flow: Cosmos source -> scheduled poll -> signal states -> thresholds ->
anomalies (alarms) -> bell/email notification -> mimic, Health tab, Machines
list, dashboard widget.

What a user can do today:

- See a station drawing with every pump bay, and a pump drawing with its readings.
- See alarms on the bay, in a table, on the Health tab, on the Machines list
  (Alarms column and "In alarm only" switch) and in a dashboard widget.
- Acknowledge an alarm, dismiss a wrong one with a note, or raise a repair from it.
- Read a machine's past alarms, with search and a severity filter.
- Get a bell notification for a critical alarm (once per condition, at most
  hourly per signal); opt out with the user setting `NOTIFY_MACHINE_ALARMS`.

## 2. Where the code is

Backend, under `src/backend/InvenTree/`:

| Area | Files |
|---|---|
| Station/pump registry, dictionary | `assets/registry.py`, `assets/registry_api.py`, `assets/dictionary_review.py` |
| Bringing a station live | `assets/activation.py` (`activate_station`) |
| Scheduled poll | `assets/tasks.py` (`poll_cosmos_pumphouse_sources`) |
| Cosmos readers | `machine_health/connectors/cosmos_pumphouse.py`, `cosmos_replay.py` |
| Alarms | `machine_health/services/anomalies.py` (`evaluate_thresholds`, `dismiss_anomaly`) |
| Notifications | `machine_health/services/alarm_notifications.py` |
| Mimic data | `machine_health/services/mimic.py`, `machine_health/mimic_api.py`, `machine_health/layouts/` |
| Recorded data shown as current | `machine_health/services/display_time.py` |
| Health API (`/api/machine-health/`) | `machine_health/api.py` |
| Machine list and alarm counts | `assets/api.py` (`with_open_alarms`), `assets/location_api.py` |
| Commands | `assets/management/commands/` (`test_alarm`, `apply_signal_limits`, `register_pump_station`, `deploy_preflight`, ...) |
| Feature flags | `aimms_flags.py` (`AIMMS_COSMOS_PUMPHOUSE_ENABLED` gates the poll) |
| Client scope | `src/backend/tasks/scope.py` |

Frontend, under `src/frontend/src/`:

| Area | Files |
|---|---|
| Machines page | `pages/assets/MachineIndex.tsx`, `pages/assets/locations/LocationWorkspace.tsx` |
| Machine page and tabs | `pages/assets/MachineDetail.tsx`, `pages/assets/health/` |
| Mimic | `pages/assets/health/PumphouseMimic.tsx`, `PumpMimic.tsx`, `health/mimic/` |
| Alarm table and history | `health/mimic/AlarmTable.tsx`, `health/AlarmHistory.tsx` |
| Shared alarm badges | `components/items/MachineAlarmBadges.tsx` |
| Dashboard widget | `components/dashboard/widgets/MachineAlarmsWidget.tsx` |

`tables/assets/AssetMachineTable.tsx` is not used by any page since the merge.

## 3. Branches

| Branch | At | Role |
|---|---|---|
| `inventTree-aniket` | `4a92f54cc` | Working branch for this work |
| `IOT-Merge` | `4a92f54cc` | Same commit; waiting for Lowkey-wizard's review |
| `equa/customizations` | `988ce86f8` | The deployable line; does not have the pump-station work yet |
| `merge/iot-into-customizations` | `16c6cc511` | The merge commit itself; one commit behind |
| `IOT` | `a362afc7f` | Older; to be moved forward after review |

The two lines were merged on 2026-10-11 (`16c6cc511`). Intended path: work on
`inventTree-aniket` -> `IOT-Merge` for review -> `equa/customizations` for
deployment -> `IOT`.

## 4. Environments

- **Azure dev database (`inventree`)**: already on the merged schema (966
  migrations). Three stations are registered, activated and polled to the end
  of the recorded window (2025-07-11): Millbrook (id 60), Cedar Creek (93),
  Maple Grove (94); 30 pumps, 1,452 signal bindings, 404 with reviewed limits.
  One older alarm stands on Electronics Test Bench 1; nothing from testing is
  left raised.
- **Deployed dev app**: still runs older `equa/customizations` code against
  that database, with Cosmos polling switched off. Nothing new is visible
  there until it is redeployed from the merged code.
- **Local**: the app can be run from this checkout against the Azure database.
  The database is far away (about 0.25 s per query), so pages are slow and
  bulk work through the app is not practical.

## 5. Run and test

- Dev stack and rebuild steps: `contrib/container/DEV_RUNBOOK.md`. The merged
  migrations need the `vector` extension, so the database must be a pgvector
  image (the compose file names `pgvector/pgvector:pg15`).
- Backend tests: `python manage.py test <labels>` inside the container. On an
  image without the dev requirements add
  `--testrunner django.test.runner.DiscoverRunner`.
- Frontend: `npx tsc --noEmit -p .` and `npm run test:unit` in `src/frontend`.
- Mocked browser tests (no backend needed):
  `node node_modules/@playwright/test/cli.js test --config=playwright.mimic.config.ts`
  and `--config=playwright.machine-locations.config.ts`. The second expects a
  Vite dev server already running on `127.0.0.1:5173`.
- A real alarm for a demo: `manage.py test_alarm --machine <id> --yes`, and
  `--clear` to put everything back exactly as it was.
- Format and lint with the repository hooks (`prek run --files ...`), which
  pin the tool versions.

## 5a. Populating a plant from a workbook

`manage.py populate_from_workbook <xlsx>` builds a plant from the UI
population workbook (`assets/population.py`, `assets/population_workbook.py`).
It has three modes: `--plan` (read-only report, the default), `--dry-run`
(applies inside a transaction and rolls back), and `--apply`, which requires
`--backup-ref <reference>` and refuses a non-local database host unless
`--allow-remote` is given. Every
write goes through the existing services (`save_location`, `register_station`,
`ensure_pump`, `validate_machine_profile`), so their invariants hold, and
replaying the same workbook is a no-op.

Stages, in order: physical locations, station and pumps, generic equipment,
placements, then — only when the workbook has a Parts Catalogue sheet — part
categories, parts, component occurrences and installed-part links. Without that
sheet the parts-dependent stages are reported as blocked, listing the missing
IPNs, rather than inventing parts.

Local sandbox (nothing touches Azure): `contrib/cosmos/devtools/sandbox.sh
up|down|status|shell|manage <args>`. It runs a throwaway pgvector Postgres
(`pop-pg`) and the merged image (`pop-test`) on http://localhost:8010 with the
production scope mode (`granted_client_scope_resolver`). To see registered
stations and pumps in the UI, grant the user the client:
`sandbox.sh manage grant_client_scope <user> cedar-creek-regional-water`.
Verified there with the Cedar Creek master workbook: 45 locations, station +
14 pumps, 48 equipment, 63 machines visible, 17 draft components on each pump.

## 6. Rules to keep

- No pull request without a human reviewing it first, and no AI-written issues
  (`AGENTS.md`).
- Ask the owner before anything that writes to the Azure database, including
  starting an app against it. Reading is fine.
- Credentials are never committed and never printed. Get them from the owner.
- When running an app against the Azure database, serve it through a small
  fixed pool (`gunicorn --worker-class gthread --workers 1 --threads 4`). The
  server allows few connections and a thread-per-request server exhausted them
  on 2026-10-11.
- Check migration lineage before any deployment (`assets/deploy_lineage.py`,
  `manage.py deploy_preflight`). Do not deploy one line's code onto a database
  migrated by another.
- Do not commit translation catalog churn (`src/frontend/src/locales`); the
  image build extracts and compiles them.
- One backend test run at a time against a given test database.
- Commits on this work have carried only the developer as author, with no AI
  co-author trailer.

## 7. Open items

1. Lowkey-wizard's review of `IOT-Merge`, then bring `equa/customizations` up
   to it and redeploy the dev app (web and worker).
2. Switch Cosmos polling on in the deployed app: set
   `AIMMS_COSMOS_PUMPHOUSE_ENABLED` and give it Cosmos access.
3. Email alerts need a mail host configured; only bell notifications go out now.
4. A widget added to the dashboard comes in as a very small tile until it is
   resized in Edit Layout. This affects every widget, not only Machine Alarms.
5. The `TransactionTestCase` tests in `assets/test_locations.py`
   (`ConcurrentLocationTests`) error on the test-database flush, because
   migration `assets.0020_retire_synthetic_ledger` is state-only and leaves
   tables with foreign keys behind. Left to its author by agreement. Do not
   run them with `--keepdb`; the rows they leave make unrelated tests fail.
6. The whole backend suite has not been run end to end on the merged code.
7. The plain local Postgres has no `vector` extension, so the old local dev
   database cannot take the merged migrations until it is rebuilt.
8. The data is a recorded window ending 2025-07-11, displayed as if current.
   Live plant data needs a live Cosmos source.

## 8. Further reading

| Document | Covers |
|---|---|
| `contrib/cosmos/HANDOFF.md` | Plant configuration step by step: source, dictionary review, schematic, activation |
| `contrib/cosmos/README.md` | The Cosmos container and its tools |
| `contrib/cosmos/THRESHOLDS.md` | How limits were reviewed and what each means |
| `contrib/cosmos/DEPLOYMENT_CHECKLIST.md`, `BLOCKERS.md` | Rollout steps and known blockers |
| `contrib/container/DEV_RUNBOOK.md` | Building and restarting the dev stack |
| `contrib/container/asset-locations.md` | The physical-location workspace on the Machines page |
| `contrib/aimms_production_env.md` | Environment variables in production |
| `specs/002-cosmos-pumphouse-connector/` | Original plan, tasks and an earlier handover (the folder is git-ignored; new files there need `git add -f`) |
