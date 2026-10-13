# Handover: pump-station monitoring

State on 2026-10-11 at commit `921fa697f` (`inventTree-aniket`; `IOT-Merge` is
still at `4a92f54cc`, before the workbook population commits).
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
| `inventTree-aniket` | `921fa697f` | Working branch for this work (pump-station merge + workbook population) |
| `IOT-Merge` | `4a92f54cc` | Waiting for Lowkey-wizard's review; behind `inventTree-aniket` by the population commits |
| `equa/customizations` | `988ce86f8` | The deployable line; does not have the pump-station work yet |
| `merge/iot-into-customizations` | `16c6cc511` | The merge commit itself; one commit behind |
| `IOT` | `a362afc7f` | Older; to be moved forward after review |

The two lines were merged on 2026-10-11 (`16c6cc511`). Intended path: work on
`inventTree-aniket` -> `IOT-Merge` for review -> `equa/customizations` for
deployment -> `IOT`.

## 4. Environments

- **Azure dev database (`inventree`)**: already on the merged schema (966
  migrations). Three stations are registered, activated and polled to the
  end of the recorded window, 2025-08-01 02:59 (see item 8): Millbrook
  (id 60), Cedar Creek (93), Maple Grove (94); 30 pumps, 1,452 signal
  bindings, 404 with reviewed limits. Pine Hollow (230) is registered but not
  activated (item 12).
- **Azure Cosmos (`aimms/pumphouse_readings`)**: the July 2025 dump is
  migrated for the same three stations, 2025-07-02 01:00 to 2025-08-01 02:00
  UTC: Cedar Creek 432,510 documents, Millbrook 325,564, Maple Grove 303,905.
  The 2025-07-12 onward part was backfilled on 2026-10-12/13 with
  `contrib/cosmos/devtools/backfill_station.sh` and checked hour by hour
  against Cassandra's partition index: every hour with source data is present
  (384 / 449 / 418 hours in that part); the empty days are empty in Cassandra
  too. Pine Hollow (Annapurna PH, `4162f034-…`) was migrated on 2026-10-13,
  2025-07-01 03:00 to 2025-08-01 02:00 UTC: 270,760 documents in 674 hours,
  matching Cassandra hour for hour. Its rows sit under its own uuid in
  Cassandra, not the shared regional partition, so it needed
  `PARENT=4162f034-…` (`--parent-entity-uuid`). Database throughput is back
  at 400 RU/s. The other ten stations have nothing in Cosmos. The first
  2025-07-01 03:00 to 07-02 01:00 (about 22 hours) of the three original
  stations is in Cassandra but not yet in Cosmos.
  One older alarm stands on Electronics Test Bench 1; nothing from testing is
  left raised. Cedar Creek (93) has been populated from the UI population
  workbook (section 5a): locations, placements, profiles, 48 support
  equipment, CC-REF parts and components, warehouse, companies, stock,
  history and staged work orders, all under client `internal`.
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
population workbook (`assets/population.py`, `assets/population_workbook.py`,
`assets/population_operations.py`).
It has three modes: `--plan` (read-only report, the default), `--dry-run`
(applies inside a transaction and rolls back), and `--apply`, which requires
`--backup-ref <reference>` and refuses a non-local database host unless
`--allow-remote` is given. Every
write goes through the existing services (`save_location`, `register_station`,
`ensure_pump`, `validate_machine_profile`, `transition_work_order`), so their
invariants hold, and replaying the same workbook is a no-op.

Stages, in order: physical locations, station and pumps, generic equipment,
placements, then — only when the workbook has a Parts Catalogue sheet — part
categories, parts, component occurrences and installed-part links; then
warehouse locations (`StockLocation`), companies, manufacturer parts (and a
supplier part only when the sheet names a supplier *and* a SKU), stock items,
dated maintenance history (`AssetMaintenanceRecord`, never linked to a work
order) and staged work orders (created `draft`, planned through the lifecycle
service when the sheet asks). Without the catalogue sheet the parts-dependent
stages are reported as blocked, listing the missing IPNs, rather than inventing
parts. Staff accounts, PM plans (governed, customer-scoped procedures),
attachments (no files ship with the package) and repair packets are reported
under `manual` and not written.

Two flags bind a workbook to a plant that was registered from its telemetry
source first: `--client-code <code>` populates an existing client instead of
the workbook's (needed on the single-site deployment, whose scope resolver only
shows `internal`), and `--station <id>` adopts an already-registered station.
The registered identity (`source_namespace`/`source_entity_uuid`/`source_key`,
public UUID) is immutable and kept; the workbook's provisional identity is
reported as a warning. The station is renamed to the workbook name and its
pump labels follow.

Local sandbox (nothing touches Azure): `contrib/cosmos/devtools/sandbox.sh
up|down|status|shell|manage <args>`. It runs a throwaway pgvector Postgres
(`pop-pg`) and the merged image (`pop-test`) on http://localhost:8010 with the
production scope mode (`granted_client_scope_resolver`). To see registered
stations and pumps in the UI, grant the user the client:
`sandbox.sh manage grant_client_scope <user> cedar-creek-regional-water`.
Verified there with the Cedar Creek master workbook: 45 locations, station +
14 pumps, 48 equipment, 63 machines visible, 17 draft components on each pump,
8 warehouse locations, 30 companies, 6 manufacturer parts, 6 stock items, 132
history rows, 18 work orders (8 planned); replay is a no-op.

Azure (2026-10-11): applied with `--client-code internal --station 93` after
`manage.py dbbackup` (`data/backup/InvenTree-db-2026-10-11-155535.psql.bin.gz`,
sha256 `f47adfac…f08e`). Station 93 keeps its `klsw` identity and is now named
`Cedar Creek Final Effluent Pump Station`; its pumps keep their 16 `PS-*`
dictionary components alongside the 17 `CC-REF` ones from the workbook. The
run is one transaction and takes about an hour over this link because every
save also fires the plugin/event hooks; do not start a second one alongside.

Undoing a run: `contrib/cosmos/POPULATION_ROLLBACK.md`. In short, only a restore
of the pre-apply backup is exact. Placement history protects machines and
locations from deletion, so a selective undo cancels, deactivates and archives
rather than deletes.

Portable demo dataset (Import Sequence step 18): `contrib/cosmos/demo/cedar-creek/`.
`build.sh <workbook>` builds it from an empty database on its own containers
(`demo-pg`, `demo-app`, network `demonet`; never `pop-test` or Azure), replays it,
runs the QA gates and writes a `pg_dump` plus `build-report.json` to
`data/demo/cedar-creek/<version>/`. Version `2026-10-12.1` built OK (22/22 gates,
restore checked). Bump `version` in `manifest.json` for any change.

Read-only checks for the mechanically verifiable QA gates (Import Sequence
step 17): `contrib/cosmos/devtools/cedar_creek_qa_gates.py`, run through the
Django shell of the container that points at the database (usage in its
docstring; defaults describe Azure station 93, environment variables select
the sandbox). On the sandbox it reports 22 pass / 0 fail. Against Azure it
reported **22 pass / 0 fail on 2026-10-13** (QA-08 `klsw`/`PH_3`, QA-15 33
components on each of the 14 pumps, QA-55 113 machines); see open item 11 for
how it was run without writing.

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
- Commit only on `inventTree-aniket`. Moving `IOT-Merge`, `equa/customizations`
  or `IOT` forward is the owner's call, not part of routine work.

## 7. Open items

1. Lowkey-wizard's review of `IOT-Merge`, then bring `equa/customizations` up
   to it and redeploy the dev app (web and worker).
2. Switch Cosmos polling on in the deployed app: set
   `AIMMS_COSMOS_PUMPHOUSE_ENABLED` and give it Cosmos access.
3. Email alerts need a mail host configured; only bell notifications go out now.
4. Fixed 2026-10-12: a widget added to the dashboard used to arrive at its
   minimum size (2x1 for the count widgets, a 108x64 px tile). It now gets
   an explicit 6x4 entry below the existing widgets, never below its minimum
   or wider than the grid (`dashboard/dashboardLayoutUtils.ts`; a widget can
   declare `defaultWidth`/`defaultHeight`). Layouts already saved in a
   browser keep their sizes.
5. The `TransactionTestCase` tests in `assets/test_locations.py`
   (`ConcurrentLocationTests`) error on the test-database flush, because
   migration `assets.0020_retire_synthetic_ledger` is state-only and leaves
   tables with foreign keys behind. Left to its author by agreement. Do not
   run them with `--keepdb`; the rows they leave make unrelated tests fail.
6. The whole backend suite was run end to end on the merged code on
   2026-10-12 (`invoke dev.test --fork`, PostgreSQL, CI-like env, local
   sandbox only): 5,023 tests, 47 failures, 147 errors. None comes from the
   pump-station or population work. By cause:
   - 74 need `AIMMS_SINGLE_SITE_POLICY_KEY`. No CI workflow sets it and there
     is no `ai/.env`; with a dummy key the voice/approval modules pass (43/43).
   - ~70 are open item 5's flush failure plus the duplicate-key rows it leaves.
   - 12 are dev-only packages missing from the image (`requests_mock`,
     `django_test_migrations`, `pdfminer`, `scim2_client`).
   - 50 are in `aichat` and persist with the key set (timezone/erasure
     timestamps, thread boundaries); 42 more appear only with the key.
   - Pre-existing, from before this work: `RuleSet` coverage (51 models not in
     any ruleset, INVE-E5), `aichat` models with missing migrations,
     `test_po_line_merge_pricing` (`None` to `Decimal`).
   - Environment only: untranslated label (no `compilemessages`), report logo
     (no static files).
   The 4 lineage/preflight failures naming `assets.0022` were the migration
   being added mid-run; they pass on a fresh database. The frontend is clean:
   `tsc` 0 errors, 202/202 unit tests, mimic 14/14, machine locations 19/19.
7. Fixed 2026-10-12: the local dev database was a PostgreSQL 17 data directory
   without `vector`, which the pinned `pgvector/pgvector:pg15` image cannot
   open. `contrib/container/rebuild-dev-db.sh` moved it aside
   (`data/pgdb-pg17-20261012-130808`, kept), dumped it with postgres:17,
   restored it into a fresh pg15 + `vector` 0.8.7 directory (49 machines,
   535 parts, 8 users kept; dump in `data/backup/`), and the 88 merged
   migrations then applied cleanly (`migrate --check`: none pending).
   Still to do: the compose dev image `inventree-dev-image` predates the merge
   (no `django_fsm`), so rebuild it before `docker compose up`:
   `docker compose --project-directory . -f contrib/container/dev-docker-compose.yml build`.
8. The data is a recorded window, displayed as if current. Cosmos holds it to
   2025-08-01 02:00 UTC, and since 2026-10-13 the app is polled to the same
   end. `discover_data_range` moved the recorded end (`data_ranges.to`) of
   stations 60/93/94 to 2025-08-01 02:59. Replaying the 20 days in between
   from this Mac was not practical (~230 ms per Azure Postgres round trip;
   5 minutes moved about one minute of plant time). The app keeps only the
   latest value per signal (`MachineSignalState`), and trends are read from
   Cosmos directly, so with the owner's approval the three checkpoints were
   moved forward with `IngestionCheckpoint.advance_to` to 2 minutes before
   each end (`data/devscratch/cedar-creek/azure_skip_ahead.py`, dry run
   first), then polled to the end with the real task
   (`azure_poll_catchup.py`, 9.6 minutes, no errors). Skipped: alarm
   raise/clear events between 2025-07-12 and 08-01. Anomalies and
   critical-alarm notifications were unchanged (3 / 1 active critical;
   6 notifications). The 2025-07-12 00:01 to 08-01 02:57 stretch was never
   applied as state. Live plant data still needs a live Cosmos source.
9. Cedar Creek workbook items the import deliberately leaves to a person
   (reported under `manual` by the command): 12 demo staff accounts, 94 PM
   plans as governed procedures, 134 attachments from the archive, 10 repair
   packets raised from the staged work orders, and 6 supplier offers once a
   distributor SKU is verified. Work-order assignees are free text until the
   accounts exist (QA-25).
10. Cedar Creek pumps carry both the 16 `PS-*` dictionary-crosswalk
    components (bound to signal points) and the 17 `CC-REF` workbook
    components. Whether to retire one set is a data decision for the owner.
11. The Azure database stopped accepting the `pgadmin` password held by the
    local `aimms-azure-server` container on 2026-10-11
    (`FATAL: password authentication failed`). Cause: the server's activity
    log shows a `flexibleServers/write` by Lokesh@equa.work at 17:31 UTC
    (20:31 local, +03:00), followed by a firewall-rule write at 17:34 UTC,
    which fits a reset of the admin password. The workbook apply was already
    connected, so it still committed (`APPLY_EXIT=0` at 20:54 local). The
    password in the container is the one from before the change, and the
    only Entra admin on the server is Lokesh, so there is no other way in.
    Resolved 2026-10-13: the owner supplied the current password. It is kept
    in `data/azure-db.env` (mode 600, git-ignored, one line
    `INVENTREE_DB_PASSWORD=...`) and passed per command with
    `docker exec --env-file data/azure-db.env aimms-azure-server ...`; the
    container's built-in value is still the old one. The QA gates were then
    run read-only (22 pass / 0 fail), with two guards that are needed for any
    read-only Django command against Azure from this container:
    - `-e PGOPTIONS='-c default_transaction_read_only=on'` makes the server
      refuse every write in the session.
    - `-e INVENTREE_PLUGIN_FILE=<file holding the stock two-line plugins.txt>`.
      Django startup is not read-only: when the plugins file's hash differs
      from `_PLUGIN_FILE_HASH` in the database it installs plugins and saves
      the new hash. Azure holds the hash of the stock empty file
      (`dd72fc1e…`); the local `data/plugins.txt` lists
      `inventree-brother-plugin` (six times) and hashes differently, so
      **starting the app from this checkout against Azure would install that
      plugin and write to the database**. The plugin-registry reload still
      tries to take a lease and log the failure; the read-only session
      refuses both (0 new `error_report_error` rows).
12. Azure database changes made 2026-10-13 (owner-approved), after a backup
    (`data/backup/InvenTree-db-2026-10-13-063634.psql.bin.gz`):
    - Applied `assets.0022_var_unit`, the only migration pending (adds the
      `var`/`VAr` units only). Lineage check: nothing applied that the code
      does not know.
    - Registered Pine Hollow Lift Station (`PH_6`): station 230, pumps
      231-234, from `contrib/cosmos/review/pine-hollow.manifest.json` and
      `pine-hollow.tags.json` (built from migrated Cosmos documents). It is
      **not activated**: all 601 points are pending, 295 of them OPC-UA node
      ids (`NS=4;S=…`). Next: a person fills
      `contrib/cosmos/review/pine-hollow.review.pending.json`, then
      `apply_dictionary_review`, then `onboard_pumphouse_estate … --activate`
      (its data range is already recorded, as activation requires), then
      reviewed limits via `apply_signal_limits`.
    - Recorded data ranges for stations 60/93/94 and Pine Hollow
      (`discover_data_range --from 2025-07-01 --to 2025-08-02`); see item 8.
    - `data/plugins.txt` deduplicated (one `inventree-brother-plugin` line;
      backup `data/plugins.txt.bak-20261013`). Its hash still differs from
      Azure's, so item 11's warning stands.

## 8. Further reading

| Document | Covers |
|---|---|
| `contrib/cosmos/HANDOFF.md` | Plant configuration step by step: source, dictionary review, schematic, activation |
| `contrib/cosmos/POPULATION_ROLLBACK.md` | What a workbook population run changes and how to undo it |
| `contrib/cosmos/demo/cedar-creek/README.md` | Portable Cedar Creek demo dataset: manifest, build, restore |
| `contrib/cosmos/D17_IDENTITY.md` | Which identity reads Cosmos; read-only proof; asks for the Azure owner |
| `contrib/cosmos/README.md` | The Cosmos container and its tools |
| `contrib/cosmos/THRESHOLDS.md` | How limits were reviewed and what each means |
| `contrib/cosmos/DEPLOYMENT_CHECKLIST.md`, `BLOCKERS.md` | Rollout steps and known blockers |
| `contrib/container/DEV_RUNBOOK.md` | Building and restarting the dev stack |
| `contrib/container/asset-locations.md` | The physical-location workspace on the Machines page |
| `contrib/aimms_production_env.md` | Environment variables in production |
| `specs/002-cosmos-pumphouse-connector/` | Original plan, tasks and an earlier handover (the folder is git-ignored; new files there need `git add -f`) |
