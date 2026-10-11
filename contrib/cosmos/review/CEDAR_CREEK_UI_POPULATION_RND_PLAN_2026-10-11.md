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

Apply against Azure (Pass B; only after owner approval and a backup). *Superseded: the
Azure run used `--client-code internal --station 93` instead of `--client-name` /
`--station-uuid`, because the station already existed; see "Pass B" below.*

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

## Pass A2: delivered (2026-10-11)

Steps 5 and 9 run when the workbook carries `Parts Catalogue` (commit `cdf14e8eb`).
The remaining sheets with a model behind them were added afterwards
(`assets/population_operations.py`):

| Import Sequence step | Sheet | Target | Rule |
|---|---|---|---|
| 4 Stock locations | Warehouse Locations | `StockLocation` | codes may not shadow facility codes (QA-05); matched by (parent, name) |
| 12 Companies / offers / stock | Companies | `Company` | identity only; matched by name |
| | Supplier Offers | `ManufacturerPart` | `SupplierPart` only with supplier **and** SKU (QA-23); otherwise reported |
| | Stock Items | `StockItem` | never on a virtual part or a structural bin (QA-18, QA-22); illustrative cost goes to a note, not `purchase_price` (QA-58); quantities not re-synced on replay |
| 13 History | Maintenance History | `AssetMaintenanceRecord` | a `work_order_reference` is rejected (QA-26) |
| 14 Work orders | Work Orders | `tasks.WorkOrder` | created `draft`, planned via `transition_work_order`; `job_number` = `work_order_key`; existing jobs are never overwritten (QA-24) |

Reported as `manual`, not written: Staff Roles (step 2), PM Plans (step 13; procedures
are customer-scoped governed content), Attachment Plan (step 15; no files in the
package), repair packets (step 14; raised from the staged jobs in the UI), Closeout Copy
(QA-29), Tag Hints (step 11, blocked by the package), compliance UI (step 16, a feature).

Two operator flags bind the workbook to a plant that already exists from its source:
`--client-code` (populate an existing client) and `--station <id>` (adopt a registered
station; identity immutable, name and pump labels follow the workbook).

## Pass B: Azure (2026-10-11)

Plan against Azure showed the workbook would have created a second client and a second
Cedar Creek station beside the live one (id 93, client `internal`, `klsw` identity), and
the deployed app runs `single_site_scope_resolver` on `internal`, so nothing under a new
client would be visible. Applied instead with:

```bash
manage.py dbbackup --noinput --compress     # data/backup/InvenTree-db-2026-10-11-155535.psql.bin.gz
manage.py populate_from_workbook MASTER.xlsx --apply --actor admin \
  --client-code internal --station 93 \
  --backup-ref InvenTree-db-2026-10-11-155535.psql.bin.gz --allow-remote
```

No `--station-uuid`: the public UUID of station 93 is `ce0a411f-…` and immutable; the
package's reserved `d0b4aace-…` applies only to a fresh registration. Pre-flight
(read-only) confirmed `USD` enabled, stock-item notes allowed, the actor's scope and
`plan_workorder` permission, and no name collisions for the 30 companies.

### Pass B result

`APPLY_EXIT=0` after about 65 minutes in one transaction (the link is ~0.25 s per
query and every save fires the plugin/event hooks). Counts from the command's JSON:

| Stage | Result |
|---|---|
| Client | `internal` (pk 1) existed |
| Locations | 45 created |
| Station | 93 adopted; renamed `Cedar Creek Final Effluent Pump Station`; `klsw`/`PH_3` identity and UUID `ce0a411f-…` kept |
| Pumps | 14 existing (labels follow the station name) |
| Equipment | 48 created |
| Profiles / placements | 63 updated / 63 moved (38 transfers) |
| Part categories / parts | 4 / 304 created |
| Components / part links | 238 (17 per pump, beside the 16 `PS-*`) / 224 created |
| Warehouse locations | 8 created |
| Companies / manufacturer parts | 30 / 6 created; 6 supplier offers blocked (no SKU) |
| Stock items | 6 created |
| History | 132 created |
| Work orders | 18 created, 8 planned |

QA gates (step 17): the read-only checker is `contrib/cosmos/devtools/cedar_creek_qa_gates.py`.
Against the sandbox population it reports 22 pass / 0 fail. The Azure run is
**outstanding**: shortly after the apply committed, the server began rejecting the
`pgadmin` password held by the local container (`password authentication failed`,
from ~21:10 local). The password is static, so it was changed server-side; the owner
has to supply the current one before the gates can be run there. See `HANDOVER.md`
section 5a and open item 11.
