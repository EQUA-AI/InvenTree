# Apply the IOT focus release to the recorded live database

This release includes a reconstructed **schema checkpoint** for the complete
17-record migration batch applied on 2026-10-21. The original migration source
was not recovered. The checkpoint captures the observed compatibility boundary;
it does not claim to recover the original dependencies, Python data operations
or authority-grant logic. Existing records and grants are retained as they are.

The checkpoint is stored in `legacy_boundary/snapshot.sql`, `snapshot.json`,
and `check_renderings.json`. All three are required and protected by checksums in
`legacy_boundary/checkpoint.py`. They contain schema metadata, no customer rows,
credentials, permission grants or historical data backfills.

The renderings file contains four exact paired CHECK definitions observed live
and after restoration. They differ only in PostgreSQL's placement of
binary-compatible varchar-to-text array casts. Names, fields and allowed values
are fixed; changed values or other definitions still refuse.

## What is checked

Before any migration DDL, the guarded runner requires either:

- The complete recorded 17-name batch, with the exact archived PostgreSQL
  compatibility footprint: 15 tables, their columns/identity/defaults,
  constraints and indexes, eight added columns on active tables, and the
  persistent work-order tags default. Proposal storage is checked as well.
- A fresh database with none of that batch and none of those archived tables.

Partial batches, unexplained migration names, missing or altered structures,
unexpected triggers/row-security rules, and incomplete focus SQL defaults
refuse. New arbitrary names are never admitted by the checkpoint.

Seven genuine upstream ancestors are recognized separately, conditional on
their shipped enclosing squash and applied join. Six build names come from the
nested squash in Git blob `db2803f53fd060eed9f3d71689dd5e8f88335423`.
The part parallel branch `0061_auto_20210104_2331` is defined in blob
`c40b611b297621636a3e9126ffce737b97b66ddd`; its join with the other 0061 is
blob `4a8f4378f4c6a0ffc5c0caa4363d6221d318c65f`. Existing migration files
and their replacement lists are unchanged.

The migration-only app uses one replacement boundary for the entire batch.
This avoids inventing the lost per-app merge graph. On an existing matching
database, Django adopts the replacement and does not execute its bootstrap SQL
or replay historical data operations. On fresh PostgreSQL installations, the
checkpoint creates the same inert compatibility storage without seed records
or new authority grants. Runtime models and authorization behavior are unchanged.
The checkpoint also carries the replaced migrations' schema operations as model
state only, so `makemigrations --check` matches the models and fresh databases
get the content types and permissions those models declare. This adds nothing
to apply on a database that already has the checkpoint recorded.

The checkpoint bootstrap is irreversible because removing retained storage
would destroy historical records. Rolling back the application image leaves
this additive schema in place. Focus fields retain their existing reversible
migration behavior.

## Run against the live database

Use the complete tested release with its native database configuration and an
available database recovery point. Run this from the backend directory in the
release/container; the current working directory must not import the repository
root's Invoke `tasks.py` in place of the backend `tasks` package.

```bash
cd src/backend

# Read-only preflight. Any mismatch must be resolved before applying.
python InvenTree/manage.py runmigrations --check

# One runner; lineage, migration DDL and recorder updates commit together.
python InvenTree/manage.py runmigrations

# Read-only completion check: must report nothing to apply.
python InvenTree/manage.py runmigrations --check
```

For the verified restored live boundary, the forward plan is:

1. `aichat.0054_chatthread_conversation_focus`
2. `aichat.0055_chatthread_focus_db_defaults`
3. `legacy_boundary.0002_verify_focus_checkpoint`

Django also records `legacy_boundary.0001_observed_schema_checkpoint` as the
replacement marker. The 17 original records and their timestamps remain intact.
Do not fake migrations or manually insert/delete recorder rows.

The guarded runner requires PostgreSQL, obtains a per-database advisory
transaction lock, refuses a competing runner and non-atomic plans, and limits
lock waits to five seconds. Any failure rolls back the entire operation. A
repeat run checks the boundary and has no pending operations.

The command name is already recognized by InvenTree's native migration-mode
checks. It suppresses normal startup writes before the command handler runs;
the `--check` handler additionally uses a read-only PostgreSQL transaction.

Use this enclosing transaction for the live upgrade. Running ordinary
`manage.py migrate` exposes the intermediate 0054 schema, whose missing SQL
defaults break still-serving old thread writers before 0055 commits.

## Container startup

`AIMMS_MIGRATE_ON_START=true` invokes the same guarded runner before the web
server starts. Enable it on exactly one migration candidate; keep it disabled
on workers and other replicas. Remove the one-time flag after verification.
Migration code does not change traffic, update workers or deploy a revision.

Fresh installs use the normal migration procedure: older upstream squashes
include non-atomic operations and are intentionally refused by the atomic
startup runner. Fresh SQLite/MySQL installs build the same tables from the
replaced migrations' schema operations; adopting an existing legacy batch on
those engines is not certified by this checkpoint.

## Scope of the local evidence

The restored-database rehearsal compares every original table's row count and
SHA-256 content fingerprint (excluding only the two added focus columns), all
old migration timestamps, and Cedar records. It also checks repeat execution.
Separate PostgreSQL tests cover old writers, reverse/reapply, committed schema
visibility, rollback on injected failure, concurrent runners, partial history,
schema/default/index/constraint changes, unknown history and artifact tampering.

On 2026-10-21, the complete native app registry migrated successfully from an
empty PostgreSQL database. All 28 focused PostgreSQL tests passed without skips,
using disposable isolated copies with normal Django case setup/teardown and
cascade cleanup for retained retired tables. The restored upgrade preserved
all original table contents, historical migration timestamps and Cedar records.
The live catalog preflight at 14:16 UTC passed with TLS verification and
database-enforced read-only access: three pending migrations, no unknown names,
consistent history and a matching checkpoint. No live migrations were applied.
The actual native command also passed startup, read-only preflight, apply,
completion check and repeated apply on a restored copy with the same data and
ledger preservation checks.

These are migration validation results. Image build, current live preflight,
backup freshness, staging health and traffic promotion are separate deployment
steps. If the live boundary has changed since the snapshot, the preflight must
be run against that state; no bypass is provided.
