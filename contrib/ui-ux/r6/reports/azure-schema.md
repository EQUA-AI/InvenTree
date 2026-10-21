# Azure schema ahead of the image: pending set, audit, fix and rehearsal

**Status: safe for the running old app, with one short window during the
migrate run (below).** Rehearsed on a disposable copy of today's Azure backup;
Azure itself was not contacted. Changes are uncommitted on `r6/azure-schema`
(base `a858aadec`) for coordinator review. Agent review, not a human sign-off.

## What the owner asked for

Bring the schema of Azure Postgres `inventree` (epconchat-pg-dev, PG 15.19) up
to this line now, while Container App `aimms-dev` keeps running its old image
until another team deploys the new one. So every pending migration must leave
the old code able to read **and write** everything it writes today.

"Old code" below is `bb732dd1d`, the last commit before the first pending
migration: its migration leaves are exactly Azure's (tasks 0030, assets 0022,
repair 0010, aichat 0053, users 0024).

## Pending set

Azure (the 2026-10-21 dump): 967 rows in `django_migrations`, last
`assets.0022_var_unit` at 2026-10-13 06:56 UTC. `manage.py showmigrations
--plan` of this code against the restored copy, before the fix: 435 applied
nodes (squashes collapse the 967 rows), **15 pending**, in plan order:

| # | Migration | Origin |
|---|---|---|
| 1 | aichat 0054_investigation_proposal_action | 16c6cc511..1610c07c3 |
| 2 | tasks 0031_investigation_requests | 16c6cc511..1610c07c3 |
| 3 | tasks 0032_closeoutcapture_voice_unavailable_reason | 16c6cc511..1610c07c3 |
| 4 | assets 0023_registry_recorded_facts | R6 |
| 5 | tasks 0033_operational_authority | R6 |
| 6 | repair 0011_repair_finding_revisions | 16c6cc511..1610c07c3 |
| 7 | repair 0012_operational_authority | R6 |
| 8 | tasks 0037_calibration_traceability | R6 |
| 9 | tasks 0036_capture_originals_accepted_reports | R6 |
| 10 | tasks 0035_process_decisions | R6 |
| 11 | tasks 0034_work_time_downtime | R6 |
| 12 | tasks 0039_merge_r6_parallel | R6 |
| 13 | tasks 0038_routine_corrective_origin | R6 |
| 14 | tasks 0040_merge_r6_o21 | R6 |
| 15 | users 0025_operating_authority_role | R6 |

`git diff --diff-filter=A 16c6cc511 1610c07c3 -- '*/migrations/*.py'` also
lists `assets 0022_var_unit`; Azure already has it. With the fix the set is
**17**: the 15 plus `tasks 0041_db_defaults_for_old_code` and
`users 0026_ruleset_operating_authority_db_default`.

## Audit

Every operation of the 15, checked against the SQL each one actually runs on
the copy (collected with the migration executor in `collect_sql` mode,
`sql-original.txt`), not only the Python.

| Migration | Operation | Old-code safe? | Why |
|---|---|---|---|
| aichat 0054 | AlterField `chatactionproposal.action_type` (choices) | Yes | SQL is `(no-op)`; choices are not a database constraint. |
| tasks 0031 | CreateModel InvestigationRequest, InvestigationArtifact (+ index, check constraints, FKs to existing tables) | Yes | New tables; FKs are on the new tables only. |
| tasks 0032 | AddField `closeoutcapture.voice_unavailable_reason` CharField(default='') | **No → fixed** | `ADD COLUMN ... DEFAULT '' NOT NULL` then `DROP DEFAULT`: old-code INSERTs into `tasks_closeoutcapture` fail. |
| assets 0023 | CreateModel RegistryCommand, SignalDesignation, ComponentFit, SharedPerformanceView | Yes | New tables. |
| tasks 0033 | CreateModel OperationalAuthorityRecord (+ custom permission) | Yes | New table; permission row added by post_migrate. |
| repair 0011 | CreateModel RepairFindingRevision | Yes | New table. |
| repair 0012 | CreateModel GateAuthorityRecord | Yes | New table. |
| tasks 0037 | CreateModel CalibrationTraceability | Yes | New table. |
| tasks 0036 | CreateModel CaptureOriginal, AcceptedCloseoutReport | Yes | New tables. |
| tasks 0035 | CreateModel WorkHandover | Yes | New table. |
| tasks 0035 | AddField `workorder.recurrence_of` FK null | Yes | Nullable; plus an index. |
| tasks 0035 | AddField `workorder.recurrence_occurrence` OneToOne null | Yes | Nullable; UNIQUE allows any number of NULLs. |
| tasks 0035 | AddField `investigationrequest.escalation_group/escalated_by/escalated_at` null | Yes | Nullable, and the table is new on Azure. |
| tasks 0035 | AddField `investigationrequest.escalation_reason` TextField(blank) | Yes on Azure → hardened | NOT NULL, default dropped, but the table is created by 0031 in the same run, so the old code never writes it. Given a database default anyway for any environment that ran 0031-0034 with code older than 0035. |
| tasks 0035 | RunPython `expert_group` | Yes | `get_or_create` of auth group "Reliability engineers"; grants nothing. Old code sees one more group without rulesets, which it already handles. |
| tasks 0034 | CreateModel WorkTimeInterval, WorkDowntime | Yes | New tables. |
| tasks 0039, 0040 | Merge, no operations | Yes | |
| tasks 0038 | AddField `workorder.corrective_origin_kind` CharField(default='') | **No → fixed** | NOT NULL, default dropped: every old-code Work order create fails. |
| tasks 0038 | AddField `workorder.corrective_finding` TextField(blank) | **No → fixed** | Same. |
| tasks 0038 | AddField `workorder.corrective_reading`, `corrective_step` FK null | Yes | Nullable; plus indexes. |
| tasks 0038 | RunPython `mark_recurrences` | Yes | Updates rows with `recurrence_of` set; the column is new, so 0 rows on Azure. |
| users 0025 | AddField `ruleset.can_record_operating_authority` BooleanField(default=False) | **No → fixed** | NOT NULL, default dropped: old code creating a group (or its rulesets) fails. |
| users 0025 | RunPython `preserve_operating_grants` | Yes | Looks for groups holding `tasks.record_operating_authority`; that permission does not exist until post_migrate, so 0 rows. `users_ruleset` count unchanged (91). |

No RemoveField, RenameField, RenameModel, DeleteModel, AlterField on an
existing column's type or nullability, or constraint on an existing column
other than the UNIQUE on new nullable `recurrence_occurrence_id`. No
pre-existing column changed nullability or default (checked from
`information_schema` before and after).

Old code uses the same ruleset names as this line (`users/ruleset.py` only
gains the new permission mapping and ignore-list entries), so the one new
RuleSet column is the only users-side risk.

## Fix

Expand only; no written migration edited; no row changed.

- Model fields get `db_default` equal to their Python default, keeping
  `default=`: `WorkOrder.corrective_origin_kind` and `corrective_finding`
  (`src/backend/tasks/models.py`), `CloseoutCapture.voice_unavailable_reason`
  (`tasks/closeout_models.py`), `InvestigationRequest.escalation_reason`
  (`tasks/investigation_models.py`), `RuleSet.can_record_operating_authority`
  (`InvenTree/users/models.py`). `corrective_finding` and `escalation_reason`
  had an implicit `''`; it is now explicit so new code's inserts behave exactly
  as before.
- `src/backend/tasks/migrations/0041_db_defaults_for_old_code.py` (after 0040):
  four AlterField, SQL `ALTER COLUMN ... SET DEFAULT ''`.
- `src/backend/InvenTree/users/migrations/0026_ruleset_operating_authority_db_default.py`
  (after 0025): `ALTER COLUMN "can_record_operating_authority" SET DEFAULT false`.
- `makemigrations --check --dry-run tasks users repair assets`: "No changes
  detected" for each, exit 0. A whole-project `makemigrations --check` exits 1
  on `aichat` (seven `id`/`supersedes` AlterFields on the memory models); the
  identical output comes from a `git archive` of `a858aadec`, so it predates
  this change and is not touched here.

### Residual: windows inside the migrate run

Django drops the default when each AddField commits and the fix restores it
when 0041/0026 commit, so for part of the run those columns have no default.
Measured on the copy (from `django_migrations.applied`):

| Column | No default from → to | Window |
|---|---|---|
| `tasks_closeoutcapture.voice_unavailable_reason` | 0032 → 0041 | 17.4 s |
| `tasks_workorder.corrective_origin_kind`, `corrective_finding` | 0038 → 0041 | 0.9 s |
| `users_ruleset.can_record_operating_authority` | 0025 → 0026 | 0.02 s |

An old-app closeout capture started in the first window, or a Work order
created in the second, fails once with a server error and succeeds on retry.
Azure is further away, so expect these to be longer. Closing them would mean
editing 0032/0038/0025, which this task does not do, and Django gives no way
to run 0041 straight after 0032. Run the migration at a quiet time. (Azure
holds 4 closeout captures in total, so the 17 s window is a small risk.)

## Rehearsal (copy of today's backup)

Evidence: `data/devscratch/r6-azure-schema/` (schema, counts and timings only;
no row contents).

- Dump `data/devscratch/azure-rehearsal/inventree-20261021.dump` (read-only
  bind mount, not copied). It was written by pg_dump **17.11** (archive 1.16),
  which PG15's `pg_restore` refuses ("unsupported version (1.16)"); restored
  with the local `postgres:17` client over a private network into a new
  `pgvector/pgvector:pg15` container `aimms-schema-rehearsal` (15.19, vector
  0.8.7), `--no-owner --no-acl`, 26 s. The only error was the PG17 client's
  `SET transaction_timeout`, which PG15 does not know.
- Before: 278 tables, 3,159 columns, 967 migrations. After: 293 tables, 3,357 columns, 984 migrations.
- Code: this checkout mounted read-only in `inventree-dev-image:latest`
  (Python 3.14.7, Django 5.2.17), config/secret key/media in tmpfs,
  `INVENTREE_PLUGINS_ENABLED=False` (Azure's `app.yaml` at `8fd765f77` does not
  set it, and the default is off), `INVENTREE_AUTO_UPDATE=False`.

| Check | Result |
|---|---|
| Lineage guard `report()` before migrate | **PASS**: "lineage ok: 17 migration(s) to apply (aichat 1, assets 1, repair 2, tasks 11, users 2), and every applied migration is one this code knows." exit 0 |
| `migrate --noinput` | **PASS**: 17 applied, exit 0. 39.1 s wall including container start; 19.7 s between the first and last `applied` stamps. Slowest: tasks 0035 8.3 s, assets 0023 1.9 s, tasks 0031 1.7 s. |
| New columns on pre-existing tables nullable or defaulted | **PASS**, all 8: `tasks_closeoutcapture.voice_unavailable_reason` NOT NULL `''`; `tasks_workorder.corrective_finding` NOT NULL `''`; `corrective_origin_kind` NOT NULL `''`; `corrective_reading_id`, `corrective_step_id`, `recurrence_of_id`, `recurrence_occurrence_id` nullable; `users_ruleset.can_record_operating_authority` NOT NULL `false`. No column removed; no pre-existing column's nullability or default changed. |
| Raw INSERTs listing only pre-existing columns (cloned from an existing row, one transaction, rolled back) | **PASS**: `tasks_workorder` (new columns came out `''`/NULL), `tasks_closeoutcapture` (`voice_unavailable_reason = ''`), `users_ruleset` for a new group (`false`). `tasks_investigationrequest` did not exist before, so not applicable. Counts afterwards unchanged. |
| Negative control: the same INSERTs with the defaults dropped again (rolled back) | Fail as expected: `null value in column "corrective_origin_kind"`, `"voice_unavailable_reason"`, `"can_record_operating_authority"` ... violates not-null constraint. So the fix is what makes the inserts pass. |
| Old code on the migrated copy (`git archive bb732dd1d`, its own ORM, one transaction, rolled back) | **PASS**: 0 pending for it; its lineage guard says "nothing to apply" (exit 0); `manage.py check` no issues; it inserted a Work order, a closeout capture, a group and 13 rulesets; new columns took the database defaults. Counts afterwards 1550 / 4 / 91 as before. |
| Row counts, pre-existing tables | **PASS**: all equal except the intended: `auth_group` 7 → 8 ("Reliability engineers", tasks 0035), `auth_permission` 1185 → 1246 and `django_content_type` 286 → 301 (post_migrate for 15 new models), `django_migrations` 967 → 984. Also `django_q_ormq` 0 → 10: `register_event` tasks queued by each Django process start in the rehearsal (setting-saved / plugins-loaded events), not by any migration. 15 new tables, all empty. |
| `showmigrations --plan` after | **PASS**: 452 applied, 0 pending |
| `manage.py check` after | **PASS**: no issues |
| Lineage guard after | **PASS**: "nothing to apply" |
| Teardown | Container, volume `aimms-schema-rehearsal-data` and network `aimms-schema-rehearsal-net` removed; the generated rehearsal password deleted. |

On Azure the migrate container will not have the app's secret key, so the
`register_event` tasks it queues are signed with a different key. The running
worker logs "Failed to push task to queue" for each and deletes it
(`django_q.pusher`, `BadSignature` → `broker.fail`). This is noise, not a
failure.

## Dump growth (21 MB → 141 MB)

Restored both dumps side by side (database 126 MB → 294 MB). Same 278 tables;
the growth is AI attachment indexing:

| Table | Rows 10-20 → 10-21 | Size |
|---|---|---|
| `aichat_attachmentchunk` | 593 → 19,940 | 5.6 MB → 186 MB (158 MB TOAST; 1536-dim embeddings 117 MB, chunk text 13 MB) |
| `aichat_attachmentingest` | 15 → 125 | |
| `common_attachment` | 36 → 146 | |

So about 110 documents were uploaded and RAG-indexed in a day; the 120
distinct ingests hold 19,940 chunks. Other changes are small: users 14 → 26
(with profiles, owners, group links), `common_inventreeusersetting` 329 → 869,
10 more parts and 6 categories, 90 more risk-scan runs and 45 risk-finding
events. Notifications, error reports and `django_q_task` went **down** (2,866
→ 2,147, 2,862 → 2,143, 3,827 → 3,106): routine pruning. The 10-20 dump was
also written by pg_dump 15.19 and the 10-21 one by pg_dump 17.11.

## Backend tests

**Not run yet.** The rule is one offline run at a time. Another worktree's run
(`o24-scope`, container `funny_cohen`, started 04:37 UTC) was still going when
this report was written. It had printed nothing since 04:44 UTC and sat at 100%
CPU in `aichat.tests.test_migrations.AttachmentRagMigrationTests`. Its twin run
was stopped by its owner at the same point. Nothing was run beside it.

Queued: once no `backend_baseline.py` container is running, a job starts

```bash
python3 contrib/ui-ux/r6/backend_baseline.py --output data/devscratch/r6-azure-schema/backend tasks users repair
```

with results in `data/devscratch/r6-azure-schema/backend/{backend.log,manifest.json}`.
The same three apps took about 7 minutes in the o24 run. Expect one error
that predates this change: `users.test_migrations` fails to import because the
dev image lacks `django_test_migrations`; the o24 run on the same image shows
the same error.

## Command to migrate Azure (for the coordinator; not run)

Run from this checkout once the change is reviewed, at a quiet time, from a
host the Azure firewall admits. It mounts the code read-only, keeps every
writable path in tmpfs, takes the password only from the env file, and stops
before `migrate` if the lineage guard refuses (`set -e`):

```bash
cd /Users/Aniketkumar/IdeaProjects/InvenTree-r6/azure-schema
docker run --rm --pull never \
  --mount type=bind,src="$PWD",dst=/home/inventree,readonly \
  --tmpfs /tmp:rw,size=1g \
  --env-file /Users/Aniketkumar/IdeaProjects/InvenTree/data/azure-db.env \
  -e INVENTREE_DB_ENGINE=postgresql \
  -e INVENTREE_DB_NAME=inventree \
  -e INVENTREE_DB_HOST=epconchat-pg-dev.postgres.database.azure.com \
  -e INVENTREE_DB_PORT=5432 \
  -e INVENTREE_DB_USER=pgadmin \
  -e INVENTREE_PLUGINS_ENABLED=False \
  -e INVENTREE_AUTO_UPDATE=False \
  -e INVENTREE_DEBUG=True \
  -e INVENTREE_SITE_URL=http://localhost \
  -e INVENTREE_DATA_DIR=/tmp/data \
  -e INVENTREE_CONFIG_FILE=/tmp/data/config.yaml \
  -e INVENTREE_SECRET_KEY_FILE=/tmp/data/secret_key.txt \
  -e INVENTREE_OIDC_PRIVATE_KEY_FILE=/tmp/data/oidc.pem \
  -e INVENTREE_PLUGIN_FILE=/tmp/data/plugins.txt \
  -e INVENTREE_PLUGIN_DIR=/tmp/data/plugins \
  -e INVENTREE_MEDIA_ROOT=/tmp/data/media \
  -e INVENTREE_STATIC_ROOT=/tmp/data/static \
  -e INVENTREE_BACKUP_DIR=/tmp/data/backup \
  -e PYTHONUSERBASE=/root/.local -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -w /home/inventree/src/backend/InvenTree \
  --entrypoint /bin/bash inventree-dev-image:latest \
  -c 'set -euo pipefail
      mkdir -p /tmp/data && echo "{}" > /tmp/data/config.yaml
      python manage.py shell -c "from assets.deploy_lineage import report; report()"
      python manage.py migrate --noinput
      python manage.py showmigrations --plan | { grep "\[ \]" || echo "nothing pending"; }'
```

Expected: the guard prints `lineage ok: 17 migration(s) to apply (aichat 1,
assets 1, repair 2, tasks 11, users 2)`; migrate applies the 17 in the order
above; the last line is `nothing pending`. If the guard prints `STOP`, nothing
has changed; do not run migrate. `data/azure-db.env` must supply
`INVENTREE_DB_PASSWORD`; it is referenced by path only. Take a fresh
`pg_dump` first. The old app needs no restart; if it restarts, it starts:
nothing is pending for it, and its own guard reports "nothing to apply".

## Files

- `src/backend/tasks/models.py`, `src/backend/tasks/closeout_models.py`,
  `src/backend/tasks/investigation_models.py`,
  `src/backend/InvenTree/users/models.py`: `db_default` on five fields.
- `src/backend/tasks/migrations/0041_db_defaults_for_old_code.py`,
  `src/backend/InvenTree/users/migrations/0026_ruleset_operating_authority_db_default.py`: new.
- This report.

Formatted with `prek run --files` on the six Python files: all hooks pass.
