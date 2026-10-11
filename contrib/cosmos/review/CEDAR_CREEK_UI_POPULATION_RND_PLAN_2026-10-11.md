# Cedar Creek UI Population: R&D and implementation plan

Date: 2026-10-11

## Scope requested

Use the Cedar Creek population workbook and package docs to:

1. Create missing machines.
2. Populate/update data for machines that already exist.
3. Continue with additional dependent population work based on package data.

Important safety constraint: no writes to Azure PostgreSQL without explicit owner permission and a backup snapshot first.

## Inputs explored

- `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/CEDAR_CREEK_AIMMS_UI_POPULATION_MASTER_2026-10-08.xlsx`
- `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/CEDAR_CREEK_AIMMS_UI_DATA_POPULATION_2026-10-08/00_START_HERE_AND_EXECUTIVE_HANDOFF.md`
- `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/CEDAR_CREEK_AIMMS_UI_DATA_POPULATION_2026-10-08/01_UI_FIELD_POPULATION_GUIDE.md`
- `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/CEDAR_CREEK_AIMMS_UI_DATA_POPULATION_2026-10-08/05_INTEGRATION_AND_QA_PLAYBOOK.md`
- `/Users/Aniketkumar/Desktop/x-WORK/Repo/equaAI/CEDAR_CREEK_AIMMS_UI_DATA_POPULATION_2026-10-08/PACKAGE_MANIFEST.md`

Workbook has 31 sheets. Key sheets include: `Facility Locations`, `Machine Records`, `Machine Profiles`, `Component Occurrences`, `Machine Part Links`, `Import Sequence`, `QA Gates`.

## Existing code paths to reuse (no raw SQL)

### Best entry points

- Station/pump onboarding command:
  - `src/backend/InvenTree/assets/management/commands/onboard_pumphouse_estate.py`
  - service: `src/backend/InvenTree/assets/pumphouse_estate.py`
- Registry station and pump creation:
  - `src/backend/InvenTree/assets/registry.py` (`register_station`, `ensure_pump`)
- Machine CRUD API:
  - `src/backend/InvenTree/assets/api.py` (`AssetMachineList`, `AssetMachineDetail`)
- Location create/update/transfer:
  - `src/backend/InvenTree/assets/location_api.py`
  - `src/backend/InvenTree/assets/locations.py` (versioned transfer, idempotency)

### Constraints to design around

- `AssetMachine.name` is globally unique.
- Pumphouse identity has uniqueness on `(source_namespace, source_entity_uuid)`.
- Pump identity has uniqueness on `(parent, source_key)`.
- Only pumps can be children of a station.
- Location tree has depth, parent, and same-client constraints.
- Registry picker currently surfaces `PS-*`; Cedar Creek references use `CC-REF-*`.

## Design choice

Implement a dedicated management command that is safe, idempotent, and previewable:

- New command (proposed): `populate_cedar_creek_ui`
- Source input: workbook path
- Modes:
  - `--plan` (parse + validate + no DB write)
  - `--dry-run` (transaction rollback)
  - `--apply` (writes)
- Guardrails:
  - Requires explicit flag and environment check for Azure targets.
  - Emits a signed execution report (counts, created/updated/skipped/errors).
  - Uses deterministic idempotency keys for placement transfer.

Why command over ad-hoc script:

- Fits repo conventions (`manage.py` command workflows).
- Reuses validated model/service logic.
- Easier to test and re-run for partial completion.

## Proposed phased implementation

1. Parse and validate workbook
   - Validate required sheets and required columns.
   - Build in-memory maps keyed by `client_code`, location `code`, and `machine_key`.
   - Validate referential integrity across sheets (machine->location, profile->machine, component->machine).

2. Identity lock and diff stage
   - Resolve target client and existing station by source identity.
   - Produce deterministic diff: create/update/no-op per location and machine.
   - Refuse unsafe mutations (identity conflicts, duplicate names, parent mismatch).

3. Location stage
   - Upsert location hierarchy from root to leaves.
   - Preserve existing unaffected nodes.

4. Station and pumps stage
   - Create/update station via registry identity path.
   - Ensure pump slots `P1..P14` using `ensure_pump`.

5. Machine detail/profile stage
   - Upsert mutable machine fields (description/model/serial/manufacturer/active/profile).
   - Apply profile JSON using existing profile validators.

6. Placement stage
   - Assign physical locations using transfer service with version checks and idempotency keys.

7. Component and machine-part stage
   - Only after parts exist and can resolve safely.
   - For `CC-REF-*` linkage, use API/backend path that accepts FK part IDs directly.

8. Post-run QA stage
   - Execute machine/location/profile consistency checks mapped to `QA Gates` P0 subset.

## Backup and permission gate (must happen before apply)

No write should run until:

1. Owner permission is captured for Azure PostgreSQL write.
2. Backup snapshot is created and verified.

Documented backup command path in this repo:

- `invoke backup` (calls `dbbackup` and `mediabackup`)
- docs: `docs/docs/start/backup.md`
- implementation: `tasks.py` backup task

If running in dockerized backend context:

- `docker compose run --rm inventree-server invoke backup`

## Deliverables to implement next

1. `populate_cedar_creek_ui` command with plan/dry-run/apply modes.
2. `assets/cedar_creek_population.py` service layer containing pure import steps.
3. Parsing module for workbook extraction and schema checks.
4. Focused backend tests:
   - parse validation
   - idempotent rerun
   - station+pump identity constraints
   - location transfer versioning
   - machine update behavior for existing records
5. Short operator README with runbook commands and rollback notes.

## Initial risk register

- Identity collision with pre-existing data in target tenant.
- Station/pump source identity drift versus workbook assumptions.
- Incomplete part catalog causing component/machine-part linkage failures.
- Accidental write to Azure without backup if apply gate is bypassed.
- Translation/catalog or unrelated churn if command implementation touches frontend (avoid).

## Recommendation

Proceed in two passes:

- Pass A (safe now): implement command + tests + plan mode only; run against local/test DB.
- Pass B (after explicit permission): execute backup, then run apply against Azure target.

This keeps development moving immediately while honoring write and backup controls.

## Pass A: delivered (2026-10-11)

Files:

- `src/backend/InvenTree/assets/population_workbook.py` - Django-free parser; reads
  `Facility Locations`, `Machine Records`, `Machine Profiles`, `Component Occurrences`,
  `Machine Part Links`; validates codes, parents, cycles, depth, pump keys, one client,
  one station; records the workbook SHA-256.
- `src/backend/InvenTree/assets/population.py` - `plan_population` (read-only diff) and
  `apply_population` (atomic; `dry_run` rolls back). Writes only through
  `save_location`, `transfer_machines`, `register_station`, `ensure_pump`,
  `rename_station`, `validate_machine_profile`.
- `src/backend/InvenTree/assets/management/commands/populate_from_workbook.py` - CLI.
- `src/backend/InvenTree/assets/tests/test_population.py` - 25 tests.

Verified in an isolated sandbox (throwaway `pgvector/pgvector:pg16`, app container from
`aimms-merged-image` pointed only at it; nothing touched Azure):

| Check | Result |
|---|---|
| Real workbook parses | 45 locations, station, 14 pumps, 48 equipment, 63 profiles; 0 conflicts |
| Dry run | all writes executed and rolled back; 0 rows left |
| Apply | 45 locations, 63 machines, 63 placements in 38 transfers |
| Replay | every step `unchanged`/`exists`; 0 new transfers |
| QA-03/04 | one root `cc-site-hou`, `America/Chicago` |
| QA-06/07 | exactly P1..P14; names match the generated `station / Pump NN` |
| QA-09 | 63/63 placed, 0 without client |
| QA-11..13 | 0 invalid stored profiles; Pump 04 has 35 components |
| QA-59 | 238 components / 224 part links held back: 16 `CC-REF-*` parts missing |

Decisions made while implementing:

- Pump display names are **never** taken from the workbook. The registry generates
  them; a differing workbook name is a warning, not an error (QA-07).
- Station public UUID can be reserved via `--station-uuid` or an optional
  `public_uuid` column. The package reserves `d0b4aace-a6a7-555c-9fc6-e93089ba12c5`
  (Screen Fields tab). Pump UUIDs derive from it, so set it on the first apply.
- A machine row with no `Machine Profiles` entry still gets a minimal profile from
  its `criticality` / `maintenance_strategy` columns.
- Location codes are stored lower-case (the service does this); the parser mirrors it
  so plans compare like with like.
- `--apply` needs `--backup-ref`; a dotted DB host also needs `--allow-remote`.

### Runbook

Plan (read-only, safe anywhere):

```bash
python manage.py populate_from_workbook /path/CEDAR_CREEK_AIMMS_UI_POPULATION_MASTER_2026-10-08.xlsx \
  --client-name "Cedar Creek Regional Water Authority" \
  --station-uuid d0b4aace-a6a7-555c-9fc6-e93089ba12c5
```

Dry run (writes then rolls back):

```bash
python manage.py populate_from_workbook /path/MASTER.xlsx --dry-run --actor <username> \
  --client-name "Cedar Creek Regional Water Authority" \
  --station-uuid d0b4aace-a6a7-555c-9fc6-e93089ba12c5
```

Apply against Azure (Pass B; only after owner approval and a backup):

```bash
invoke backup                                  # or: docker compose run --rm inventree-server invoke backup
python manage.py populate_from_workbook /path/MASTER.xlsx --apply --actor <username> \
  --client-name "Cedar Creek Regional Water Authority" \
  --station-uuid d0b4aace-a6a7-555c-9fc6-e93089ba12c5 \
  --backup-ref <backup file or label> --allow-remote
```

The actor needs the `work_order` add/change role. If `AIMMS_MAINTENANCE_SCOPE_RESOLVER`
is configured, grant scope first: `manage.py grant_client_scope <username> cedar-creek-regional-water`.

### Next (Pass A2, still local)

1. Parts stage: create the `CC-REF-*` virtual reference Parts and categories from
   `Parts Catalogue` (Import Sequence step 5).
2. Component occurrences (`AssetComponent`) and `MachinePart` aggregation once parts exist
   (step 9), through the registry component path (QA-59).
3. Support equipment is already created in Pass A (step 10).
