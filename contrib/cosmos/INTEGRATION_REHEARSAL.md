# Reviewed EQUA / IoT integration rehearsal

This is a local integration/rehearsal contract, **not deployment approval**.
The owner separately authorized accelerated local completion and a merge commit.
No cloud connection, role assignment, deployment database operation, push or
publication is authorized by this document. See [deployment checklist](DEPLOYMENT_CHECKLIST.md).

## Frozen candidate

- Target: `7bc90407ce30ac651d68fe2905b8c0e10459920e`
  (reviewed, committed EQUA demo metrics).
- Source: `c43f330f1ef54cce7d6d56f6aa9b1aada5724917`
  (the complete 45-commit reviewed IoT dependency chain).
- Integration branch: `integration/equa-iot-demo`; complete the authorized local
  merge only after verifying the exact final staged tree. Human review is still
  required before opening any pull request.
- Excluded: dirty target WIP and newer unreviewed `origin/IOT` commits.
- Keep all historical migrations already in the reviewed target and all four
  imported IoT assets migrations byte-identical, including `0011_equipment_registry`.
  This integration introduces no historical edits/deletions or old dependency
  changes. The target already contains upstream squashes in seven core apps;
  those pre-existing source/target differences are not introduced by this merge.
  Test an actual database created by the exact pinned IoT checkout, including
  its original core-app records, rather than infer full compatibility from an
  assets-only probe using the target's squashes. Never use `--fake` to hide
  inconsistent history.
- The additive assets join is `0019_merge_iot_registry_demometrics`, depending
  on `0014_station_activation` and
  `0018_alter_demometricsreceipt_operation_kind`.

## Non-demo consolidation

The accepted merge is preserved as history. The `equa/customizations`
consolidation removes synthetic runtime models, services, APIs, UI, providers,
loaders and their deployment harnesses. It keeps ordinary maintenance metrics,
physical locations and native IoT workflows. Migration
`assets.0020_retire_synthetic_ledger` changes model state only: existing synthetic
ledger tables, rows and foreign keys remain. Applied migrations, including IoT
`0011`, are not edited. Do not delete retained tables or content types as part of
this step. Foreign-key references can still prevent deletion of referenced
records; any archival or constraint retirement needs a separately reviewed change.

Production-branch creation, push, deployment and shared-database operations are
not authorized by local consolidation. Repeat fresh, exact-target and exact-IoT
upgrade rehearsals and model-state checks for the final consolidated candidate;
the historical acceptance below is not evidence for a later tree.

## Database verification

Use a dependency-equipped interpreter and explicit disposable database,
configuration, static/media/backup paths and disabled plugins/providers. Do not
inherit credentials or a live database from a normal development configuration.
Keep scheduler polling disabled unless the local emulator test explicitly
controls it. Local test databases/containers are distinct from deployment data.

From `src/backend/InvenTree`, using that isolated configuration:

```bash
python manage.py showmigrations assets --plan
python manage.py makemigrations assets --check --dry-run
python manage.py test InvenTree.test_migration_rewind users.test_iot_ruleset --noinput
python manage.py test assets.test_registry_migration assets.test_migrations --noinput
python manage.py test assets machine_health users part.test_pump_catalogue \
  --exclude-tag migration_test --noinput
```

Acceptance requires **all** of these; a plan or mocked executor is not proof:

1. Fresh SQLite and PostgreSQL schemas migrate to the merged leaves.
2. A database built by the exact pinned IoT checkout, as well as the isolated
   IoT-only assets-history probe, can upgrade without
   history inconsistency, regenerated public UUIDs, changed machine/client IDs,
   lost station/checkpoint ownership or altered accepted polling position.
3. A database at the complete pinned target-only assets history can upgrade
   without losing profile/placement data or changing existing identities.
4. Both upgrades retain all previously applied migration records, add the
   complete other branch and the join, and preserve the registry's conditional
   source-station/pump-slot uniqueness constraints.
5. The real SQLite rehearsal command passes preview, refusal, execution and
   reapply checks. Full historical backfill/profile/barcode tests restore all
   leaves before subsequent tests flush current-model tables.
6. The real Cosmos SDK emulator lane passes partition isolation, capped resume,
   two-hour history, ingestion and replay. It does not establish Azure RBAC or
   network acceptance.

Do not suppress a failing broad check. Compare a suspected baseline failure
against the exact pinned target. The target's unrelated `aichat` model drift and
permission-coverage omissions require separate target-line work; do not invent
migrations or broadly grant ignored-model access in this integration.
`get_ruleset_ignore()` is authorization-affecting, not a test skip list. The new
internal ingestion checkpoint belongs only to the administrative ruleset.

## Explicit disposable SQLite rewind

**Ordinary deep `migrate assets ...` rollback across the merged SQLite graph is
unsupported.** Django can rebuild a table through the retained sibling's wrong
historical state, losing registry columns/indexes. Do not repair this by rewriting
`0011`, faking state, or running destructive SQL on deployment data.

For a disposable SQLite copy only, preview the exact full migration name:

```bash
python manage.py rewind_migrations assets 0010_remove_assetmachine_customer
```

Read the entire JSON `plan`, `additional_rewinds` and warning. The command
rewinds the complete applied suffix of Django's canonical forward plan, including
later sibling and **other-app** migrations. Historical data operations may discard
data on reversal. A no-op preview remains read-only; an unapplied/abbreviated
migration name is rejected. The fingerprint binds the database alias/name,
target and ordered plan. A changed database/plan needs a fresh preview.

Only after reviewing that exact plan and confirming the configured database is
disposable:

```bash
python manage.py rewind_migrations assets 0010_remove_assetmachine_customer \
  --apply --disposable-database --expected-plan-sha256 <preview-plan-sha256>
python manage.py migrate --noinput
```

Do not run parallel migration executors, mutate migration history between preview
and execution, or use this command on a shared database. Execution refuses
PostgreSQL. **Deployment recovery is forward-fix only**: switching an application
image does not undo schema/data changes; a separately approved restore requires a
verified backup and its own rehearsal.

## External integration release gates

Before any deployment or external connectivity, the deployment/client owners must
record approval of:

- The exact tested candidate/tree and release image; human review before any PR.
- One named migration executor, `equa-iot-migrate`, with an assigned operator,
  backup, successful restore rehearsal and maintenance-window criteria. This is
  the required executor name, not a job created by local integration work.
- `INVENTREE_AUTO_UPDATE=False` on web instances; quiescent web/workers while the
  named executor runs; only resume them after schema/readiness checks pass.
- The exact non-production account, database, container, application identity,
  identity's effective data-plane permissions and allowed network path. A
  management-plane role or the existence of a read-only assignment alone does
  not prove the identity lacks other write roles. No write probe against plant data.
- Approved telemetry provenance, sensitivity and account/container ownership.
  A non-production account alone does not authorize external connectivity or
  establish the sensitivity of its contents. Do not substitute another account.
- Client/source/station ownership, reviewed dictionary and exact catalogue/unit
  mappings, config/dictionary owners and confirmed thresholds. Unavailable,
  stale, disabled and unreviewed readings must not become zero/healthy values.

The `INVENTREE_COSMOS_*` environment coordinates are for utility/dev tools.
The live connector reads its Client-owned `HealthSource.config` row. Use
`DefaultAzureCredential` with approved read-only data-plane access; account-key
references are accepted only for a local emulator. Operator APIs must not expose
endpoints, credential references or source configuration.

Station activation does not enable the global scheduler. Leave
`AIMMS_COSMOS_PUMPHOUSE_ENABLED=False` until approved activation and access checks
complete, then coordinate the approved setting on server and worker. Local
mock/browser tests cannot close external access, data sensitivity, complete
plant coverage or geometry/threshold review gates.

## Evidence and handoff

Record commands, actual exit codes, database/backend/history boundaries, exact
suite counts/skips, review findings, baseline failures and final tree fingerprint.
Preserve failed harness attempts separately from corrected runs; do not call a
partially run or killed suite passed. Verify staged blobs equal the tested files.
Stop/remove only the named disposable resources after checks finish, and preserve
the original dirty checkout and its local handover. No commit, push or deployment
is implicit in this checklist. The local merge commit is separately authorized.
