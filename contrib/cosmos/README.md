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

```bash
az cosmosdb sql container show \
  --account-name "$ACCOUNT" -g "$RG" --database-name "$DB" \
  --name pumphouse_readings \
  --query "{pk:resource.partitionKey, ttl:resource.defaultTtl, indexing:resource.indexingPolicy}"
```

Expect:

- `pk.paths` = `["/station_uuid", "/hour_bucket"]`, `pk.kind` = `MultiHash`, `pk.version` = `2`
- `ttl` = `-1`
- `indexing.excludedPaths` contains `/*`

Paste that output back and the connector work can proceed against a container we know the shape of.

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

## Files here

| File | Purpose |
|---|---|
| `schema/pumphouse_readings.container.json` | The container definition: partition key, TTL and indexing policy. Contains **no** database id, account name or credential — those are deployment configuration |

Still to come (tickets T3 and T5): `provision.py` to verify a live container against this definition, and
`seed.py` plus sample documents for the manual inserts.
