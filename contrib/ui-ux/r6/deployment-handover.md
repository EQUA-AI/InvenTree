# AIMMS R6 deployment hand-over: aimms-dev

Prepared 21 October 2026 by the coordinator agent for the owner (O25). Updated for
the owner's same-day plan: the coordinator migrates Azure Postgres before the app
is deployed; the deployer verifies the result and deploys the image. Every fact
comes from the repository, the rehearsal reports or the owner's notes; anything not established is marked
**UNCONFIRMED**. No secret value appears here. Secrets are named by variable and
location only.

Revised the same day after a review against `a858aadec`: each correction was
checked against its cited source first (see "Reviewer notes not applied" at the end).

Audience: an engineer with write access to the `aimms-dev` Container App and push
access to the `aimms` container registry, who has not worked on this project.

## Database status at hand-over

The coordinator completes this table after migrating database `inventree` on
Azure Postgres server `epconchat-pg-dev`, before handing over for deployment.
The deployer does not run migrations. Verify the completed record against the
final image as described in sections 3.3 and 7 before rolling out.

| Record | Value |
|---|---|
| Migrated at (UTC) | TO FILL |
| Applied count after | TO FILL |
| showmigrations result (nothing pending) | TO FILL |
| Backup file name | TO FILL |

A read-only `pg_dump -Fc` backup of Azure was taken on 21 October 2026 before
the migration and is held by the owner locally. Record only its file name above;
do not include its path or contents.

---

## 1. Purpose and scope

**What is being deployed.** The integrated AIMMS R6 line of InvenTree, branch
`inventTree-aniket`, onto the existing Container App `aimms-dev` and its worker.
That means:

- a new production image built from `contrib/container/Dockerfile`, target `production`;
- an already migrated, expand-only database schema prepared by the coordinator (section 7);
- the R6 configuration flags (section 5);
- a size of 1 vCPU / 2 GiB per replica.

**Which commit.** The commit is not final yet. At the time of writing the branch
tip is `a858aadec27e6a80d3f97308d7b3415bd29a253b`. Two things still have to happen
before the build:

1. Owner decision O24 (below) is being implemented on branch `r6/o24-scope` and
   will be merged first.
2. The owner merges the finished `inventTree-aniket` into `IOT-Merge` personally (O25).

**Which SHA you build, and who pushes it to GitHub, is UNCONFIRMED.** It could be
the `IOT-Merge` merge commit or the `inventTree-aniket` tip. Get the final 40-character
SHA from the owner in writing. The build pulls the source from GitHub (section 4).
Right now HEAD is on no remote branch: `origin/inventTree-aniket` and
`origin/IOT-Merge` are both at `fe0d73a4a`, and HEAD is 80 commits ahead.

**Target.**

| Item | Value |
|---|---|
| Subscription | `5b75a75a-fff3-4d72-a3e9-5e16cb6a8687` |
| Resource group | `EpconChat` |
| Container registry | `aimms` (login server `aimms-hjcxb6epgvhgbyge.azurecr.io`) |
| Image repository | `aimms-dev` |
| Web app | `aimms-dev` |
| Worker app | `aimms-dev-worker`, named in `contrib/aimms_production_env.md` and in the August ledger `LocalDocs/RagRun04Rollout.md` (last recorded at 2.0 vCPU / 4 GiB). **UNCONFIRMED** for today: confirm in step 0 that this is the app running `invoke worker` against the same database. |
| Container Apps environment | `epcon-ai-env` |
| Database | Azure Postgres Flexible Server `epconchat-pg-dev.postgres.database.azure.com`, database `inventree` (PG 15.19), user `pgadmin` |
| Cosmos account | `epconchatcosmos9d6b`, database `aimms`, container `pumphouse_readings` |

**What is running now (UNCONFIRMED).** No tracked file records the running
commit. The git-ignored rehearsal reports under `data/devscratch/azure-rehearsal/`
and the owner's notes say aimms-dev runs the older `equa/customizations` line at
`b4f1504a1` (a 2 October 2026 commit). The last recorded topology is the "Final
topology" table of the August ledger `LocalDocs/RagRun04Rollout.md` (tracked):

| App | Revision | Image digest | Size |
|---|---|---|---|
| `aimms-dev` | `aimms-dev--r43417c2` (100%) | `sha256:4fb11186…` | 1.0 vCPU / 2 GiB |
| `aimms-dev-worker` | `aimms-dev-worker--r43417c2` | same digest | 2.0 vCPU / 4 GiB |

The ledger's `aimms-dev:equa-customizations-449fb3bfb` row at 0.5 vCPU / 1 GiB is
the state *before* that rollout. In the August soak the dev worker peaked at
1,944 MiB. `contrib/aimms_production_env.md` later names the dev pair
`aimms-dev--0000091` / `aimms-dev-worker--0000021` (5 September), without sizes.
Both are history. The owner cannot read the app, so step 0 (section 3) must record
what is actually there.

**Owner decisions that apply** (full text in `contrib/ui-ux/r6/owner-approval.md`):

| Decision | What it means for you |
|---|---|
| O15 A2 | Deploy `inventTree-aniket` to aimms-dev **only with the owner's go-ahead at the time**. Under the 21 October plan, the coordinator handles the pre-migration backup and migration before hand-over; the deployer verifies the database. |
| O15 A3 | Only the showcase station goes live. |
| O18 | aimms-dev runs at **1 vCPU / 2 GiB** per replica during the rollout and after it. Do not drop it to 1 GiB. The production-image rehearsal showed 512 MiB / 0.25 CPU OOM-killed before readiness, 1 GiB OOM-killed on a work-order read, and 2 GiB passing every read with a peak of about 1.0 GiB (four gunicorn workers). |
| O19 | The owner's account `Aniket@equa.work` cannot read or update `aimms-dev` or push to `aimms`, so the rollout is handed to you. |
| O23 | Showcase is WO-003826 on Cedar Creek Pump 08 (machine 102). Cedar Creek's live feed is **not** restarted; historical readings (ending 1 August 2025) are used and must be labelled historical. Receiving the real spare is an Azure write after deployment, only with the owner's go-ahead. |
| O24 | Work orders with an explicit customer, and those with neither customer nor machine, become visible to every user with the Work order view role. Role permissions still gate every read and action. Machine, health and telemetry reads keep client scope. Code-only change, no migration. |
| O25 | The owner merges to `IOT-Merge`. No agent push or pull request. The 21 October plan assigns the Azure schema migration to the coordinator before deployment. This document is the hand-over. |

Standing rule from the owner: **ask before any write to Azure Postgres**, including
starting an app against it. Reads are fine.

---

## 2. Access and prerequisites

**Azure rights you need** (from `.azure/deployment-plan.md` 13.1 and 13.5):

| Right | Scope | Why |
|---|---|---|
| Container Registry Tasks Contributor | registry `aimms` | To queue `az acr build`. AcrPush alone cannot queue a Task. |
| AcrPull | registry `aimms` | To read the image digest. |
| Container Apps Contributor | `aimms-dev` and the worker app | Update, logs, exec. |
| Owner, Contributor or DocumentDB Account Contributor | Cosmos account `epconchatcosmos9d6b` | For the step-5 Cosmos data-role assignment. Cosmos DB Operator cannot do it. |
| Read, and for 9.4 write, on the managed environment (e.g. Container Apps Contributor or Contributor; role name is general Azure knowledge, **UNCONFIRMED**) | Container Apps environment `epcon-ai-env` | Step 0's `az containerapp env storage show` and 9.4's `env storage set` act on the environment, not on the apps. Plan 13.1 scopes rights to the apps only. |
| Owner or Contributor | Resource group `EpconChat` | Only if 9.4 needs a new storage account (`contrib/cosmos/reference_library/README.md` step 1). Reading the account key for `env storage set` also needs key-list rights on that account. |

The owner's account has Reader and Contributor on the two Postgres servers and
Cosmos DB Operator, so the owner can do the read-only database checks and the
logical backup in section 3 from his machine.

**Tools.** Azure CLI with the `containerapp` extension; for the read-only checks,
Docker with the `postgres:17` image (owner's machine).

**What does not travel with a git clone.**

- `data/` is git-ignored. The rehearsal reports
  `data/devscratch/azure-rehearsal/REPORT.md` and `REPORT-prod.md` are on the
  owner's machine only. Their key results are summarised in this document.
- Database dumps hold real data and stay with the owner. The read-only
  `pg_dump -Fc` backup taken on 21 October before migration is held by the
  owner locally; its file name belongs in the hand-over table, without its path
  or contents.
- `data/azure-db.env` holds the database password as `INVENTREE_DB_PASSWORD`. Owner's machine only; never print or copy it.
- `.azure/deployment-plan.md` is git-tracked (despite its own line 124 saying it is
  ignored). Sections 13 and 14 are the base procedure. This document corrects the
  parts that are stale for this release.

**Missing script.** `contrib/aimms_production_env.md` names
`LocalDocs/scripts/aimms_deploy.sh` as "the only sanctioned deploy path". It exists
in no git ref and not in `LocalDocs/`. **UNCONFIRMED** whether it exists anywhere or
applies to aimms-dev. This document uses the plan's `az` commands instead.

**Approvals checklist before you touch Azure.**

- [ ] Owner's written go-ahead for this deployment, at the time (O15 A2, O18).
- [ ] Final SHA agreed and present on `https://github.com/EQUA-AI/InvenTree.git`.
- [ ] O24 and the expand-only default migrations included in that SHA; reconcile its migrations with the coordinator's record (section 7).
- [ ] Coordinator's database hand-over table completed; final-image `showmigrations` and lineage checks show nothing pending or unknown (sections 3.3 and 7).
- [ ] Deployer's own fresh, validated pre-deploy backup recommended (section 3.5).
- [ ] Decision on the pumphouse poll and section 14 (section 6, step 6).
- [ ] Go-ahead for the smoke-test writes in sections 8.3 and 8.4 (test users, tokens, sign-ins).
- [ ] Decision on rotating the database password that sits in git history (section 5.4).

---

## 3. Before you start: backup and read-only pre-checks

### 3.1 Set the variables

```
az account set --subscription 5b75a75a-fff3-4d72-a3e9-5e16cb6a8687
RG=EpconChat
ACR=aimms
LOGIN=aimms-hjcxb6epgvhgbyge.azurecr.io
APP=aimms-dev
REPO=aimms-dev
```

### 3.2 Step 0: record what is running (read only)

From `.azure/deployment-plan.md` 13.2 step 0, with `traffic` added and `PREVIOUS_REV` taken from the traffic list:

```
az containerapp show -n "$APP" -g "$RG" -o json --query "{image: properties.template.containers[0].image, revision: properties.latestReadyRevisionName, mode: properties.configuration.activeRevisionsMode, traffic: properties.configuration.ingress.traffic, resources: properties.template.containers[0].resources, scale: properties.template.scale, probes: properties.template.containers[0].probes}"
PREVIOUS_REV=$(az containerapp show -n "$APP" -g "$RG" --query "properties.configuration.ingress.traffic[?weight==\`100\`].revisionName | [0]" -o tsv)
echo "$PREVIOUS_REV"
```

`PREVIOUS_REV` must be the revision that **carries the traffic**, not
`latestReadyRevisionName`: in multi-revision mode the latest ready revision can be
a 0% one. If the 100% entry has `latestRevision: true` instead of a name, or the
traffic is split, stop and record the full `traffic` list; the traffic handling in
section 6 then needs adjusting (**UNCONFIRMED** case, not seen in any source).

Then run the plan's `az containerapp list … db_host … auto_update … min_replicas`
query (deployment-plan.md, step 0) to find the worker: the app running
`invoke worker` on the same database. Set `WORKER=<that app name>` and record
`WORKER_PREVIOUS_REV` (the worker has no ingress, so use its
`latestReadyRevisionName`), its revision mode, its CPU and memory, and its list of
active revisions (`az containerapp revision list -n "$WORKER" -g "$RG" -o table`).

Also record these, which the plan's query does not print. Read env **names** in
bulk; env values can be plaintext secrets, so read values only through the filtered
query below. (These queries are composed for this hand-over,
not copied from the plan.)

```
az containerapp show -n "$APP" -g "$RG" --query "{env: properties.template.containers[0].env[].name, volumes: properties.template.volumes, mounts: properties.template.containers[0].volumeMounts}"
az containerapp show -n "$WORKER" -g "$RG" --query "{env: properties.template.containers[0].env[].name, volumes: properties.template.volumes, mounts: properties.template.containers[0].volumeMounts}"
az containerapp env storage show -n epcon-ai-env -g EpconChat --storage-name inventree-media-vol
```

The name-only query does not show the values the checklist below asks for. Read
the values of only the non-secret switches (composed for this hand-over; names
containing KEY, SECRET, TOKEN or PASSWORD are excluded, and entries backed by a
`secretRef` show no value):

```
for A in "$APP" "$WORKER"; do
  az containerapp show -n "$A" -g "$RG" -o table --query "properties.template.containers[0].env[?(name=='INVENTREE_DEBUG' || name=='INVENTREE_AUTO_UPDATE' || starts_with(name,'AIMMS_')) && !contains(name,'KEY') && !contains(name,'SECRET') && !contains(name,'TOKEN') && !contains(name,'PASSWORD')].{name: name, value: value, secretRef: secretRef}"
done
```

Write down:

- [ ] current image and its **digest** (for rollback);
- [ ] revision mode (Single or Multiple), and `ingress.traffic`;
- [ ] CPU, memory, min/max replicas, probes;
- [ ] whether `INVENTREE_DEBUG` is set and to what (it was `True` in the July export);
- [ ] worker CPU, memory and revision mode (last recorded 2.0 vCPU / 4 GiB; keep it);
- [ ] **every** `AIMMS_*` name on web and worker, and the values from the query above (`AIMMS_WORK_ORDERS_ENABLED`, `AIMMS_MAINTENANCE_SCOPE_RESOLVER` and the others). Some `AIMMS_*` variables are read directly by code, not through the registry (section 5);
- [ ] whether `INVENTREE_AUTO_UPDATE` is set (it must stay off);
- [ ] whether the AzureFile volume `inventree-media-vol` is still mounted at `/home/inventree/data/media`, and which share backs it.

**Revision mode: very likely Multiple.** The July export and plan 13.0 say Single,
but the later sources say otherwise: `contrib/aimms_production_env.md` (September)
says the four apps are explicit-traffic multi-revision ("without `ingress traffic
set` a new revision serves nothing"), and the August ledger kept the old revision
`aimms-dev--0000073` active at 0% after the switch. Plan for Multiple; if step 0
prints `Single`, skip the traffic steps in section 6. **UNCONFIRMED** until step 0.

**Scaled to zero.** An app at zero replicas has nothing to exec into. Load its URL
once first.

### 3.3 Migration state (read only)

**Do not use the manual decision table in plan 13.0.** It says "contains
`0011_assetmachine_profile` → the other line — stop". That is stale: the two lines
were merged at `16c6cc511` (an ancestor of HEAD), and the Azure database was
migrated to the merged schema on 2026-10-11. It now holds both
`0011_assetmachine_profile` and `0011_equipment_registry`, and so does the code.
The authority is the lineage guard (section 7).

Read-only session from the owner's machine. Load the password from
`INVENTREE_DB_PASSWORD` in `data/azure-db.env` into `PGPASSWORD` without printing it
(do not `echo` it, do not run with `set -x`):

```
set -a; . data/azure-db.env; set +a
export PGPASSWORD="$INVENTREE_DB_PASSWORD"
docker run --rm -it -e PGPASSWORD -e PGSSLMODE=require -e PGOPTIONS='-c default_transaction_read_only=on' postgres:17 psql -h epconchat-pg-dev.postgres.database.azure.com -U pgadmin -d inventree
```

`-it` is required: without it the container has no stdin and psql exits at once.
For a non-interactive run, drop `-it` and pass each query with `-c "<query>"`.

Or from a replica of the current app: `az containerapp exec -n "$APP" -g "$RG" --command /bin/bash`,
then `python src/backend/InvenTree/manage.py dbshell`, then first run
`SET default_transaction_read_only = on;`.

Run:

```
select name from django_migrations where app = 'assets' order by id;
select count(*) from django_migrations;
select app, max(name) from django_migrations where app in ('aichat','assets','repair','tasks','users') group by app;
```

Pre-migration baseline recorded by the owner: Azure had 967 applied migrations,
with assets last at `0022_var_unit` on 13 October 2026. These are historical
values, not the expected state at deployment:

| Check | Expected |
|---|---|
| `count(*)` | 967 |
| aichat | `0053_controlled_projection_audit` |
| assets | `0022_var_unit` |
| repair | `0010_backfill_gate_blocking_authority` |
| tasks | `0030_workorder_tasks_wo_actual_completed_and_more` |
| users | `0024_notification_publish_permission` |

At hand-over, compare the applied count with the coordinator's completed table
near the top. From the **final image's code**, using the Azure database
configuration and bypassing the normal container startup entrypoint, run:

```
PGOPTIONS='-c default_transaction_read_only=on' python src/backend/InvenTree/manage.py showmigrations
PGOPTIONS='-c default_transaction_read_only=on' python src/backend/InvenTree/manage.py shell -c "from assets.deploy_lineage import report; report()"
```

Use the existing database variables and secret references; never print their
values. Every migration must be marked `[X]`, and the lineage report must say
`lineage ok: nothing to apply.` with no unknown or leftover migration notes.
Checking the old deployed image alone cannot verify the final image's migrations.
If anything is pending, unknown or different from the coordinator's record, stop
and return it to the coordinator. Do not apply migrations as part of deployment.

### 3.4 Packet/Work machine audit (read only)

R6 withholds a repair packet linked to a Work order unless the packet's machine
equals the Work order's machine. NULL on either side (including both) is also
withheld. Run this in the read-only session:

```
select count(*) as packets, count(p.work_order_id) as linked, count(*) filter (where p.work_order_id is not null and p.machine_id is distinct from w.machine_id) as mismatched, count(*) filter (where p.work_order_id is not null and (p.machine_id is null or w.machine_id is null)) as null_side from repair_repairpacket p left join tasks_workorder w on w.id = p.work_order_id;
```

A previous read-only audit reported 17 packets, 12 linked, 0 mismatched. No saved
output of that run exists in the repository, so treat it as **UNCONFIRMED** and run
it again. **Proceed only if `mismatched = 0`.** Report any non-zero count (including
`null_side`) to the owner before step 2. No source sets a stop rule for `null_side`;
such packets are simply withheld, so it is the owner's call.

**Re-run against the merged O24 rule.** The in-progress O24 code (worktree
`r6/o24-scope`, uncommitted change to `src/backend/InvenTree/repair/scope.py`)
requires machine scope for **every** packet, linked or standalone: a packet with no
machine, or on a machine outside the reader's client scope, is withheld. When O24 is
merged, re-read its `scoped_repair_packets` and run the audit plus these checks
(composed, not in any source):

```
select count(*) filter (where p.machine_id is null) as packet_without_machine, count(*) filter (where p.machine_id is not null and m.client_id is null) as machine_without_client from repair_repairpacket p left join assets_assetmachine m on m.id = p.machine_id;
```

### 3.5 Backup

The owner's read-only `pg_dump -Fc` backup of Azure was taken on 21 October 2026
before the coordinator's migration and is held by the owner locally. The
coordinator records its file name in the hand-over table. Do not include its path
or contents here. The pre-migration backup is already taken; the deployer does
not repeat a migration gate.

A fresh backup immediately before deployment is still recommended, because the
old web app and worker can continue writing after the pre-migration backup. The
deployer can take a read-only logical dump using `PGPASSWORD` as in section 3.3:

```
docker run --rm -e PGPASSWORD -e PGSSLMODE=require -v <private dir>:/backup postgres:17 pg_dump -h epconchat-pg-dev.postgres.database.azure.com -U pgadmin -d inventree -Fc --no-owner --no-acl -f /backup/inventree-<UTC date>-pre-deploy.dump
```

- Keep the file private, mode 600. It holds real data.
- Validate a new dump by restoring it into a throwaway local
  `pgvector/pgvector:pg15` container after `CREATE EXTENSION vector`, using
  `docker exec -i <db> pg_restore --verbose --exit-on-error --no-owner --no-acl -U <user> -d <db> < <dump>`.
  Compare the `django_migrations` count and business-table counts with those
  recorded when the dump was taken. This restore is local, not against Azure.
- Azure-native backup retention is **UNCONFIRMED**. Read it with
  `az postgres flexible-server show -g EpconChat -n epconchat-pg-dev --query backup`.
- `contrib/cosmos/reference_library/README.md` still requires a fresh `dbbackup`
  before its separate media publish step (section 9.4).

**Not a backup path:** `invoke backup` writes to `/home/inventree/data/backup`,
which is not on the mounted volume, and needs exec rights.

---

## 4. Build and push the image

From plan 13.2 step 1. The plan still pins `SHA=11cec10e58c0…` and
`DATE=2026-10-02T14:28:46+03:00`; **those are stale**. Use the final SHA from section 1.

```
SHA=<final 40-char sha>
DATE=<output of: git log -1 --format=%cI $SHA>
TAG="inventree-aniket-${SHA:0:12}"
az acr build --resource-group "$RG" --registry "$ACR" \
  --image "$REPO:$TAG" \
  --file contrib/container/Dockerfile --target production --platform linux/amd64 \
  --build-arg commit_hash="$SHA" --build-arg commit_date="$DATE" \
  --build-arg commit_tag="$TAG" \
  "https://github.com/EQUA-AI/InvenTree.git#$SHA"
DIGEST=$(az acr repository show --name "$ACR" --image "$REPO:$TAG" --query digest -o tsv)
IMAGE="$LOGIN/$REPO@$DIGEST"
```

Notes:

- The build fails on purpose if `build-info.json` does not equal
  `{"commit": commit_hash, "dirty": False, "ui_contract": 1}`. The production stage
  sets `INVENTREE_COMMIT_HASH` and `INVENTREE_COMMIT_DATE` from the build args.
- Keep the immutable tag and **deploy by digest**. Never add a `latest` tag.
  (In July aimms-dev ran `aimms-dev:latest`.)
- The image includes `psql`, `contrib/cosmos/review` and `contrib/cosmos/limits`.
- **UNCONFIRMED: an amd64 ACR build of this line's image has never run.** The 57 m 28 s
  figure is a **local arm64** rehearsal build (745 MB image, Python 3.14.7,
  InvenTree 1.6.0dev, API 545, Django 5.2.17). The August ledger records ACR
  production builds of the other line taking about 10 minutes (10 m 01 s and
  9 m 51 s) from a `git archive` context. The `az acr build` default timeout may still
  matter; consider `--timeout` (general CLI knowledge, not in the sources).
- If the commit cannot be put on GitHub (not in the sources): export the exact
  commit with `git archive <sha> | tar -x -C <dir>` and build that directory with
  `az acr build` or a local `docker build --platform linux/amd64`. A local build needs
  AcrPush and `az acr login`.
- The rehearsed lineage guard is byte-identical to HEAD's
  (`src/backend/InvenTree/assets/deploy_lineage.py`), and the container, Cosmos
  and dependency files are unchanged since the rehearsed base.

---

## 5. Configuration

Most switches are flags in the registry `src/backend/InvenTree/aimms_flags.py`, and
`AIMMS_EXPERT_GROUP_NAME` and `AIMMS_RECURRENCE_DAYS` are read in `settings.py`. The
registry is **not** the only path: code also reads some variables directly through
`os.environ` / `os.getenv`, for example `AIMMS_SINGLE_SITE_POLICY_KEY` and
`AIMMS_VOICE_CAPTURE_ENABLED` / `AIMMS_VOICE_PURPOSES` (`voice/api.py`,
`ai/core/decisions/adapters/capture_adapter.py`), `AIMMS_REPAIR_GENERATOR` and
`AIMMS_AI_TIMEOUT` (`repair/generation.py`) and `AIMMS_EGRESS_ALLOW`
(`ai/core/egress.py`). So keep every `AIMMS_*` name recorded in step 0 as it is. The
boot log prints one `AIMMS switchboard:` line listing every registry flag the
revision runs with; use it to verify the registry flags only.

**Boolean spelling.** Django-side booleans accept `1/y/yes/t/true/on`. But
`init.sh` migrates only when `AIMMS_MIGRATE_ON_START` lower-cases to exactly `true`.
A value of `1` skips that startup step. With the database already migrated, the
server starts either way; if migrations are pending, skipping it causes INVE-W8.
Use `True` if keeping the startup check enabled.

"New?" means new since the deployed `b4f1504a1` line.

### 5.1 Set on the web app

| Name | Value | New? | Purpose | Effect if unset |
|---|---|---|---|---|
| `AIMMS_WORK_ORDERS_ENABLED` | `True` | No | Master gate for the Work API, authority API, E1 media upload/download, canonical commands, risk rules. | Every `/api/tasks/work-orders/...` route returns 404; Closeout shows "Standalone completion is not enabled". |
| `AIMMS_CLOSEOUT_WIZARD_ENABLED` | `True` | No | Closeout API, capture, readiness, closeout readings panel; calibration traceability needs it. | Closeout capture and wizard return 404; readings panel hidden; calibration records cannot be written. **Not set in either rehearsal.** |
| `AIMMS_MAINTENANCE_SCOPE_RESOLVER` | `tasks.scope.granted_client_scope_resolver` | No | Resolves each user's client scope. **Behaviour change:** per the Cedar Creek plan (Pass B, 2026-10-11) the deployed app runs `single_site_scope_resolver` on `internal`. With the granted resolver, a user holding any non-`internal` grant loses the `internal` fallback (see 9.1). Check grants before switching. | Scope is "unresolved" (fail closed): machine and health reads 404, and even O24-shared Work stays invisible. |
| `AIMMS_SINGLE_SITE_CLIENT_CODE` | `internal` (or leave unset; default is `internal`) | No | Fallback client for users with no grant. Cedar Creek is under `internal`. | Default applies. A wrong value leaves users without grants with no scope. |
| `AIMMS_MAINTENANCE_AI_READ_ENABLED` | `True` | No | Assistant may read Work records. | "Ask about this work" is offered but cannot read Work. |
| `AIMMS_MACHINE_AI_READ_ENABLED` | `True` | No | Assistant may read machine records; `diagnostics.health.read`. | Assistant cannot read machines. |
| `AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER` | `repair.diagnostic_scope.single_site_diagnostic_capability_resolver` | No | Tools for the reasoning rail. | Rail has zero tools; route refused. |
| `AIMMS_PLANT_TIMEZONE` | `America/Chicago` (**owner to confirm**) | No | Time buckets for AI analytics only. | Server `TIME_ZONE` (UTC default) is used. Invalid value falls back silently. |
| `INVENTREE_COMMIT_HASH` | `$SHA` | No | Build provenance on the revision. | Provenance comes from the image only. |
| `INVENTREE_COMMIT_DATE` | `$DATE` | No | As above. | As above. |
| `AIMMS_MIGRATE_ON_START` | May stay `True`, or be left unset after verification | Yes (read by `init.sh`, not in the registry) | With `True`, startup checks lineage and runs its migration step, which finds nothing to apply on the coordinator-migrated database. | Skips the startup lineage/migration step; the server starts on the completed schema. Pending migrations cause INVE-W8. Verify lineage separately either way (section 7). |
| `AIMMS_COSMOS_PUMPHOUSE_ENABLED` | **Not in steps 2 or 4.** `True` per plan, set only by the separate step 6 command after the owner's decision | No | Turns on the scheduled Cosmos poll and the "enabled" readout. History reads are not gated by it. | No scheduled poll. |
| `AIMMS_CAPTURE_MAX_BYTES` | Optional; owner's cap in bytes, e.g. `104857600` (100 MiB) - a suggestion, not from the sources | Yes | Cap on E1 original uploads. | 0 = follow `INVENTREE_UPLOAD_MAX_SIZE` (500 MB on Azure). Originals are read fully into memory, at a 2 GiB limit. A non-integer gives a 500 on upload. |

### 5.2 Set on the worker

| Name | Value | New? | Purpose | Effect if unset |
|---|---|---|---|---|
| `INVENTREE_COMMIT_HASH`, `INVENTREE_COMMIT_DATE` | `$SHA`, `$DATE` | No | Provenance. | As above. |
| `AIMMS_COSMOS_PUMPHOUSE_ENABLED` | **Not in step 4.** `True` per plan, only through the separate step 6 command after the owner's decision. On the worker it starts polling within a minute, which writes to Azure Postgres. | No | Scheduled poll. | No poll. |
| `AIMMS_WORK_ORDERS_ENABLED`, `AIMMS_MAINTENANCE_SCOPE_RESOLVER` | as on web | No | Recommended parity so scheduled risk and memory tasks see the same switchboard (recommendation, not from the sources). | Worker tasks run with the flags off. |

**Never set `AIMMS_MIGRATE_ON_START` on the worker.**

### 5.3 Leave unset (owner decisions)

| Name | Default | New? | Why leave it |
|---|---|---|---|
| `AIMMS_CLOSEOUT_VERIFY_POLICY` | `off` | Yes (newly bridged) | O17. Acceptance stays "unknown"; recurrence opens linked corrective Work. |
| `AIMMS_EXPERT_GROUP_NAME` | `Reliability engineers` | Yes | O15 C2. Read **at migration time** by tasks 0035. The coordinator uses this name before hand-over; changing the web env at deployment does not rename the group already created. |
| `AIMMS_RECURRENCE_DAYS` | `30` | Yes | O15 C3. A non-integer stops the app from booting. |
| `AIMMS_WORK_SUPERVISOR_GROUPS`, `AIMMS_WORK_TECHNICIAN_GROUPS` | empty | Yes | No group names given yet. Everyone gets the neutral "All work" view; the setting grants nothing. |
| `AIMMS_CAPTURE_STORAGE_ALIAS` | `default` | Yes | Only `default`, `staticfiles` and `dbbackup` exist and there is no Azure storage backend. Azure Files is wired by mounting the share at the media root, not by this setting. Changing it later requires moving files. |
| `INVENTREE_AUTO_UPDATE` | off | No | Must stay off on web and worker: it skips the ownership check and starts the server even after a failed migration. |
| `INVENTREE_GUNICORN_WORKERS` | 4 (image default) | No | O18 was measured with 4 workers at 2 GiB. Fewer workers at less memory was never tested. |
| `AIMMS_EGRESS_MODE` | off | No | `enforce` was only used to isolate the rehearsal. |

### 5.4 Other configuration facts

- **`INVENTREE_DEBUG`.** The July export had `True`. Both rehearsals ran with `False`.
  With `True`, `init.sh` skips collectstatic and the SPA sync, and Django serves
  `MEDIA_ROOT` at `/media/` without the Work-authorized route (which includes
  capture originals; the names are random UUIDs). Whether `/web` loads with the new
  image under `True` is **UNCONFIRMED**. Owner's call; record what step 0 shows.
- **Cosmos connection** is not env: endpoint, database and container live in the
  HealthSource row. Authentication is `DefaultAzureCredential` (the app's managed
  identity). The fallback is `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and
  `AZURE_CLIENT_SECRET` as a Container App secret. The `INVENTREE_COSMOS_*` variables
  are for devtools only.
- **Flags that no environment variable can change** (they need a code change):
  `AIMMS_JOB_KITS_ENABLED`, `AIMMS_PROCEDURES_ENABLED`, `AIMMS_CLOSEOUT_EFFECTS_ENABLED`,
  `AIMMS_CLOSEOUT_AMENDMENTS_ENABLED`, `AIMMS_CLOSEOUT_VERIFY_SEPARATION`,
  `AIMMS_CLOSEOUT_AMENDMENT_APPROVAL`, `AIMMS_CLOSEOUT_RECON_ENFORCED`, `AIMMS_RPF_*`,
  `AIMMS_WORK_ORDER_REQUIRE_*`, `AIMMS_HEALTH_WEBHOOK_SECRETS`. See section 11.
- **Flags and image in one revision.** Plan step 2 sets flags with the image, while
  `contrib/container/AIMMS-release-checks.md` and `contrib/aimms_production_env.md`
  prefer one revision per phase. If you split them, deploy the image with the
  rollout controls first, then add the R6 flags in a second revision. In Multiple
  mode that second revision also starts at 0% and needs its own traffic switch
  (section 6, "Traffic in Multiple mode").
- **Credential in git history (owner decision needed).** `app.yaml` at commit
  `73c7ce6f5` (10 July) holds the database password as a plaintext env value;
  `f44148516` is the later commit that *deleted* the file. `73c7ce6f5` is reachable
  from `origin/IOT`, `origin/IOT-Merge`, `origin/inventTree-aniket`,
  `origin/equa/customizations` and three other remote branches (`git branch -r
  --contains 73c7ce6f5`). That GitHub repository is the build source, which plan
  section 7 calls a "public Git context". The owner's Azure state note says the
  password was still valid, and the server firewall still had an allow-all rule, on
  2026-10-11. **Ask the owner whether to rotate the password** (and tighten the
  firewall). Rotating also means updating the value the web and worker apps use
  (env value or Container App secret), which creates new revisions. Do not copy
  anything from that file.

---

## 6. Roll out

Prerequisites: sections 2 and 3 done, owner's go-ahead given, `$IMAGE` from section 4.
The coordinator's migration record is complete and the final-image checks show no
pending or unknown migrations. The deployer does not run migrations.
Web commands keep **1 vCPU / 2 GiB** (O18); keep the worker at its recorded 4 GiB.

**Step 1. Build** (section 4).

**Step 2. Deploy the web app against the already migrated database.**
The command below keeps `AIMMS_MIGRATE_ON_START=True`: its startup migration step
will find nothing to apply. It may instead be left unset (section 7.2); complete
the read-only checks before starting the revision either way. The app deployment
still needs the owner's go-ahead.

```
SUFFIX="r${SHA:0:8}"
NEW_REV="$APP--$SUFFIX"
az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" \
  --revision-suffix "$SUFFIX" --no-wait \
  --cpu 1.0 --memory 2.0Gi --min-replicas 1 \
  --set-env-vars INVENTREE_COMMIT_HASH="$SHA" INVENTREE_COMMIT_DATE="$DATE" \
                 AIMMS_MIGRATE_ON_START=True
az containerapp logs show -n "$APP" -g "$RG" --revision "$NEW_REV" --follow --tail 200
```

- Add the section 5.1 R6 flags to `--set-env-vars` here, or in a separate revision (5.4).
- **Never add `AIMMS_COSMOS_PUMPHOUSE_ENABLED` here.** It is set only by the separate
  step 6 command, after the owner decides (O15 A3, O23).
- A suffix can be used once. If you repeat step 2, change `SUFFIX`.
- In Multiple mode the new revision starts at 0%; the old revision keeps serving
  until you switch traffic in step 3a.
- The old web app and old worker can keep running on the expand-only schema
  until their replacements are ready. The coordinator's database defaults protect
  their inserts (section 7.1).

What the log tells you:

| Log | Meaning | Action |
|---|---|---|
| `lineage ok: nothing to apply.` with no unknown or leftover notes | Expected with the flag enabled. The following migration step has nothing to apply. | Continue to step 3 when healthy. |
| `STOP: …`, a pending-migration count, an unknown/leftover note, or any `Applying` line | The image and database do not match the hand-over record. | Stop the rollout, keep the old revision serving and return the logs to the coordinator. Do not run migrations to fix it. |
| INVE-W8 with the flag unset | Migrations are unexpectedly pending. | Stop and return to the coordinator. Do not enable the flag to apply them. |
| Container restarts before readiness | Startup may exceed the probe budget. | Inspect startup logs and probes; do not treat a restart as a reason to migrate. |

**Start-up time risk (UNCONFIRMED on aimms-dev).** Keeping the flag enabled still
boots the app for the guard and the no-op migration step before the server and
collectstatic. The production-image rehearsal (local, no CPU quota, 4 pending
migrations) took about 317 s in all: guard plus migration 173 s, then readiness
144 s. That is historical timing, not a measurement of this already migrated
startup. The default Container Apps TCP start-up probe for an ingress app is about
240 × 1 s. Confirm the actual probes and watch readiness. Leaving the flag unset
avoids the two extra application boots but still requires the read-only lineage
check in section 7.

**Connection pressure (UNCONFIRMED).** The server allows about 50 connections (15
reserved by Azure; the deployed workers held 12 on 2026-10-11). During the overlap
the old revision, new revision, startup checks and worker all share it.

**Step 3. Check the new revision.**

```
az containerapp revision show -n "$APP" -g "$RG" --revision "$NEW_REV" -o table --query "{health: properties.healthState, state: properties.runningState, traffic: properties.trafficWeight, replicas: properties.replicas}"
az containerapp exec -n "$APP" -g "$RG" --command "python src/backend/InvenTree/manage.py showmigrations"
```

Expect every migration marked `[X]`, including the coordinator's new tasks 0041
and users 0026 defaults (exact names in the migration record; section 7). Run the
read-only lineage check in section 7.2 and require no pending or unknown migrations,
even if the startup flag is unset. If exec will not take arguments, use
`--command /bin/bash`. In Multiple mode add `--revision "$NEW_REV"` to `exec`, so
you reach the new revision.

**Before switching traffic, run the section 8 checks against the new revision's own
URL** (the form in plan 12.5; the revision FQDN is reachable without traffic):

```
NEW_FQDN=$(az containerapp revision show -n "$APP" -g "$RG" --revision "$NEW_REV" --query properties.fqdn -o tsv)
curl --fail --silent --show-error "https://${NEW_FQDN}/api/" >/dev/null
curl --fail --silent --show-error "https://${NEW_FQDN}/health/live"
```

**Step 3a. Traffic in Multiple mode (very likely; confirm with step 0).** Every
revision you create starts at **0%**: the step 2 revision, any optional startup-flag
revision (`--remove-env-vars`), a separate flag revision (5.4), the step
6 flag revision and the step 7 revision. Each needs its own traffic switch once it
is healthy. Name both weights, in the form plan 12.8 uses. For the step 2 revision:

```
az containerapp ingress traffic set -g "$RG" -n "$APP" \
  --revision-weight "$NEW_REV=100" "$PREVIOUS_REV=0"
```

For any later revision:

```
CUR_REV=$(az containerapp show -n "$APP" -g "$RG" --query "properties.configuration.ingress.traffic[?weight==\`100\`].revisionName | [0]" -o tsv)
LATEST_REV=$(az containerapp show -n "$APP" -g "$RG" --query properties.latestRevisionName -o tsv)
az containerapp revision show -n "$APP" -g "$RG" --revision "$LATEST_REV" --query "{health: properties.healthState, state: properties.runningState}"
az containerapp ingress traffic set -g "$RG" -n "$APP" \
  --revision-weight "$LATEST_REV=100" "$CUR_REV=0"
```

Keep `$PREVIOUS_REV` active at 0% as the rollback target (the August ledger kept
`aimms-dev--0000073` that way). Then confirm with
`az containerapp ingress traffic show -n "$APP" -g "$RG"`.

**Step 3b. Previous revision against the migrated schema (CR-9).**
`contrib/aimms_production_env.md` (CR-9) prescribes running the previous revision's
`manage.py check` against the migrated database. In Multiple mode, with
`$PREVIOUS_REV` still active (load its revision URL first if it is scaled to zero):

```
az containerapp exec -n "$APP" -g "$RG" --revision "$PREVIOUS_REV" --command "python src/backend/InvenTree/manage.py check"
```

Record this as an additional configuration check. The old code already runs on
the expand-only schema; no schema rollback is needed (section 10). If the check
fails, report the failure to the owner and investigate before continuing. In
Single mode the previous revision is deactivated once the new one is ready, so
this check is not available; note that in the record.

**Step 4. Deploy the worker.** The coordinator has already migrated the database.
The old worker can run safely on that schema until this replacement is ready.

```
az containerapp update -n "$WORKER" -g "$RG" --image "$IMAGE" \
  --set-env-vars INVENTREE_COMMIT_HASH="$SHA" INVENTREE_COMMIT_DATE="$DATE"
```

- Record `WORKER_PREVIOUS_REV` first. No `AIMMS_MIGRATE_ON_START`. Keep
  `INVENTREE_AUTO_UPDATE` off.
- **No `AIMMS_COSMOS_PUMPHOUSE_ENABLED` here.** Azure already has 3 activated
  stations with checkpoints and the deployed worker runs the poll schedule every
  minute (owner's notes); with the flag set, the new worker would start polling at
  once and write to Azure Postgres before the owner decides (step 6).
- **Keep the worker's size.** The command passes no `--cpu` / `--memory`, so the
  worker keeps what step 0 printed (last recorded 2.0 vCPU / 4 GiB). Do not apply
  the web app's 1 vCPU / 2 GiB (O18) to the worker: the old image's dev worker
  peaked at 1,944 MiB in the August soak. Its memory under the new image has not
  been measured (**UNCONFIRMED**); watch it.
- If the worker is in Multiple revision mode, the old worker revision may stay
  active and keep running beside the new one (general Container Apps behaviour,
  **UNCONFIRMED** here). After the new worker revision is healthy, list active
  revisions and deactivate the old one
  (`az containerapp revision deactivate -n "$WORKER" -g "$RG" --revision "$WORKER_PREVIOUS_REV"`).
  Re-activate it only for a rollback.

**Step 5. Cosmos read access.** Grant Cosmos Data Reader on
`/dbs/aimms/colls/pumphouse_readings` to both the web and worker identities, using
the exact commands in `.azure/deployment-plan.md` 13.2 step 5. The web identity had
no Cosmos grant on 2 October; without this, history reads fail.

**Step 6. Telemetry bootstrap: verify, do not re-run without a decision.** The plan
says "run section 14, start to finish". The owner's notes say Azure already holds 3
activated stations, about 1,452 bindings, 404 limits, 3 checkpoints and HealthSource
id 1 (**UNCONFIRMED**, from notes only; the 20 October copy had 1,643 bindings).
O15 A3 says only the showcase station goes live, and O23 says Cedar Creek's live feed
is not restarted. Setting `AIMMS_COSMOS_PUMPHOUSE_ENABLED=True` polls **every** active
checkpoint. Deactivating a station is a DELETE on
`/api/assets/registry/<station>/activate/`, which removes its managed bindings and
state. **Ask the owner** whether to turn the poll on, and which stations stay active,
before setting the flag or re-running section 14. All of these are Azure writes.

Only after the owner's written decision, and after any station deactivation the
owner asked for, turn the poll on with this separate command (worker first: the
worker runs the poll; on the web app the flag only drives the "enabled" readout):

```
az containerapp update -n "$WORKER" -g "$RG" --set-env-vars AIMMS_COSMOS_PUMPHOUSE_ENABLED=True
az containerapp update -n "$APP" -g "$RG" --set-env-vars AIMMS_COSMOS_PUMPHOUSE_ENABLED=True
```

The web update creates a new revision; in Multiple mode switch traffic to it as in
step 3a. If the owner says no, skip this command; nothing else depends on it.

**Step 7. Set the steady size (O18).** `AIMMS_MIGRATE_ON_START` may stay `True`;
its startup step will find nothing to apply. The example removes it to avoid two
extra application boots. To keep it, omit `--remove-env-vars AIMMS_MIGRATE_ON_START`.
Both choices require the section 7 checks.

```
az containerapp update -n "$APP" -g "$RG" --remove-env-vars AIMMS_MIGRATE_ON_START --cpu 1.0 --memory 2.0Gi --min-replicas 0
```

The plan's comment says "`--min-replicas` is whatever step 0 printed", but the
command hard-codes 0 (the July export had 0). **Use the value step 0 printed.** Ignore
the plan's older line saying step 7 brings it down to 1 GiB; O18 overrides it.

Step 7 creates a new revision. In Multiple mode it starts at 0%, so switch traffic
to it as in step 3a ("any later revision"); until then the step 2 revision keeps
serving. Keeping the migrate flag set is also valid on the completed schema.

---

## 7. Migrations and the startup guard

### 7.1 Coordinator-applied migrations

The coordinator applies the schema changes to Azure **before deployment** and
completes the hand-over table. The deployer verifies them against the final SHA's
image; no migration command is part of the rollout. The final image must include
the expand-only default migrations as well as the 15 migrations listed below.

Azure's baseline was 967 applied migrations, with assets `0022_var_unit` last
applied on 13 October 2026. The line adds these 15 migrations, plus tasks 0041 and
users 0026 for database defaults. The table describes the original 15; its
rehearsal column refers only to the historical local-copy rehearsal.

| # | Migration | Rehearsed? | What it does | Data step |
|---|---|---|---|---|
| 1 | `aichat.0054_investigation_proposal_action` | Yes | AlterField (choices only). | None |
| 2 | `repair.0011_repair_finding_revisions` | Yes | CreateModel. | None |
| 3 | `tasks.0031_investigation_requests` | Yes | CreateModel ×2 (investigation requests and artifacts). | None |
| 4 | `tasks.0032_closeoutcapture_voice_unavailable_reason` | Yes | AddField `voice_unavailable_reason`. | None |
| 5 | `assets.0023_registry_recorded_facts` | No | 4 new tables: RegistryCommand, SignalDesignation, ComponentFit, SharedPerformanceView. | None |
| 6 | `repair.0012_operational_authority` | No | New append-only GateAuthorityRecord. | None |
| 7 | `users.0025_operating_authority_role` | No | AddField `ruleset.can_record_operating_authority` (default False). | RunPython `preserve_operating_grants`: keeps the flag for groups that already hold the permission. Expected to update 0 rows on Azure. |
| 8 | `tasks.0033_operational_authority` | No | New OperationalAuthorityRecord with permission `record_operating_authority`. | None |
| 9 | `tasks.0034_work_time_downtime` | No | New WorkTimeInterval and WorkDowntime. | None |
| 10 | `tasks.0035_process_decisions` | No | New WorkHandover; Work `recurrence_of`, `recurrence_occurrence`; escalation fields on investigation requests. | RunPython `expert_group`: creates the auth group `AIMMS_EXPERT_GROUP_NAME` (default `Reliability engineers`) if absent, with no roles and no members. Reverse is a no-op. |
| 11 | `tasks.0036_capture_originals_accepted_reports` | No | New CaptureOriginal (file under `capture-originals/<uuid>`) and AcceptedCloseoutReport. | None |
| 12 | `tasks.0037_calibration_traceability` | No | New CalibrationTraceability. | None |
| 13 | `tasks.0038_routine_corrective_origin` | No | Work `corrective_origin_kind`, `corrective_finding`, `corrective_reading`, `corrective_step`. | RunPython `mark_recurrences`. Expected to update 0 rows. |
| 14 | `tasks.0039_merge_r6_parallel` | No | Merge only. | None |
| 15 | `tasks.0040_merge_r6_o21` | No | Merge only. | None |

Totals for those 15 migrations, from the migration files:

- **15 new tables** (CreateModel): 3 in the rehearsed rows 1-4 (RepairFindingRevision,
  InvestigationRequest, InvestigationArtifact) and 12 in rows 5-12 (assets 0023 4,
  repair 0012 1, tasks 0033 1, tasks 0034 2, tasks 0035 1, tasks 0036 2, tasks 0037 1).
- **12 AddField columns**: tasks 0032 (1), users 0025 (1), tasks 0035 (6), tasks
  0038 (4). Only **8** are on tables that exist before the coordinator's migration:
  `tasks_workorder` 6, `tasks_closeoutcapture` 1, `users_ruleset` 1. The other 4
  (0035's escalation fields) are on the investigation-request table that tasks 0031
  creates during the coordinator's migration.
- **1 AlterField** (aichat 0054, choices only).
- 3 RunPython steps (users 0025, tasks 0035, tasks 0038), no RunSQL in these 15 files.

The module docstring of 0035 says "apply only in an explicitly approved
environment". The 0038 module docstring reads "Local O21 linked-corrective origins;
no routine outcome backfill."; its migration class docstring adds "apply only in an
approved environment". The coordinator handles the migration under the owner's
21 October plan; deployment still needs its own go-ahead (section 2).

The coordinator also applies new **expand-only database-default migrations**:

| Migration | Exact final migration name | Purpose |
|---|---|---|
| tasks 0041 | to be confirmed by the coordinator's migration record | Add database defaults for the three tasks columns below. |
| users 0026 | to be confirmed by the coordinator's migration record | Add a database default for the ruleset column below. |

These migration files are not visible in this checkout. Confirm their exact names
and inclusion in the final image from the coordinator's migration record.
They make these four NOT NULL columns safe for inserts from the old web app and
the old worker, which remain running until deployment:

| Column | Database default |
|---|---|
| `tasks_workorder.corrective_finding` | Empty string |
| `tasks_workorder.corrective_origin_kind` (the CharField added in tasks 0038) | Empty string |
| `tasks_investigationrequest.escalation_reason` | Empty string |
| `users_ruleset.can_record_operating_authority` | `false` |

Database defaults apply when old code omits a new column; a Python model default
alone does not provide that protection. The schema remains expand-only, and the
old code already runs on it before the deployer switches images.

If these are the only additions, the arithmetic is 967 + 15 + 2 = 984 applied
rows. This is an expectation, not the completed migration record. Use the
coordinator's actual count and exact names in the hand-over table. Expected leaves
are aichat `0054_investigation_proposal_action`, assets
`0023_registry_recorded_facts`, repair `0012_operational_authority`, tasks 0041
and users 0026 (exact final names to be confirmed by the coordinator's migration
record). `showmigrations` from the final image must show nothing pending.

**Historical rehearsal limit.** Only the first 4 migrations were timed on a local
copy: guard plus migration took 172.8 s, and seven business-table row counts were
unchanged. That rehearsal did not cover the full 15 plus the new defaults; it is
not evidence of the coordinator's completed Azure migration.

### 7.2 What the startup guard does

The deployer's acceptance check is stricter than the guard's refusal rule:
**no pending or unknown migrations**, and no leftover-app notes. Require
`lineage ok: nothing to apply.` with no warnings or notes, plus every migration
marked `[X]` in `manage.py showmigrations`. A zero exit code alone is insufficient.

With `AIMMS_MIGRATE_ON_START=True`, `contrib/container/init.sh` checks lineage,
then invokes its built-in migration step before starting the server. On the
coordinator-migrated database this finds **nothing to apply** and prints
`Migration step finished - starting the server`. Keeping the flag is valid; it
costs two extra application boots at every start. It is not a way for the deployer
to apply missing migrations.

With the flag unset or `False`, startup skips that guard and migration step.
The server starts normally on the completed schema. If migrations are pending,
it logs `INVE-W8: Database Migrations required` and exits. Run the read-only
lineage check separately whether the flag is enabled or unset, before rollout
(section 3.3) and on the new revision before switching traffic (section 6).

The guard (`src/backend/InvenTree/assets/deploy_lineage.py`):

- **Refuses** only when the code has pending migrations **and** the database holds
  migrations this code does not know. Names in on-disk `replaces` lists and 7
  retired upstream names are excluded: build `0021_auto_20201020_0908`,
  `0022_auto_20201020_0953`, `0023_auto_20201020_1009`, `0024_auto_20201020_1144`,
  `0025_auto_20201020_1248`, `0026_auto_20201023_1228`, and part `0061_auto_20210104_2331`.
  Rows for uninstalled apps count as leftovers.
- A refusal prints `STOP: …` and exits 1 with **nothing changed**.
- A database ahead of its image only gets a note. This applies to images that contain
  the guard. The deployed `b4f1504a1` does not (`git ls-tree b4f1504a1` has no
  `assets/deploy_lineage.py`, and its `init.sh` has no `AIMMS_MIGRATE_ON_START`), so
  rollback safety for that image comes from the expand-only schema and database
  defaults, not from this guard (section 10).
- On the 20 October copy, the fixed guard reported no unknown and no leftover migrations.
- When nothing is pending it prints `lineage ok: nothing to apply.` It can also
  print unknown-migration notes, so check the entire output, not just this line.

Read-only lineage check you can run any time:

```
az containerapp exec -n "$APP" -g "$RG" --command "python src/backend/InvenTree/manage.py shell -c \"from assets.deploy_lineage import report; report()\""
```

(If exec does not pass the quoting, open `--command /bin/bash` and run
`python src/backend/InvenTree/manage.py shell -c "from assets.deploy_lineage import report; report()"`.)

---

## 8. Verify

### 8.1 Health and container

- [ ] `GET /health/live` returns `{"status":"alive"}`.
- [ ] `GET /api/` returns 200.
- [ ] `GET /api/system/health/` responds.
- [ ] `GET /health/ai-ready` returns `ready`, or 503 `not_ready` if the AI plane is not configured. **UNCONFIRMED** on aimms-dev.
- [ ] `showmigrations` marks every migration `[X]`, including tasks 0041 and users 0026 (exact names from the coordinator's record); nothing is pending.
- [ ] Lineage check prints `lineage ok: nothing to apply.` with no unknown or leftover migration notes.
- [ ] In the container, `ls contrib/cosmos/review contrib/cosmos/limits` lists 14 files and 1 (the counts at `a858aadec` from `git ls-tree`; re-count on the final SHA). Plan 13.3's "nine files and one" is the count at `11cec10e5` and is stale. The Dockerfile copies both directories (COPY lines 186 and 192) and `.dockerignore` excludes none of these files.
- [ ] Boot log has one `AIMMS switchboard:` line with the section 5 values.
- [ ] Revision is Healthy and Running and carries the traffic. In Multiple mode, run the checks in 8.1-8.3 against the new revision's own URL (`$NEW_FQDN`, step 3) before switching traffic, and the public URL after.
- [ ] Memory stays below 2 GiB (rehearsal peak about 1.0 GiB). A management command adds 330-350 MiB.

### 8.2 Browser

- [ ] `/` redirects to `/web`; sign in (a sign-in writes to Azure Postgres; covered by the 8.3 go-ahead).
- [ ] No hash-ID strings in place of text.
- [ ] `/web/machines/index/`: Alarms column and "In alarm only".
- [ ] `/web/machines/machine/93/`: Pumphouse mimic.
- [ ] `/web/machines/machine/102/`: Details › Equipment view, Health, Performance (beside Health), Parts, Maintenance, Fault History.
- [ ] `/web/maintenance/work-orders/3826/`: Work, Diagnose, Plan & Safety, Parts, Verify & Return, Closeout, History.
- [ ] Recorded history is labelled as historical, not live (O23; see section 11).
- [ ] Admin pages: `/web/settings/admin/user` and `/web/core/group/<id>/`.

### 8.3 Authenticated API smoke

**These smoke tests write to Azure Postgres** and need the owner's go-ahead under
the standing rule: creating test users (and their grants), calling
`/api/user/me/token/` (inserts an API token) and signing in (8.4; session and
last-login rows) are all writes. Delete the test users, grants and tokens afterwards,
as the August ledger records ("disposable identities, tokens, and threads deleted").

Use an operator with the Work roles and an `internal` grant. Create test users
through the UI or admin, not through `manage.py shell` (a shell-created user has no
UserProfile and the token call returns 500 "User has no profile.").

Rehearsed (all 200 at 2 GiB):

- [ ] `/api/user/me/token/?name=<name>` (basic auth)
- [ ] `/api/`
- [ ] `/api/assets/machines/102/`
- [ ] `/api/assets/machines/102/positions/`
- [ ] `/api/assets/registry/102/components/`
- [ ] `/api/assets/machines/102/work-summary/`
- [ ] `/api/machine-health/machines/102/health/`
- [ ] `/api/machine-health/machines/102/health/signals/`
- [ ] `/api/kanban/work-orders/3826/overview/` (the read that OOM-killed 1 GiB)
- [ ] `/api/tasks/work-orders/3826/materials/`

New R6 reads, not rehearsed:

- [ ] `/api/kanban/work-orders/persona/`
- [ ] `/api/kanban/work-orders/3826/operational-authority/`
- [ ] `/api/kanban/work-orders/3826/investigation-requests/` and `?escalation_owners=1`
- [ ] `/api/tasks/work-orders/3826/time/`
- [ ] `/api/assets/recorded/102/critical-signals/`
- [ ] `/api/assets/recorded/102/performance-views/`
- [ ] `/api/assets/recorded/102/slots/<component>/fits/`

### 8.4 Release gate script

GET only, but it needs an operator's session-cookie file (an API token is not
enough), and signing in to get that cookie writes to Azure Postgres (8.3 go-ahead):

```
python .github/scripts/check_aimms_release.py \
  --base-url https://CANDIDATE_HOST \
  --cookie-file /private/path/session-cookies.txt \
  --expect-commit FULL_40_CHARACTER_GIT_SHA
```

It checks `/health/live`, `/health/ai-ready` (must be `ready`), backend commit,
`/static/web/build-info.json` (commit equals SHA, `dirty: false`), AI threads, voice
capability, proposals, risk scopes and the 15 maintenance metrics. It will fail if
AI is not configured on aimms-dev (**UNCONFIRMED**). Delete the cookie file afterwards.

### 8.5 Worker

- [ ] Worker logs show `poll_cosmos_pumphouse_sources` from the new image (only if the pumphouse flag was turned on) and no `AUTH` error code on the checkpoints.
- [ ] No "Background worker check failed" in the web log (in the rehearsals this appeared only because no worker ran).
- [ ] Worker inventory, heartbeat and failed-task checks from `contrib/container/AIMMS-release-checks.md`.
- [ ] `check_pumphouse_readiness --source $SRC [--probe]`: read its issues; do not gate on its exit code (it can never report ready for this estate). `$SRC` is the Cosmos HealthSource id, 1 per the owner's notes (**UNCONFIRMED**). `deploy_preflight --json` always exits 0 and cannot gate anything.

Benign log noise seen in the rehearsals: INVE-W3 "Could not detect git information",
"RAG default-on flags degraded off (providers incomplete)", "model-pin boot probe
skipped", "DevUI unavailable". "AIMMS lifecycle failed; AI unavailable" means the AI
mount did not start.

---

## 9. One-time setup after the deploy

**Every item here writes to the Azure database and needs the owner's go-ahead at the time.**

### 9.1 Client scope grants

With `tasks.scope.granted_client_scope_resolver`:

- a user with grant rows sees exactly those clients;
- a user with none falls back to client `internal`;
- both require the Work order view role.

Cedar Creek, the other stations and Pine Hollow are on `internal`.

```
az containerapp exec -n "$APP" -g "$RG" --command "python src/backend/InvenTree/manage.py grant_client_scope <user> internal"
```

Use `--revoke` to remove a grant. **Gotcha:** granting any other client removes the
`internal` fallback for that user, so grant `internal` too. Under O24, Work with a
customer (and the 3 orders with neither customer nor machine) is visible to every
Work viewer anyway. Machine, health and telemetry reads still need the scope.

### 9.2 "Reliability engineers" group (O15 C2)

Migration tasks 0035 creates the group with **no roles and no members**. Escalation
offers no owners, and refuses, until it has an active member.

- Add members: Admin Center → Users (`/web/settings/admin/user`) → open the user → "User Groups".
- Each member must also hold the Work order **View** role (give it to this group, or check members have it through another group) and have access to the Work.
- The person escalating needs Work view and change.
- Verify: `GET /api/kanban/work-orders/<id>/investigation-requests/?escalation_owners=1` returns `{group, available, reason, owners}`. `group_not_configured` means the group is missing.

### 9.3 Named roles (O15 B1, B2, O20 and others)

No group gains the new permissions automatically. Users 0025 keeps the operating
permission only for groups that already had it, which on Azure should be none, so
after deploy only superusers hold it. Set these on the group's roles
(`/web/core/group/<id>/`, RoleTable):

| Record | Role needed |
|---|---|
| Test acceptance (B2, maintenance) | Work view + change + "Can execute work orders" |
| Operating permission, observed return, service state (B2, operations) | Work view + change + "Can record operating authority and service state" - grant it to the site's named operations role |
| Physical isolation / permit / access window on packet gates (B1) | "Can execute work orders" plus the gate's own required permission, if set (values on Azure **UNCONFIRMED**). Packet machine must equal Work machine. |
| Calibration traceability (O20) | "Can capture closeout narratives", Work scope, calibration-type Work, and `AIMMS_CLOSEOUT_WIZARD_ENABLED=True` |
| Accept handover (C1) | Incoming owner, active, scoped, "Can execute work orders" |
| Critical signals (D1), default Performance views (D5), curation | native `admin` change role |
| Fitted items (D2) | Stock view |

All authority records also need machine scope on the Work's machine. Check what a
signed-in user may record with `GET /api/kanban/work-orders/<id>/operational-authority/`
(the `commands` booleans). Read-only role audit:
`python src/backend/InvenTree/manage.py audit_role_permissions [--group <pk>]`.

Persona groups (`AIMMS_WORK_SUPERVISOR_GROUPS` / `AIMMS_WORK_TECHNICIAN_GROUPS`)
stay unset until the owner names them.

### 9.4 Media storage for E1 originals

Originals (`capture-originals/<uuid><ext>`) are written to the `default` storage at
`MEDIA_ROOT` (`/home/inventree/data/media`) by the web app only.

- **If step 0 shows `inventree-media-vol` (AzureFile) mounted at `/home/inventree/data/media`**,
  E1 already lands on Azure Files. **Very likely, that share is already shared with
  `aimms-experimental`**, whose database is different. Environment storage is defined
  once per managed environment, and both apps mount the same storage name in the
  same environment: aimms-dev in `73c7ce6f5:app.yaml` (volume `inventree-media-vol`,
  storage `inventree-media-vol`, environment `epcon-ai-env`), aimms-experimental in
  `.azure/deployment-plan.md` lines 298 and 407, and the August ledger puts all four
  apps in `epcon-ai-env`. So aimms-dev media, including E1 originals, very likely
  already mixes with aimms-experimental's. Step 0's `env storage show` names the
  share; record it and tell the owner, who decides whether aimms-dev needs its own
  storage name and share.
- **If no share is mounted**, originals live on the container filesystem. They are
  lost on redeploy or scale-down (step 7 allows scale to zero) while their database
  rows remain, and they are not shared between replicas. Provisioning is in
  `contrib/cosmos/reference_library/README.md`:

```
az storage account create -g EpconChat -n <account> -l <region of EpconChat> \
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
  --allow-blob-public-access false
az storage share-rm create -g EpconChat --storage-account <account> \
  -n aimms-media --quota 50
az role assignment create --assignee Aniket@equa.work \
  --role "Storage Account Contributor" \
  --scope $(az storage account show -g EpconChat -n <account> --query id -o tsv)
STORAGE_ACCOUNT=<account> BACKUP_REF=<fresh dbbackup file> \
  contrib/cosmos/reference_library/publish_media_to_azure_files.sh plan
STORAGE_ACCOUNT=<account> BACKUP_REF=<fresh dbbackup file> \
  contrib/cosmos/reference_library/publish_media_to_azure_files.sh apply
```

  Then register the share on the environment. The README gives this command only as
  prose with "..."; the complete form below is composed from general CLI knowledge
  (**UNCONFIRMED**). The key is read into a variable and never printed:

```
STORAGE_KEY=$(az storage account keys list -g EpconChat -n <account> --query "[0].value" -o tsv)
az containerapp env storage set -n epcon-ai-env -g EpconChat \
  --storage-name aimms-media \
  --azure-file-account-name <account> \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name aimms-media \
  --access-mode ReadWrite
unset STORAGE_KEY
```

  Then add a read-write AzureFile volume mounted at the media path on **both** web
  and worker. **Copy the existing media into the share before mounting**, because a
  mount hides what is already at that path. The publish script writes to the Azure
  database through a local container, so it needs the owner's go-ahead. Keep
  `AIMMS_CAPTURE_STORAGE_ALIAS` at `default`.

---

## 10. Rollback

Roll back by **digest**, never by a mutable tag.

**Web:**

```
az containerapp revision copy -n "$APP" -g "$RG" --from-revision "$PREVIOUS_REV"
```

**Worker:** the same, with `-n "$WORKER"` and `--from-revision "$WORKER_PREVIOUS_REV"`.

**In Multiple revision mode**, switch the web traffic back instead, naming both
weights (the form in plan 12.8; `$CUR_REV` is the revision now at 100%):

```
az containerapp ingress traffic set -g "$RG" -n "$APP" \
  --revision-weight "$PREVIOUS_REV=100" "$CUR_REV=0"
```

The worker has no ingress, so traffic does not apply to it: re-activate
`$WORKER_PREVIOUS_REV` (`az containerapp revision activate`) and deactivate the new
worker revision, or use `revision copy` as above.

Rolling the web and worker images back to their previous revisions is safe:
the coordinator's schema is expand-only, and the old code already runs on it
before deployment. The added database defaults protect old-code inserts into the
four NOT NULL columns listed in section 7.1. **No schema rollback is needed or
advised.** Leave the migrated schema and its defaults in place.

Do not reverse migrations, drop the new columns or apply ad hoc default SQL as
part of image rollback. The coordinator has already supplied the defaults.

A database restore is separate disaster recovery, not an image rollback. It needs
the owner's explicit go-ahead and a recovery plan: stop every web app and worker
using that database first, because restoring a backup loses all writes since it
was taken. The pre-migration backup is held by the owner; a deployer may also have
a newer pre-deploy backup (section 3.5). Do not restore either for a routine image
rollback. `INVENTREE_RESTORE_HOLD=1` makes the app answer HTTP requests with 503
"Restore in progress." during a separately approved recovery.

---

## 11. Known limits and intentionally unavailable data

**Intentionally unavailable, or shown as "not recorded":**

- **Cedar Creek has no live feed** (O23). The screens use historical readings ending
  2025-08-01 02:59. With polling on and a closed window, zero documents is not an
  error: `last_poll_at` advances and `last_error_code` stays empty.
- **Open wording conflict (UNCONFIRMED).** `machine_health/services/display_time.py`
  shifts recorded history to read as the last ten days. Performance says "Recorded
  history presented as current; the plant's own times are … earlier." O23 asks for
  "historical everywhere, never as live". Whether every screen meets O23 is
  **UNCONFIRMED**. The unmerged `r6/design-pass-2` branch changes this text.
- Physical, operating and test authority, calibration traceability, fits, critical
  signals and similar facts show "not recorded" until an authorized person records
  them. No site records, procedures or role grants were supplied.
- The real spare for WO-003826 is unknown, so the walkthrough shows the shortage
  path (O23). Receiving it is a later Azure write with the owner's go-ahead.
- Closure acceptance is "unknown" while `AIMMS_CLOSEOUT_VERIFY_POLICY` is off (O17).
- Job kits, procedures (prepared packages and deviations), closeout effects
  execution and closeout amendments (the E2 amendment path) are off. Their flags are
  not environment-settable (section 5.4). Materials show native requirements with a
  stock check and never a reservation. Whether R6 acceptance needs any of these is
  **UNCONFIRMED**.
- Email alerts need a mail host. Only bell notifications are sent.

**Operational limits:**

- Scale to zero: no HTTP request for 300 s means no replicas. An exec session does
  not keep the app alive, so a long command can lose its container.
- Keeping migrate-on-start adds two application boots even with nothing pending; startup probe timing on Azure is unconfirmed (section 6, step 2).
- Image rollback keeps the expand-only schema and the coordinator-applied database defaults (section 10).
- The rehearsals did **not** exercise the worker, telemetry ingestion, write journeys,
  non-admin permissions, Azure probes or any AI path.
- **Known test failures that are not caused by R6** (same failures on the pre-R6 base
  `fe0d73a4a`):
  - 4 alarm-notification tests in `machine_health.tests.test_alarm_notifications` (3 failures, 1 error);
  - 5 onboarding errors, "Invalid physical unit", `'cusec' is not defined`;
  - one offline-SQLite "Cannot operate on a closed database" test;
  - three renderer-sensitive 3D pixel tests on the Mac, and an intermittent assistant-width browser check.

  `HANDOFF.md` counts "3 failures and 6 errors"; the log shows 6 errors = 5 onboarding
  + 1 alarm email. That the onboarding errors have the same cause on the base is
  **UNCONFIRMED**.
- `r6/design-pass`, `r6/design-pass-2` and `r6/work-uplift`
  (Work-workspace uplift, in progress per the 21 October checkpoint in `HANDOFF.md`)
  are not merged.
  P34 and P36 are Blocked and P35 is in progress. P36's deployed evidence waits for
  this deployment.

---

## 12. Open items and who owns them

| # | Item | Owner |
|---|---|---|
| 1 | Merge O24 (`r6/o24-scope`) and any other finished branches (`r6/work-uplift`, `r6/design-pass`, `r6/design-pass-2` are still unmerged per `HANDOFF.md`) into `inventTree-aniket`; reconcile the final migrations with the coordinator's record (section 7.1) and re-run the packet audit (3.4) against the result. | Coordinator agent (merge), owner (approval) |
| 2 | Merge `inventTree-aniket` into `IOT-Merge` (O25). | Owner |
| 3 | Push the final commit to `github.com/EQUA-AI/InvenTree` and name the 40-character SHA to build. Who pushes is **UNCONFIRMED**. | Owner |
| 4 | Written go-ahead for deployment and every Azure write in sections 6, 9 and 10. Coordinator completes the migration before hand-over under the 21 October plan. | Owner (deployment go-ahead), coordinator (migration and record) |
| 5 | Step 0 readout: image and digest, revision mode, traffic, size, probes, min-replicas, env names, volumes; confirm which app is the worker. | Deployer |
| 6 | Complete the database hand-over table; deployer verifies final-image `showmigrations` and lineage (nothing pending or unknown), plus the packet/Work audit (sections 3.3, 3.4, 7). | Coordinator (record), deployer (verification) |
| 7 | Pre-migration backup taken 21 October, held by the owner; coordinator records its file name. Fresh validated pre-deploy backup recommended (section 3.5). Check Azure PG backup retention (**UNCONFIRMED**). | Owner (holds backup), coordinator (record), deployer (recommended new backup) |
| 8 | Build, push, roll out, verify (sections 4, 6, 8). Watch the amd64 build time and ACR timeout, start-up time at 1 vCPU, worker memory (keep the worker's 4 GiB), and switch traffic for every new revision in Multiple mode. | Deployer |
| 9 | Cosmos Data Reader grants (step 5). | Deployer (needs Cosmos account rights) |
| 10 | Decide on the pumphouse poll and whether to re-run section 14, given O15 A3 and O23. | Owner |
| 11 | Confirm `AIMMS_PLANT_TIMEZONE=America/Chicago`, an `AIMMS_CAPTURE_MAX_BYTES` cap, and `INVENTREE_DEBUG`. | Owner |
| 12 | Media storage: confirm which share backs `inventree-media-vol`, or provision `aimms-media` (section 9.4). | Owner (decision), deployer (Azure) |
| 13 | One-time setup: grants, Reliability engineers members, named operations and maintenance roles (section 9). | Owner (names people and roles), admin on aimms-dev |
| 14 | AI readiness on aimms-dev, needed by the release-gate script. | Owner / deployer, **UNCONFIRMED** |
| 15 | Whether `LocalDocs/scripts/aimms_deploy.sh` exists and should be used. | Owner, **UNCONFIRMED** |
| 16 | Fix the stale parts of `.azure/deployment-plan.md` sections 13 and 14: SHA pin, 13.0 stop rule, "through 0020", 13.3 "nine files and one", step 7 min-replicas and 1 GiB line, missing backup step, Single-mode traffic assumption, rights scoped to the apps only. | Coordinator agent, after the owner's review |
| 17 | Spare details for WO-003826, then receive it into Stock (O23). | Site / owner |
| 18 | P36 deployed evidence (A4) from the deployed build. | Coordinator agent, after deployment |
| 19 | O23 historical-labelling wording and outstanding design passes. | Coordinator agent |
| 20 | Decide whether to rotate the database password that is in git history (`73c7ce6f5:app.yaml`) and tighten the server firewall; rotation means updating the value on both apps (section 5.4). | Owner |
| 21 | Decide whether aimms-dev keeps sharing `inventree-media-vol` with aimms-experimental (section 9.4). | Owner |
| 22 | Go-ahead for the smoke-test writes (8.3, 8.4) and clean-up of test users, grants and tokens afterwards. | Owner (go-ahead), deployer (clean-up) |

---

## Reviewer notes not applied

Of the 21 review corrections, 19 were applied as written. Two were applied only in
the form the source supports:

- **Item 7 (0038 docstring).** The review says only 0035 carries the approval
  wording. The 0038 *module* docstring does read "Local O21 linked-corrective
  origins; no routine outcome backfill.", but its *migration class* docstring says
  "apply only in an approved environment" (without "explicitly"). Section 7.1 quotes
  both docstrings exactly instead of saying 0038 has no such wording.
- **Item 21, second bullet (the 21 October dump).** The earlier review disputed
  the dump's size and provenance. The owner's 21 October update now establishes
  that a read-only `pg_dump -Fc` backup was taken before migration and is held by
  the owner. Sections 2 and 3.5 reflect that update; the coordinator records only
  the file name in the hand-over table.
