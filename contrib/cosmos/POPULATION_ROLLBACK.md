# Undoing a workbook population run

How to back out `manage.py populate_from_workbook --apply`. Read this **before**
an apply, not after: the cheapest rollback is the one you prepared for.

Code: `src/backend/InvenTree/assets/population.py` (locations, station, pumps,
equipment, details, placements, parts, components, part links) and
`assets/population_operations.py` (warehouse, companies, manufacturer/supplier
parts, stock, history, work orders).

## 1. What a run changes

One apply is one database transaction. If it fails, nothing is written. Once it
commits, it has made the changes below. Most rows carry **no run marker**; the
workbook itself is the list of what the run touched, because every stage matches
rows by a natural key taken from the workbook.

| Stage | Model | Matched / identified by | Marker left by the run | Overwrites existing values? |
|---|---|---|---|---|
| Client | `assets.Client` | `code` | none | no (created only if absent) |
| Locations | `assets.AssetLocation` | client + `code` | none | **yes**: name, kind, parent, timezone, description, archived |
| Station | `AssetMachine` (pumphouse) | source identity, or `--station <pk>` | none | **yes**: an adopted station is renamed to the workbook name |
| Pumps | `AssetMachine` (pump) | station + `source_key` | none | **yes**: display name follows the station |
| Equipment | `AssetMachine` (equipment) | `name` | none | only fills a missing client |
| Details / profiles | `AssetMachine` | machine key | none | **yes**: description, model, serial, manufacturer, active, profile |
| Placements | `MachineLocationTransfer`, `MachinePlacementHistory` | — | transfer `reason` = `Workbook population <sha12>` | moves machines; history keeps the old placement |
| Categories / parts | `part.PartCategory`, `part.Part` | name + parent / `IPN` | none | **yes** for matched parts |
| Components | `assets.AssetComponent` | machine + `code` | `provenance` = `Workbook population <sha12>` | name, review_note |
| Part links | `assets.MachinePart` | machine + part | none | quantity, notes |
| Warehouse | `stock.StockLocation` | name + parent | none | **yes**: description, structural |
| Companies | `company.Company` | `name` | none | **yes** |
| Manufacturer / supplier parts | `ManufacturerPart`, `SupplierPart` | part + maker + MPN / part + supplier + SKU | none | no |
| Stock | `stock.StockItem` | part + bin + batch | note and tracking entry = `Workbook population <sha12>` | no (quantities never re-synced) |
| History | `assets.AssetMaintenanceRecord` | machine + date + summary | none | details, performed_by |
| Work orders | `tasks.WorkOrder` | `job_number` = workbook key | `tags` contains `workbook-population` | no; planned via the lifecycle service (`reason` = run label) |

`<sha12>` is the first 12 characters of the workbook's SHA-256, printed in the
command's report as `workbook_sha256`.

**What only a backup holds.** When a run overwrites a value (the "yes" rows
above), the old value is not kept anywhere except the pre-apply backup. That
includes an adopted station's previous name and any profile the station or pumps
had before.

## 2. Why rows mostly cannot be deleted

These are by design, not gaps:

- `MachinePlacementHistory` protects both its machine and its location. Once the
  run has placed a machine, **neither that machine nor that location can be
  deleted.** Placement is an audit trail.
- `AssetComponent` protects its machine and its part.
- InvenTree refuses to delete a `Part` that is active or locked. A part with
  stock, components or part links is also referenced.
- Work orders move only along the lifecycle; `draft` and `planned` can go to
  `canceled` (`tasks/services/work_orders.py`, `LEGAL_TRANSITIONS`).

So a selective undo **retires** rows rather than deleting them. Only a restore
makes the database as if the run never happened.

## 3. Option A: full restore (exact, all-or-nothing)

Use when the run was wrong as a whole and nothing else of value was written
since the backup.

1. Find the backup named by `--backup-ref`. For the Cedar Creek Azure run this is
   `data/backup/InvenTree-db-2026-10-11-155535.psql.bin.gz`; check its SHA-256
   against `HANDOVER.md` section 5a.
2. Find out what else was written after it. The restore discards **every**
   change since the backup, by anyone: other users, the poller (if enabled),
   other runs. On a shared database this needs the owner's sign-off.
3. Stop the web app and workers that use the database.
4. `manage.py dbrestore --noinput --uncompress -i <file>` (or `invoke restore`;
   see `docs/docs/start/backup.md`).
5. `manage.py migrate` (a no-op if the code did not change since the backup), then
   start the app and check the station, pumps and locations look as before.

## 4. Option B: selective retirement (keeps everything else)

Use when only this run's content should go away and the database has other
work since. Read the same workbook (`--plan --json` lists every key) and act on
those keys only. Do it in this order, inside one transaction, as a user with the
needed roles:

1. **Work orders**: for each workbook `work_order_key`, find
   `WorkOrder(job_number=key, tags__contains=['workbook-population'])` and
   transition it to `canceled` through `transition_work_order` with a reason.
   Skip any that has moved past `planned`: someone is working on it.
2. **Stock items**: delete the `StockItem`s whose note carries the run label,
   or adjust them to zero with a stock-take if their history should stay.
3. **Supplier / manufacturer parts**: delete those matching the workbook
   offers (no stock references them after step 2).
4. **Part links and components**: delete `MachinePart` rows for the workbook
   pairs, and `AssetComponent` rows whose `provenance` is the run label.
5. **Parts and categories**: set the workbook's `CC-REF-*` parts inactive. Delete
   them only after that if nothing else references them; delete categories
   last, and only if empty.
6. **Maintenance history**: delete the `AssetMaintenanceRecord` rows matching the
   workbook (machine, date, summary). They are never linked to a work order
   (QA-26), so nothing depends on them.
7. **Equipment and locations**: set the run's equipment `active=False` and
   archive the run's locations (`archived=True`, through `save_location` so the
   version check holds). Moving machines elsewhere first needs a normal transfer.
8. **Companies and warehouse**: set companies inactive; delete empty warehouse
   locations bottom-up.
9. **Station and pumps**: an adopted station keeps its identity regardless. To
   put back its old name, use `rename_station` with the name from the backup
   (restore it to a scratch database to read it). A station the run registered
   itself can be retired only by setting it inactive; its pumps follow.

Option B is not automated. Write it as a script, run it with `--dry-run`
semantics first (wrap it in a transaction and roll back), and check the counts
against the run's report before committing.

## 5. Before the next apply

- Always take the backup the command asks for, and record its SHA-256.
- Keep the run's report: `--json > apply_<date>.json`. It lists every created and
  updated count and the run label. The Cedar Creek Azure report is
  `data/devscratch/cedar-creek/azure_apply.json` (local, not committed).
- Save `--plan --json` output **before** applying. It is the only record of
  which existing rows the run is about to overwrite, apart from the backup.
- Prefer the sandbox (`contrib/cosmos/devtools/sandbox.sh`) for any rehearsal,
  including a rehearsal of the rollback.
