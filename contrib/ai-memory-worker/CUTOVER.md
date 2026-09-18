# Memory worker and PostgreSQL deployment preparation

These are review inputs. No resource, role, schedule or feature is activated by
committing them. The implementation period keeps runtime work paused.

## Two worker definitions

Copy `deployments.example.json` to a private review directory. Supply separate
production and experimental source-worker JSON exports, immutable backend image
digests and reviewed user-assigned identities. Source exports must contain only
Key Vault references; inline secret values cause refusal. Do not obtain or print
secret values to make an export pass. Public example markers are intentionally
invalid inputs.

After reviewing the inputs, the offline preparation command is:

```sh
python contrib/ai-memory-worker/render_deployments.py review/deployments.json --output-dir review/prepared-workers
```

It prepares two complete JSON app definitions, retaining reviewed database/cache
connectivity and volume bindings, removing ingress, using the dedicated cluster,
one replica and one consumer, and forcing semantic/compaction/audit flags off.
Output uses new private files and an immutable image digest. It carries only
referenced secrets, removes memory provider API-key environment bindings, and
sets the selected identity client ID. It refuses an admin database login and
requires the production/experimental runtime role separately. TLS verifies the
database hostname and system CA bundle; transaction pooling disables named
server cursors. Review the image's CA bundle and inherited DB options before use.

The source export remains an input requiring human review: a credential hidden
under an arbitrary environment name cannot be recognized reliably by a generic
renderer. Inspect provider URLs, database name, egress hosts, remaining environment
variables and volume contents. Do not carry a credential file containing raw
model keys. The Python egress guard is not a network firewall.

Each selected identity needs reviewed access to its own Key Vault references,
registry image, Azure OpenAI deployments and Content Safety resource. Role grants
are external prerequisites; cloning a definition grants nothing. Keep ingestion
on its existing consumer. Audit Q-cluster selection, memory heartbeat, queue
pressure and inherited Django schedules before enabling any producer.

## PostgreSQL 17 review and connection budget

The maintained CI/compose bump is `pg17-cutover.patch`. Apply only in the same
reviewed cutover change that switches the qualified estate major. It updates the
three pgvector lanes and two compose services; other upstream lanes already use
PostgreSQL 17. It is prepared against current source, not applied here. Use `git apply --unidiff-zero --check`
for validation and retain `--unidiff-zero` when applying after review. Qualify
extension versions and pin reviewed image digests in the frozen release manifest.

The older CR-3 census, capacity and price figures are historical. Record fresh
values before approval; do not reuse the old maximum of 50 or a prospective SKU's
connection limit as an observed budget. Prepare this table for **both databases**:

| Consumer | Reviewed limit to record |
|---|---|
| Web | Active revisions × maximum replicas × measured maximum DB sessions per replica |
| Existing workers | Maximum replicas × measured peak DB sessions per cluster |
| Memory workers | Two isolated apps × measured cluster/process session maxima |
| Qualification | Concurrent model campaign, management command and migration sessions |
| Operations | Backup/restore, monitoring and incident access reserve |
| PostgreSQL | Effective connection capacity after managed-service reservations |
| PgBouncer | Sum of per-database/per-user server pools and reserve pools; distinct from client connections |

Require total server pools plus direct sessions and operational reserve below
the observed effective PostgreSQL capacity. Count zero-weight active revisions.
Use one authorized validation campaign at a time until measured headroom allows
more. Pooling must not hide missing limits or long-held transactions.

`database_roles.sql.in` prepares separate schema, migration and application roles
using quoted psql identifiers and no password literals. It is intended for the
new target: existing role collisions stop rather than modify an unknown role.
Review restored object ownership, extension privileges, database-name/current
connection agreement and the migration role's SET ROLE behavior first. Runtime
roles receive CRUD and sequence usage, not schema creation or admin membership.
Do not run migrations as the runtime role. This script is unapplied and its
privilege matrix must be tested on a disposable restored database.

## Cutover and rollback evidence

1. Record the signed capacity/backup/cost decision and maintenance window.
2. Save private configuration/secret-reference manifests, database dumps, media
   inventories and immutable old image references. Verify backup restoration.
3. Fence web traffic, workers and schedules; keep the restore hold active.
4. Restore to the reviewed PG17 target; qualify pgvector, schema ownership,
   restricted roles, TLS, pooling, migrations and representative data counts.
5. Replay the signed v3 deletion journal before any serving. Keep the hold for
   incomplete proof, missing key continuity or residual cleanup obligations.
6. Deploy reviewed web/ingestion/memory definitions with semantic features off.
   Install memory schedules explicitly only after their isolated qualification.
7. Verify actual cluster identity, grants, budgets, provider behavior and rollback
   artifacts. Enable the approved cohort only after consolidated acceptance.
8. On failure before new writes, return to the preserved source estate and image.
   After new writes, fence both estates and reconcile the write delta before
   rollback. Repointing to an old dump would lose data and is not an automatic
   rollback procedure. Never delete queue rows to drain work.

Keep the old target intact until recovery evidence and the retention decision
permit retirement. Actual provisioning, live configuration, provider grants,
restore drills and human sign-off remain outside this preparation batch.
