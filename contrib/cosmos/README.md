# Cosmos DB container for pumphouse telemetry

This folder holds the **definition** of the container that pumphouse snapshots are read from, plus the
tools to verify and seed it. Creating the container is a deliberate, human act — nothing here creates or
modifies a live Azure resource unless you explicitly ask it to, and nothing here ever grants a role or
stores a credential.

Design rationale, document shape and the parameter mapping live in
`specs/002-cosmos-pumphouse-connector/plan.md`. This file is the operational runbook.

---

## What you are creating and why

One container, `pumphouse_readings`, in your **existing database**.

| Setting | Value | Why |
|---|---|---|
| Partition key | `/station_uuid` then `/hour_bucket` (hierarchical) | Mirrors the Cassandra partition `(entity_uuid, time_period)`. Every query the connector issues is for one station in one hour, so each one stays inside a single partition — no cross-partition fan-out, no `ALLOW FILTERING` equivalent |
| Partition key version | 2 | Required for hierarchical (sub-partitioned) keys |
| Time to Live | **On, no default** (`-1`) | A per-document `ttl` is honoured when present; documents without one live forever. Retention becomes a decision per document, not a property baked into the container |
| Indexing | Only the time/identity fields | We never filter on payload. Indexing `pd` and `dex` would multiply write RU cost for query paths we do not use |

A logical partition here is one station-hour: roughly 720 documents of about 5 KB, so ~3.6 MB against a
20 GB limit. There is no partition-size risk.

**The partition key cannot be changed after creation.** That is the one irreversible decision on this
page; everything else can be adjusted later.

---

## Before you start: which throughput model is the database on?

This decides whether a new container costs anything.

Portal: **Data Explorer → your database → Scale**, or:

```bash
az cosmosdb sql database throughput show \
  --account-name <account> -g <resource-group> --name <database>
```

| What you see | Cost of adding this container |
|---|---|
| The account is **serverless** (no throughput setting at all) | **Nothing extra.** You pay per request and per GB stored |
| The database has **shared throughput** ("Provisioned throughput" set on the database) | **Nothing extra**, provided you do *not* tick "Provision dedicated throughput for this container" — it shares the database's RU/s |
| Throughput is **per container** (the command above errors, and each container has its own Scale setting) | A new container needs its **own** allocation: 400 RU/s minimum manual, or autoscale from 1000 RU/s max. That is a real monthly line item |

If you are on per-container throughput and nothing else is going to use the empty `/maintenance_pk`
container, **delete that one and create this one instead** — it is empty, so nothing is lost and you add no
new billable container.

Check current prices for your region on the Azure pricing calculator; they vary by region and change over
time, so no figure is quoted here.

---

## Option A — Portal

1. **Data Explorer → New Container**.
2. **Database id**: select **Use existing** and pick your current database. Do not create a new one — a
   database is only a namespace and carries no schema.
3. **Container id**: `pumphouse_readings`
4. **Partition key**: `/station_uuid`
5. Tick **“My partition key is larger than 101 bytes or hierarchical”**, then **Add hierarchical partition
   key** and enter `/hour_bucket` as the second level.
   *If you do not tick this box you get a single-level key and cannot change it afterwards.*
6. **Container throughput**: if the dialog offers “Provision dedicated throughput for this container”,
   leave it **unticked** so the container shares the database's throughput.
7. Create.
8. Select the new container → **Settings → Time to Live → On (no default)** → Save.
9. Select **Settings → Indexing Policy**, replace the contents with
   `schema/pumphouse_readings.container.json` → `indexingPolicy` (the inner object only), and Save.

## Option B — Azure CLI

```bash
ACCOUNT=<account-name>
RG=<resource-group>
DB=<existing-database-id>

az cosmosdb sql container create \
  --account-name "$ACCOUNT" -g "$RG" --database-name "$DB" \
  --name pumphouse_readings \
  --partition-key-path /station_uuid /hour_bucket \
  --partition-key-version 2 \
  --ttl -1 \
  --idx @contrib/cosmos/schema/pumphouse_readings.indexing.json
```

`--ttl -1` means *TTL enabled with no default expiry* — not "expire immediately" and not "off". The
`--idx` file is the `indexingPolicy` object extracted from the container definition:

```bash
python3 -c "import json;print(json.dumps(json.load(open('contrib/cosmos/schema/pumphouse_readings.container.json'))['indexingPolicy']))" \
  > contrib/cosmos/schema/pumphouse_readings.indexing.json
```

---

## Verify it

The verifier compares a live container against `schema/pumphouse_readings.container.json` and exits
non-zero on drift. **The offline mode needs no Azure access at all**, which is the one to reach for first:

```bash
az cosmosdb sql container show \
  --account-name "$ACCOUNT" -g "$RG" --database-name "$DB" \
  --name pumphouse_readings \
  --query "{pk:resource.partitionKey, ttl:resource.defaultTtl, indexing:resource.indexingPolicy}" \
  > /tmp/live.json

python3 contrib/cosmos/provision.py --from-json /tmp/live.json --database "$DB"
```

A container created from the definition prints:

```text
aimms/pumphouse_readings matches the expected definition.
```

and one that drifts names each problem, for example:

```text
  - partition key paths are ['/station_uuid'], expected ['/station_uuid', '/hour_bucket'];
    this is immutable, so the container has to be recreated to change it
  - defaultTtl is switched off, expected -1 so that TTL is enabled with no default
  - payload is being indexed: excludedPaths has no /* entry
```

Reading the container directly (`--live`) needs a **data-plane** role. Owning the account in the portal
does not grant one — Cosmos separates control plane from data plane — so `--from-json` is usually the
quicker route.

### Environment

The application reads these; nothing is hard-coded and none of them is a secret:

```bash
export INVENTREE_COSMOS_ENDPOINT="https://<account>.documents.azure.com:443/"
export INVENTREE_COSMOS_DATABASE="<existing database id>"
export INVENTREE_COSMOS_CONTAINER="pumphouse_readings"
```

The dev containers already set all three, pointing at the local emulator (see below). Override them
to work against a real account.

---

## Access for the application (later, not needed to create the container)

The connector reads with Entra ID and a **read-only data-plane role**, so the application cannot write to
the container even if it is compromised:

```bash
az cosmosdb sql role assignment create \
  --account-name "$ACCOUNT" -g "$RG" \
  --role-definition-id 00000000-0000-0000-0000-000000000001 \
  --principal-id <object-id-of-the-app-identity> \
  --scope "/dbs/$DB/colls/pumphouse_readings"
```

Scoping to the container rather than `/` keeps the blast radius at one container.

Seeding documents **writes**, so it cannot be done with that role — seeding is run by a human with their
own credentials, deliberately.

Never put an account key in this repository, in `data/config.yaml`, in a commit message or in chat.

---

## Local emulator (no Azure account needed)

This is the normal way to work on the connector. Nothing below touches Azure, costs anything, or
needs a role to be granted — which matters, because the data-plane role on the real account is still
outstanding and a test suite that can reach production data is one that can damage it.

```bash
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml --profile cosmos up -d
```

The emulator is behind the `cosmos` profile, so a plain `docker compose up` does not start it and the
ordinary dev stack stays as light as it was. Wait for it to report healthy — it takes roughly 25
seconds, and the image runs its own readiness probe that compose is wired to:

```bash
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml --profile cosmos ps
# ... Up 2 minutes (healthy)
```

Then create the container and fill it. Both scripts pick up their coordinates from the environment
already set on the dev containers, so there are no flags to remember beyond `--emulator`:

```bash
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml exec inventree-dev-server \
    python contrib/cosmos/provision.py --create --emulator
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml exec inventree-dev-server \
    python contrib/cosmos/seed.py --emulator
```

`--create` is refused against anything but an emulator. A partition key cannot be changed after
creation, so a script that quietly "fixed" a live container would be destroying data rather than
converging on a schema.

### There is no certificate to install

The classic emulator image serves HTTPS with a self-signed certificate that every client then has to
be taught to trust, and publishes **amd64 only** — so on an Apple Silicon machine it also runs under
emulation. The image used here is the vNext one, which ships arm64 alongside amd64 and serves **plain
HTTP** on 8081. The endpoint is therefore `http://cosmos-emulator:8081` from inside the compose
network, or `http://localhost:8081` from the host. Writing `https` there fails in a way that looks
like a certificate problem and sends you off installing a root CA you do not need.

The image is pinned to a dated tag rather than `vnext-preview`: a floating preview tag can change
overnight and turn an unrelated pull request red.

### The emulator key is not a secret

It is a constant published in Microsoft's own documentation and it opens a throwaway local container.
It is still passed as an environment variable (`COSMOS_EMULATOR_KEY`) rather than written into a
config file, because that is the only path the connector will accept a key through at all. Against a
real endpoint a key is **refused outright** — that is what keeps the Data Reader role, rather than our
own code, the thing enforcing read-only.

### Starting over

Storage is deliberately ephemeral, so every `up` starts empty. That is what makes a seeded test
reproducible, and re-seeding takes about a second:

```bash
docker compose --project-directory . -f contrib/container/dev-docker-compose.yml --profile cosmos down
```

---

## Troubleshooting

### “Authorization header doesn't confirm to the required format” when saving in Data Explorer

Not a problem with the JSON. Data Explorer edits container settings over the **data plane**, and to do
that it first tries to fetch an account key. The **Cosmos DB Operator** role deliberately cannot read
keys, so the request goes out without a usable credential and the service rejects the header.

Apply the indexing policy through the **control plane** instead, which is what that role is for:

```bash
az cosmosdb sql container update \
  --account-name "$ACCOUNT" -g "$RG" --database-name "$DB" \
  --name pumphouse_readings \
  --idx @contrib/cosmos/schema/pumphouse_readings.indexing.json
```

`--idx` takes the indexing policy **on its own**, which is why `schema/pumphouse_readings.indexing.json`
exists alongside the full container definition. A test asserts the two stay identical.

The same reasoning applies to the portal: pasting the *whole* container definition into the Indexing
Policy editor will not work, because that editor expects only the `indexingPolicy` object.

### `AuthorizationFailed` on `Microsoft.DocumentDB/databaseAccounts/read`

Check the account name against your role assignment — they are easy to confuse when accounts differ by a
single character:

```bash
az role assignment list --assignee <your-object-id> --all \
  --query "[].{role:roleDefinitionName, scope:scope}" -o table
```

The `scope` is authoritative for which account you can actually administer.

### Reading documents fails even though the CLI works

Control plane and data plane are separate in Cosmos. **Cosmos DB Operator** manages containers but cannot
read a single document; that needs a data-plane role assignment (see above). This is a feature, not an
obstacle: the application's identity gets read-only *data* access and no ability to alter the container,
while an administrator can manage the container and not read the data.

---

## Files here

| File | Purpose |
|---|---|
| `schema/pumphouse_readings.container.json` | The container definition: partition key, TTL and indexing policy. Contains **no** database id, account name or credential — those are deployment configuration |
| `schema/pumphouse_readings.indexing.json` | The indexing policy alone, for `az ... --idx @file`. Kept identical to the definition by a test |
| `provision.py` | Verifies a live container against that definition; `--create` works only against the emulator |
| `test_provision.py` | Offline tests for the comparison logic: `python3 -m unittest discover -s contrib/cosmos -p 'test_*.py'` |
| `seed.py` | Validates the §3.1 invariants, then upserts the manual inserts; `--dry-run` writes nothing |
| `samples/ph3_snapshots.json` | Three pilot documents across two hour buckets, including an `I → R` transition |
| `test_seed.py` | Offline tests for the seeder's validation and derived fields |

## Scheduled polling (T9)

`assets.tasks.poll_cosmos_pumphouse_sources` runs every minute through Django-Q2.
`AIMMS_COSMOS_PUMPHOUSE_ENABLED` defaults to false. Leave it off until T11 activation
links each `IngestionCheckpoint.station` to its registered pumphouse and creates approved
signal bindings. The checkpoint's `station_uuid` is the station's **source entity UUID**;
it is not the registry's local UUID. Migration `0013_station_poll_progress` leaves old
links null rather than guessing ownership. No running database is changed by the source
code migration alone.

Each station gets up to 20 seconds and 200 documents (`max_docs_per_poll` may lower that
cap), within a 50-second sweep. Least-recently attempted stations go first. A two-minute
lease prevents overlapping scheduled polls, and per-station timestamps/error codes are
visible in the checkpoint admin. Cosmos sources created without an explicit freshness
threshold default to 300 seconds; existing source configuration is preserved.

Requests use bounded timeouts and no SDK retries. The next scheduled sweep retries failures.
These are cooperative budgets: an in-flight HTTP or credential operation must return
before the worker checks time again. Empty scan ranges advance `scan_until`, independently
of the last accepted sample, with a five-minute overlap for delayed documents. Documents
older than the overlap or behind the last accepted sample require a separate backfill
policy; this poller maintains latest state rather than importing historical series.

Errors use fixed codes: `AUTH`, `NOT_FOUND`, `THROTTLED`, `NETWORK`, `CONFIG`, `SNAPSHOT`,
`INGEST`. One station's failure does not mark the whole account failed. Every snapshot's
batches and accepted checkpoint commit together, so a batch failure leaves no partial
snapshot in the cache. Check `HANDOVER.md` for remaining activation and deployment work.

## Offline dump import (T10)

From the backend directory, run:

```bash
python manage.py import_pumphouse_dump /path/to/dump.json --station 17 --source 1 --dry-run
python manage.py import_pumphouse_dump /path/to/dump.json --station 17 --source 1
```

IDs must name your local registered station and configured Cosmos source. Existing signal
bindings determine which values are accepted; the command does not approve points or create
bindings. It accepts Cosmos document arrays, Cassandra rows with `data1` objects/text, and
this directory's `{station_uuid, snapshots}` sample envelope. Every row must identify the
selected source station. Files are limited to 8 MiB and 2000 rows. The entire file commits
or rolls back together; pure replays and dry runs leave database values unchanged. Cosmos
is never contacted and the live polling checkpoint is never advanced by this command.

## Station activation (T11)

After applying migration `0014_station_activation`, a deployment administrator must assign
`HealthSource.client` in the Health Source admin and configure the account's `stations` with
its source entity UUIDs. Legacy sources remain unassigned until this is done. Connection
configuration and `secret_ref` are never returned by the registry API.

1. Review the station dictionary and approve only verified mappings and units.
2. `GET /api/assets/registry/<station pk>/activate/?source=<source pk>` returns status and
   `preview.source_hash`, without contacting Cosmos.
3. `POST` that URL with `{"source": <pk>, "source_hash": "<preview hash>"}`. The actor needs
   station Client scope and work-order add/change permission. A changed review/configuration
   or in-progress station poll requires refreshing/retrying. No thresholds are inferred.
4. Enable `AIMMS_COSMOS_PUMPHOUSE_ENABLED` only after deployment configuration and read access
   have been verified. Activation alone does not turn on the global scheduler flag.
5. `DELETE` with the same preview-shaped body deactivates this station/source (change permission).
   It preserves the accepted cursor and manual bindings, and removes managed bindings/state.

New checkpoints start five minutes before activation. Retries and reactivation retain existing
progress. Revoking approval or changing measurement meaning disables the corresponding binding
and removes cached state; refresh activation after review. Confirmed thresholds are preserved
only while the measurement meaning is unchanged. Status includes bound/unbound counts, last
poll time and a fixed error code; it distinguishes activation from globally enabled polling.

## Emulator integration check (T13)

With the local emulator running and its public key in `COSMOS_EMULATOR_KEY`, run:

```bash
INVENTREE_TEST_COSMOS_EMULATOR=1 python src/backend/InvenTree/manage.py test \
  machine_health.tests.test_cosmos_emulator --keepdb --noinput
```

The endpoint defaults to `http://localhost:8081`. `INVENTREE_TEST_COSMOS_ENDPOINT` can change
its port, but the test accepts only loopback HTTP endpoints. It creates a uniquely named
test database, uses the checked-in partition/index definition, and deletes that database at
cleanup. The test covers a capped poll and resume, two-hour history, station isolation,
scheduler ingestion and replay. Ordinary test runs skip it unless explicitly enabled.

`.github/workflows/cosmos_integration.yaml` starts a disposable emulator pinned to the
locally tested image digest, waits on port 8080's `/ready` endpoint and runs this check.
It reuses the public emulator key already provided in the development Compose file;
no Azure subscription or credentials are required. This verifies transport and query
behaviour, not production RBAC, networking or plant data completeness.
