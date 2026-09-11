# Sprint Plan: Azure Cosmos DB schema + read-only pumphouse connector

**Feature Branch**: `inventTree-aniket` (PR to `IOT` at sprint end)
**Created**: 2026-09-11 · **Revised**: 2026-09-11 (decisions D2–D5 answered)
**Status**: Draft — open items in §8
**Input**: Cassandra `iwm_data_YYYYMM` schema (confirmed), PH_3 pilot excerpt, basic-params list, existing
`machine_health` connector framework and `assets` equipment registry.

> **Scope decision (2026-09-11).** Cassandra → Cosmos **migration is out of this sprint**. We create the
> Cosmos schema and hand-insert a handful of timestamped documents, then build the read-only connector
> against that seeded data. The migration/CDC job is a later sprint and must not constrain the schema
> beyond keeping the original `data1` text intact.

---

## 1. Where we are (research summary)

| Area | State | Evidence |
|---|---|---|
| Source schema | **Confirmed.** Cassandra monthly tables `cass_business_data_klsw.iwm_data_YYYYMM`, `PRIMARY KEY ((parent_entity_uuid, location_type, component_type, time_period, event_value_type), sub_time_period, entity_uuid)`. `time_period` is **text** (epoch-ms string), `sub_time_period` bigint, `data1` text JSON, `data2` null. One partition type holds basic (`pd`) + extension (`dex`). | user-supplied DDL; `contrib/pump-cassandra/PH_3.pilot-excerpt.json` |
| Registry (Gates 2–4) | **Merged.** Stations/pumps (`AssetMachine.asset_type`), `AssetComponent`, `DictionaryPoint` with `path`, `match_method`, review flow; preview→import hash-locked. | `assets/models.py`, `assets/registry_models.py`, `assets/registry_api.py` |
| Connector framework | **Exists, empty.** `HealthConnector` ABC (`check`, `read_latest`, `read_window`), `register()`, `Reading` dataclass; `HealthSource(connector_type, secret_ref, config)`; `MachineSignalBinding(external_key)`; `MachineSignalState` latest cache; `ingest_readings()` (batch ≤ 500, clock-skew ≤ 300 s, replay guard). No implementations. | `machine_health/connectors/base.py`, `assets/health_models.py`, `machine_health/services/ingestion.py` |
| Scheduling | `@scheduled_task(ScheduledTask.MINUTES, n)` via django-q2. None in `assets/`. | `InvenTree/tasks.py:541-617`, `machine/tasks.py:15` |
| Config | `get_setting('INVENTREE_X', 'x', default)` env → `config.yaml` → default. | `InvenTree/config.py:275` |
| Deps | `azure-identity` present; **no `azure-cosmos`**, no cassandra driver. | `src/backend/requirements.in` |
| Registry ↔ live data | **Not linked.** Approved `DictionaryPoint`s never become `MachineSignalBinding`s; UI banner says "Mapping approval does not enable live ingestion". | `EquipmentRegistry.tsx:373` |
| Validator bug | `assets/registry.py:285-294` requires `type(time_period) is int`; real column is text. Must coerce. | `assets/registry.py` |

### 1.1 Basic-params ↔ `data1` key mapping (confirmed from user list + draft mapping)

Applicability (D3, answered): a basic param is recorded **at the level it applies to** — station-level
params sit at the document root, pump-level params sit inside `pd.P<n>`. The same param may appear at both
levels (e.g. status `st`). The normaliser derives the JSON-pointer `external_key` from where the key is
found, so no per-param level table is hard-coded.

| Basic param | Key | Level | Type | Seen in PH_3 excerpt |
|---|---|---|---|---|
| Event Generation Timestamp | `egt` | station | epoch ms (int) | yes |
| Expiration Timestamp (egt + expiry) | `ext` | station | epoch ms (int) | yes (egt + 300 000) |
| Surge Pool Level | `sl` | station | float | yes |
| Running Status | `st` | station **and** `pd.P<n>` | code (`I`, …) | yes (both levels) |
| Pump Operation Details | `pd` | station (map `P1..P14`) | object | yes (only `st` populated) |
| Data Source (SCADA device) | `dsc` | station | int code (24) | yes |
| Source Text | `sr` | station | `"SCADA"` | yes |
| Total Discharge Value | `dv` | station and/or `pd.P<n>` | float | **absent** — confirm key |
| Input Power (MW) | `pmw` | station and/or `pd.P<n>` | float | **absent** — confirm key |
| Input Power (MVar) | `pmvar` | station and/or `pd.P<n>` | float | **absent** — confirm key |
| Number of Pumps Running | `pc` | station | int | **absent** — confirm key |
| Extension / SCADA payload | `dex` | station (tag → string, tags are `PUMP<n>_…` prefixed) | map | yes (17 tags incl. `ID`, `TIMESTAMP`) |

The four unconfirmed keys are carried in the Cosmos schema as optional fields; ingestion treats a missing
key as "not reported", never as zero.

---

## 2. Target architecture

```
Cassandra (klsw)        Azure Cosmos DB for NoSQL              InvenTree / AIMMS
iwm_data_YYYYMM  ╌╌►   db: iwm                        ┌────────────────────────────────────┐
(migration: LATER       ├─ pumphouse_readings         │ machine_health.connectors.cosmos_  │
 sprint, dashed)        │   (time-series, per-doc ttl)◄│   pumphouse.CosmosPumphouseConnector│
                        └─ pumphouse_latest (DEFERRED)│   .check/.read_latest/.read_window │
manual seed ──────────►  (hand-inserted docs, §3.5)   │ assets.tasks.poll_cosmos_sources    │
(this sprint)                                          │   → ingest_readings() → SignalState│
                                                       │ registry: approved DictionaryPoint │
                                                       │   → MachineSignalBinding (bridge)  │
                                                       └────────────────────────────────────┘
```

Principles carried over from the Cassandra README (Gate 5): read-only, bounded queries (single logical
partition, paged), explicit checkpoints, one normalization path for live and dump import, raw history stays
external, no credentials in DB rows.

### 2.1 API choice: Cosmos DB for NoSQL (recommended) vs Cosmos DB for Apache Cassandra

| | NoSQL API (`azure-cosmos`) | Cassandra API (`cassandra-driver`) |
|---|---|---|
| Schema reuse | Redesign (below) — no monthly tables, hierarchical PK | Copy DDL verbatim, keep monthly tables |
| Query model | SQL over one partition, `ORDER BY` on indexed path, change feed | CQL slice on clustering cols; **no change feed** |
| Latest read | 1-RU point read from `pumphouse_latest` | `LIMIT 1` slice per hour |
| Auth | Entra ID RBAC (`azure-identity` already present) | Key/password only |
| Local dev | Linux emulator (`mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator`) | Emulator Cassandra endpoint (Windows only) |
| SDK maturity / typing | Good, sync + async | Heavy native deps, licence/CI friction |

**Recommendation: NoSQL API.** With the migration deferred (D2) nothing forces CQL compatibility — we are
hand-inserting documents, so the "copy the DDL verbatim" advantage of the Cassandra API disappears while its
costs (key-only auth, no change feed, heavy driver) remain. NoSQL also removes the monthly table fan-out and
gives RBAC plus cheap point reads. Revisit only if the future migration job must keep writing CQL.

---

## 3. Cosmos DB schema (NoSQL API)

### 3.1 Database `iwm`, container `pumphouse_readings`

> **Existing container (checked 2026-09-12).** The account already has a container whose partition key is
> **`/maintenance_pk`, non-hierarchical, and it holds no data.** That key was created for a different
> purpose and cannot carry a station-hour partition as-is. A container's partition key is immutable, so
> the options are:
>
> | Option | What it means | Verdict |
> |---|---|---|
> | **A — dedicated container** (recommended) | Create `pumphouse_readings` with hierarchical PK `/station_uuid` + `/hour_bucket`. The existing empty container stays free for its intended maintenance use. | Cleanest; costs one container, loses nothing because the existing one is empty |
> | **B — reuse the existing container** | Write a synthetic `maintenance_pk = "<station_uuid>|<hour_bucket>"`. Every query we issue is already station+hour, so each one stays single-partition. | Works, but a field named `maintenance_pk` holding telemetry identity misleads the next reader, and telemetry then shares a container with unrelated documents |
> | C — change the key in place | The portal's "Change partition key" copies into a new container anyway | Pointless here: no data to preserve |
>
> **The connector does not hard-code either choice.** `HealthSource.config` carries
> `partition_key_paths` (e.g. `["/station_uuid", "/hour_bucket"]` or `["/maintenance_pk"]`) and
> `partition_key_mode` (`hierarchical` | `composite`), and the document builder and every query derive the
> key from that. Option B therefore remains available without a code change if creating a container turns
> out to be restricted.

- **Partition key (hierarchical, 2 levels)**: `/station_uuid`, `/hour_bucket`
  - mirrors Cassandra `(entity_uuid, time_period)`; one station-hour ≈ 720 docs × ~5 KB ≈ 3.6 MB
    (limit 20 GB per logical partition, so headroom is > 5 000×).
  - `hour_bucket` stored as **string** epoch-ms exactly like Cassandra `time_period` text — no
    re-interpretation on migration.
- **`id`**: `str(sub_time_period)` — unique within the partition (one sample per ms per station).
- **TTL (D5, answered)**: container `defaultTtl: -1` (TTL **enabled, no default expiry**) so a per-document
  `ttl` integer is honoured when present and documents without one live forever. The seeded pilot documents
  carry no `ttl`; a retention policy can later be applied per document without a schema change.
- **Raw payload (D5, answered)**: `data1_raw` holds the **verbatim `data1` text** exactly as Cassandra
  stores it, alongside the parsed fields. Parsed fields are a convenience/index layer; `data1_raw` is the
  record of truth and what `payload_hash` is computed over. Nothing in the connector may depend on a parsed
  field that cannot be re-derived from `data1_raw`.
- **Indexing policy** (RU control — we never filter on payload):
  ```json
  {
    "indexingMode": "consistent",
    "includedPaths": [
      {"path": "/station_uuid/?"}, {"path": "/hour_bucket/?"},
      {"path": "/sub_time_period/?"}, {"path": "/egt/?"}, {"path": "/month/?"}
    ],
    "excludedPaths": [
      {"path": "/*"}, {"path": "/pd/*"}, {"path": "/dex/*"},
      {"path": "/data1_raw/?"}, {"path": "/\"_etag\"/?"}
    ]
  }
  ```
- **Document shape** (parsed `data1` + selectors + verbatim payload):
  ```jsonc
  {
    "id": "1752854398616",
    "station_uuid": "…pumphouse uuid…",          // = Cassandra entity_uuid
    "hour_bucket": "1752850800000",              // = time_period (text)
    "sub_time_period": 1752854398616,            // bigint sample ts
    "month": "202507",                           // derived from hour_bucket in UTC (D4)
    "parent_entity_uuid": "…", "location_type": "PUMP_HOUSE",
    "component_type": 65, "event_value_type": 41,
    "sr": "SCADA", "dsc": 24, "st": "I",
    "egt": 1752854398000, "ext": 1752854698000,
    "sl": 132.45, "dv": null, "pmw": null, "pmvar": null, "pc": null,
    "pd": {"P1": {"st": "I"}, "…": {}, "P14": {"st": "I"}},
    "dex": {"COMMAN_FORBAY_LEVEL": "…", "PUMP3_EXCITATION_FLD_VLTG_PROCESS_VALUE": "…"},
    "data2": null,
    "data1_raw": "{\"sr\":\"SCADA\",\"st\":\"I\",…}",  // verbatim Cassandra data1 text (D5)
    "ingested_at": "2026-09-11T09:00:00Z",       // writer/seed receipt time, UTC
    "payload_hash": "sha256:…"                   // over data1_raw
    // "ttl": 34560000                           // optional, omitted for pilot docs
  }
  ```
- **Invariants enforced by the seeder + validated by the connector**: `hour_bucket <= sub_time_period <
  hour_bucket + 3_600_000`; `month == datetime.utcfromtimestamp(int(hour_bucket)/1000).strftime('%Y%m')`
  — **UTC** (D4), matching the legacy table suffix; `json.loads(data1_raw)` must reproduce the parsed fields.

### 3.2 Container `pumphouse_latest` — **deferred**

Requires a writer or change-feed function to maintain it; with hand-seeded data there is nothing to
maintain it, so it is **not created this sprint**. `read_latest` uses query A (§3.3) against the current and
previous hour bucket. The container definition is still written to `contrib/cosmos/schema/` so the later
migration sprint can enable it without a design round.

### 3.3 Queries the connector issues (all single-partition, parameterised, paged)

```text
-- A. latest, PK = (station, hour)
SELECT TOP 1 * FROM c WHERE c.station_uuid = @s AND c.hour_bucket = @h ORDER BY c.sub_time_period DESC
-- B. window / incremental poll slice within one hour bucket
SELECT * FROM c WHERE c.station_uuid = @s AND c.hour_bucket = @h
  AND c.sub_time_period >= @from AND c.sub_time_period < @to ORDER BY c.sub_time_period ASC
-- C. (later) change feed on the container, continuation token per source — not used this sprint
```
No cross-partition queries, no `ALLOW FILTERING` equivalent (`enable_cross_partition_query=False`).

### 3.4 RU budget (per station)

Writes: 720 docs/h × ~10 RU ≈ 2 RU/s. Reads: poller pulls ≤ 100 docs/min ≈ 10 RU/s worst case; latest point
read 1 RU. Serverless or 400 RU/s autoscale floor is sufficient for tens of stations.

### 3.5 Provisioning and manual seeding artefacts

- `contrib/cosmos/schema/iwm.pumphouse_readings.json` — container definition (PK paths, indexing,
  `defaultTtl: -1`).
- `contrib/cosmos/schema/iwm.pumphouse_latest.json` — written now, **created later** (§3.2).
- `contrib/cosmos/provision.py` — **verify/diff by default** against the existing account (D12): asserts
  partition-key paths, `defaultTtl` and indexing policy match the schema files and reports drift. `--create`
  creates a missing database/container (used for the emulator); `--emulator` targets the local endpoint.
  It **never** grants roles or writes data.
- `contrib/cosmos/seed.py` — **the manual-insert tool for this sprint.** Reads a JSON array of snapshots
  (or `PH_3.pilot-excerpt.json` Cassandra rows), validates the §3.1 invariants, computes `month`,
  `payload_hash` and `data1_raw`, and upserts. Flags: `--station-uuid` (writes `station_uuid`,
  `entity_uuid` and `parent_entity_uuid` from the same value, per D13), `--from`/`--count`/`--every-ms`
  (synthesise a timestamp ladder from one template snapshot), `--dry-run` (prints documents, writes
  nothing).
- `contrib/cosmos/samples/ph3_snapshots.json` — the hand-authored pilot documents: a few timestamps spanning
  **two hour buckets** (so bucket enumeration and the `read_latest` fallback are exercised), station-level
  and pump-level values, the full `dex` tag set (D8), a `pd.P<n>.st` transition `I → R` (D10), and one
  document carrying `dv`/`pmw`/`pmvar`/`pc` marked `"unconfirmed": true` until D7 lands.
- `contrib/cosmos/README.md` — schema rationale, the §1.1 mapping table, Annex A units, the Entra ID role
  assignment needed (D6), emulator instructions, and the exact commands to verify and seed.


---

## 4. Connector design (`machine_health/connectors/cosmos_pumphouse.py`)

### 4.1 Registration and configuration

- `@register class CosmosPumphouseConnector(HealthConnector): key = 'cosmos_pumphouse'`
- `HealthSource.source_type = SCADA`, `connector_type = 'cosmos_pumphouse'`
- `HealthSource.config` (non-secret): `{"endpoint": "https://<acct>.documents.azure.com", "database": "iwm",
  "readings_container": "pumphouse_readings", "latest_container": null,
  "stations": ["<station_uuid>", …], "poll_seconds": 60, "max_docs_per_poll": 200}`
  (`latest_container` stays `null` until §3.2 is enabled.)
- `HealthSource.secret_ref`: names the deployment identity/secret entry, resolved at call time only. Per D6
  the connector authenticates with `DefaultAzureCredential` (managed identity in Azure, developer login
  locally) and the AIMMS principal holds the built-in **Cosmos DB Data Reader** data-plane role — a role
  that cannot write, so read-only is enforced by Azure and not merely by our code. `secret_ref` names an env
  var holding the emulator key only in local development.
- Global kill-switch `AIMMS_COSMOS_PUMPHOUSE_ENABLED` via `get_boolean_setting` (default `False`), same
  pattern as `AIMMS_MACHINE_AI_READ_ENABLED`.

### 4.2 Methods

| Method | Behaviour |
|---|---|
| `check()` | `read_container_properties` on readings container; returns `(ok, code)` with codes from a fixed set: `AUTH`, `NOT_FOUND`, `THROTTLED`, `NETWORK`, `OK`. Never logs endpoint or message. |
| `read_latest(external_keys)` | Query A per station in `config.stations` against the current hour bucket, then the previous one if empty (covers the bucket boundary and sparse seeded data). Flatten → filter to requested keys. |
| `read_window(external_key, start, end, max_samples)` | `bounded_window()`; enumerate hour buckets, run query B per bucket with `max_item_count=100`, stop at `max_samples`; **never** rounds timestamps. |
| `poll(checkpoint)` *(new, optional on ABC)* | Query B from the stored `(hour_bucket, sub_time_period)` checkpoint forward, walking hour buckets up to now. Change feed is a later optimisation and is **not** required for hand-seeded data. Returns `(readings, next_checkpoint)`. |

### 4.3 One normalisation path: `machine_health/connectors/pumphouse_payload.py`

`flatten_snapshot(doc) -> list[Reading]` — pure function, no I/O, reused by connector **and** by a
`import_pumphouse_dump` command (Gate 5 "reuse one normalization path").

- Input is the parsed document, but the function re-parses `data1_raw` when present and **fails the
  snapshot** if the parsed fields disagree with it — the verbatim payload stays authoritative (D5).
- `external_key` == `DictionaryPoint.path` (JSON-pointer), derived from where the key actually sits, so
  station-level and pump-level params fall out of the same walk (D3): `/sl`, `/st`, `/pc`, `/pmw`,
  `/pd/P3/st`, `/pd/P3/pmw`, `/dex/PUMP3_EXCITATION_FLD_VLTG_PROCESS_VALUE`, `/dex/COMMAN_FORBAY_LEVEL`.
  This is the contract that lets approved registry points become bindings (§5) without a second mapping
  table. Readings carry the owning machine implicitly through the binding, so a pump-level key binds to the
  pump `AssetMachine` and a station-level key to the pumphouse.
- `observed_at = sub_time_period` (fallback `egt`), interpreted as **epoch ms UTC** (D4);
  `sequence = sub_time_period`.
- Type coercion: `dex` strings → `float` when parseable, else kept as string with `quality='uncertain'`;
  non-finite / empty → `quality='bad'`, value `None`. Status codes (`st`) are passed through as the raw
  letter with the D10 vocabulary (`I` Idle, `R` Running) applied for display only; an unrecognised code is
  `quality='uncertain'`, never silently mapped.
- Skips metadata keys `dex.ID`, `dex.TIMESTAMP`, and envelope keys `sr, dsc, ext, egt` unless a binding
  explicitly asks for them.
- Bounds: `MAX_VALUE_BYTES` (2048) respected; one snapshot ≈ 40 readings, well under
  `MAX_READINGS_PER_BATCH` (500).

### 4.4 Checkpoints

New model `assets.IngestionCheckpoint(source FK, station_uuid, hour_bucket str, sub_time_period bigint,
continuation_token text, updated_at)` with unique `(source, station_uuid)`. Rejects backwards moves.
`continuation_token` is reserved for the later change-feed mode and stays empty this sprint. Replaces the
ad-hoc "data/checkpoints" idea; survives worker restarts and is visible in admin.

### 4.5 Scheduled task (`assets/tasks.py`)

```python
@scheduled_task(ScheduledTask.MINUTES, 1)
def poll_cosmos_pumphouse_sources():
    if not settings.AIMMS_COSMOS_PUMPHOUSE_ENABLED: return
    for source in HealthSource.objects.filter(active=True, connector_type='cosmos_pumphouse'):
        connector = get_connector(source); readings, cp = connector.poll(checkpoint_for(source))
        ingest_readings(source, [r.as_dict() for r in readings]); save(cp)
        # on exception: record_source_error(source, code)
```
Per-source time budget (≤ 20 s) and doc cap so one slow account cannot starve the worker.

---

## 5. Registry → live bridge (closes the "offline registry only" gap)

- **API**: `POST /api/assets/registry/<pk>/activate/` with `{"source": <HealthSource id>}` — for every
  `DictionaryPoint(status='approved')` of the station create/refresh `MachineSignalBinding(machine=point.machine,
  source=source, external_key=point.path, display_name=point.display_name, unit=point.unit)`. Idempotent,
  hash-locked like import. Rejected/unresolved points are never bound.
- **UI** (`EquipmentRegistry.tsx`): new "Live source" card on the *Dictionary and review* tab: pick
  `HealthSource`, show bound/unbound counts, last poll time, last error code; banner text becomes
  conditional.
- Deactivation removes bindings for that station only.

---

## 6. Fixes folded into this sprint (from schema confirmation)

1. `assets/registry.py:285-294` — coerce `time_period` from str/int (`int(str)`), keep half-open bucket
   check; when a file contains rows for several `entity_uuid`s, **filter** to the station instead of
   rejecting the file (Cassandra clustering puts `entity_uuid` after `sub_time_period`, so an hour slice is
   naturally multi-station).
2. `contrib/pump-cassandra/README.md` — replace "physical primary key unknown" with the confirmed DDL,
   monthly-table note, `time_period` text caveat; mark Gate 1 done, Gate 5 → "superseded by the Cosmos
   connector, see `specs/002-cosmos-pumphouse-connector/plan.md`; a Cassandra reader is only needed if the
   deferred migration job lives in this repo".
3. `PH_3.mapping.draft.json` — `source_layout.hour_bucket_type: "text"`, add `month_table_pattern` and
   `month_table_timezone: "UTC"` (D4).
4. Tests for string `time_period` and multi-station slice in `assets/test_registry.py`.

---

## 7. Sprint backlog (2 weeks, 1 dev)

Estimates in ideal hours. DoD for every ticket: unit tests, `prek run --files …` clean, `ty` clean on
touched files, no secrets in code/fixtures, commit on `inventTree-aniket`.

### Week 1 — schema, dependency, seed, normalisation

| # | Ticket | Est | Depends |
|---|---|---|---|
| T1 | README/mapping update + `registry.py` text `time_period` coercion + multi-station slice filter + tests (§6) | 4 | — |
| T2 | Add `azure-cosmos` to `requirements.in` (`azure-identity` already present), pip-compile, CVE check, container image rebuild | 2 | — |
| T3 | `contrib/cosmos/schema/*.json`, `provision.py` (verify/diff against the existing account, `--create`/`--emulator`), `README.md` (§3) | 6 | D14 |
| T4 | Emulator in `dev-docker-compose.yml` (profile `cosmos`) for CI/offline work | 3 | T3 |
| T5 | **`contrib/cosmos/seed.py` + `samples/ph3_snapshots.json`** — hand-authored docs across two hour buckets, station + pump levels, full `dex` set, `I → R` transition, invariant validation, `--dry-run`, timestamp-ladder generator; unit tests on the validator | 6 | T3 |
| T6 | `pumphouse_payload.flatten_snapshot()` + exhaustive tests (station vs pump level keys, `data1_raw` agreement, types, quality, status vocabulary, skip-keys, bounds) | 7 | T5 |
| T7 | `IngestionCheckpoint` model + migration + admin | 3 | — |

### Week 2 — connector, poller, bridge, UI

| # | Ticket | Est | Depends |
|---|---|---|---|
| T8 | `CosmosPumphouseConnector` (`check`, `read_latest`, `read_window`, `poll`) with SDK mocked; error-code redaction tests | 10 | T2, T6, T7 |
| T9 | `poll_cosmos_pumphouse_sources` scheduled task + `AIMMS_COSMOS_PUMPHOUSE_ENABLED` setting + time/doc budget tests | 4 | T8 |
| T10 | `import_pumphouse_dump` management command (JSON rows → `flatten_snapshot` → `ingest_readings`, `--dry-run`) | 3 | T6 |
| T11 | Registry `activate/` endpoint + serializer + tests (§5) | 5 | T7 |
| T12 | UI "Live source" card, conditional banner, `tsc`/`biome` clean | 5 | T11 |
| T13 | Integration test against emulator (seed → poll → `MachineSignalState` populated → `read_window` bounded, incl. hour-bucket boundary) | 4 | T4, T5, T8, T9 |
| T14 | Docs: `docs/docs/…/cosmos-connector.md`, threat-model note (read-only identity, no credential in DB), PR to `IOT` | 3 | all |

**Total ≈ 65 h.** Explicitly **not** in this sprint: Cassandra→Cosmos migration/CDC job, `pumphouse_latest`
maintenance, change-feed polling, retention/TTL policy values.

### Milestones
- **M1 (end W1)**: emulator provisioned from repo artefacts, hand-seeded PH_3 documents present across two
  hour buckets, `flatten_snapshot` green, README Gate 1 closed.
- **M2 (end W2)**: PH_3 registered → dictionary imported → activated → live `MachineSignalState` values read
  out of Cosmos; PR raised to `IOT`.

---

## 8. Decisions

### Answered (2026-09-11)

| # | Decision |
|---|---|
| D1 | **Cosmos DB for NoSQL** (`azure-cosmos` SDK). No CQL compatibility requirement. |
| D2 | **Migration deferred.** No Cassandra→Cosmos job this sprint; we hand-insert a few timestamped documents and build the connector against them. `pumphouse_latest` therefore deferred (§3.2). |
| D3 | **Levels.** Params are recorded at pumphouse level and/or pump level "as applicable"; the normaliser derives the level from the document position rather than a hard-coded table (§1.1, §4.3). |
| D4 | **UTC** for the `month`/`YYYYMM` derivation and for all epoch-ms interpretation. |
| D5 | **TTL is a value, not a policy.** Container TTL enabled with no default (`defaultTtl: -1`); per-document `ttl` honoured when set, omitted on pilot docs. **`data1` must be kept verbatim** as `data1_raw`, authoritative over the parsed fields. |
| D6 | **Entra ID auth.** `DefaultAzureCredential` + the built-in **Cosmos DB Data Reader** data-plane role on the AIMMS identity. No account key in `HealthSource`, config or code. A key is used only against the local emulator, from an env var. |
| D8 | **`dex` extension tags ingested from day one**, alongside the basic params. Only *approved* dictionary points become bindings, so review still gates what is stored. |
| D10 | **Status vocabulary**: `st` ∈ {`I` = Idle, `R` = Running}, applies at both station and pump level. Unknown codes pass through raw with `quality='uncertain'` rather than being mapped to a guess. |
| D12 | **Azure Cosmos account and container already exist.** `provision.py` therefore runs in **verify/diff mode by default** (`--create` is opt-in): it asserts the partition-key paths, `defaultTtl` and indexing policy match `contrib/cosmos/schema/` and reports drift instead of mutating a live account. |
| D14a | **The existing container is unusable as-is**: partition key `/maintenance_pk`, non-hierarchical, **empty**. Since it holds no data, nothing is lost by leaving it alone. Plan of record is **option A** — a dedicated `pumphouse_readings` container with hierarchical PK `/station_uuid` + `/hour_bucket` — with option B (synthetic composite key in the existing container) kept available through config, not code changes. See §3.1. |
| D13 | **PH_3 only** for the pilot, with `parent_entity_uuid == entity_uuid == station_uuid == the pumphouse UUID`. The seeder writes all three from one `--station-uuid` argument. |

### Still open

| # | Question | Blocks | Default if unanswered |
|---|---|---|---|
| D7 | **Exact keys for Total Discharge / Input Power MW / MVar / Pumps Running** — one real snapshot containing them, including their shape inside `pd.P<n>`. | T5 sample fidelity | Seed the draft names `dv`, `pmw`, `pmvar`, `pc`, flagged `"unconfirmed": true` in the sample file |
| D7b | **`dsc` code set** — is `24` "SCADA device", and what are the other values? | T6 display | Pass the integer through unmapped |
| D9 | **Units and alarm bounds** — Annex A proposes a set from standard practice; the plant's own trip/alarm settings must confirm them. | T11 thresholds | Apply Annex A units, leave warn/critical unset (health shows `unknown`, never a fabricated "normal") |
| D11 | **Freshness threshold** (see explanation below) | T9 | 300 s, matching `ext − egt` |
| D14 | **Remaining account coordinates** — endpoint URI, database id, container id, and whether I may create a container (option A) or must reuse the existing one (option B). The partition key itself is now known: `/maintenance_pk`, non-hierarchical, empty (D14a). | T3, T8 | Option A; read coordinates from `INVENTREE_COSMOS_*` env vars; `provision.py` reports drift instead of assuming |

#### D11 explained

`HealthSource.freshness_threshold_seconds` is only "how old may the newest value be before the UI marks the
signal **stale** instead of showing it as current". It does not delete, reject or stop ingesting anything —
`MachineSignalState.is_stale()` compares `now − observed_at` against it and the Health blade greys the tile.

Given the source writes every ~5 s and stamps `ext = egt + 300 000` (a 5-minute validity), a value older than
**300 s** is one the source itself considers expired, so 300 s is the natural setting. InvenTree's default is
900 s, which would keep showing a dead SCADA link as healthy for 15 minutes. Recommendation: **300 s**.


## 9. Risks

| Risk | Mitigation |
|---|---|
| **Existing container has a different partition key** (immutable once created) | `provision.py` verify mode reports it in T3, before any code depends on the layout; fallback is a new container or an adapted §3.1 |
| Vibration tags could be µm or mm/s | bounds left unset until the engineering units are supplied; `classify()` returns `unknown` rather than a false "normal" (Annex A) |
| Hand-seeded data does not match what the real writer will produce | `data1_raw` kept verbatim and authoritative; seeder validates the same invariants the connector asserts; sample file marks guessed keys `unconfirmed` |
| Migration sprint later changes the document shape | Only additive fields allowed; parsed fields must remain re-derivable from `data1_raw` |
| `dex` string values with units/garbage | quality flags rather than silent zero; unresolved points never bound |
| RU throttling (429) | SDK retry policy, per-poll doc cap, `THROTTLED` error code surfaced on source |
| Hour-boundary gap on `read_latest` | query current + previous bucket; integration test T13 covers the boundary |
| Secret leakage in logs | fixed error-code vocabulary; no provider message persisted (existing `record_source_error` contract) |
| macOS case-insensitive checkout collision (seen once) | `git checkout -- src/backend/InvenTree/InvenTree/` in runbook |

---

## Annex A — proposed units and alarm bounds (D9)

**Status: proposal, not authority.** These are the conventional engineering units and typical limit bands for
large vertical motor-driven pump sets (IEC 60034 class-F insulation practice for winding temperature,
ISO 20816 families for vibration severity). **They are not the plant's settings.** Before any threshold is
entered into `MachineSignalBinding`, it must be reconciled with the PH_3 SCADA alarm/trip list; where a value
is not confirmed, leave warn/critical **unset** — `classify()` then returns `unknown`, which is the honest
answer, instead of painting an unbounded signal green.

### A.1 Station level

| Point | Unit | Typical band | Notes |
|---|---|---|---|
| `/sl` Surge Pool Level | m (above datum) | site-specific | Excerpt value 132.45 is consistent with metres above MSL, not a percentage |
| `/dex/COMMAN_FORBAY_LEVEL` | m (above datum) | site-specific | Low-low level is a pump-trip interlock — bounds must come from the plant |
| `/dv` Total Discharge | m³/s (cumecs) | site-specific | Lift-irrigation convention; could also be MLD or m³/h — confirm with D7 |
| `/pmw` Input Power | MW | 0 … installed rating | |
| `/pmvar` Input Power | MVar | ± rating | Sign convention (import/export) needs confirming |
| `/pc` Pumps Running | count | 0 … 14 | Should agree with the count of `pd.P<n>.st == "R"`; a mismatch is itself a useful anomaly check |
| `/st` Status | code | `I` Idle, `R` Running | D10 |

### A.2 Pump level (`dex` tags, `PUMP<n>_` prefix stripped to the local tag)

| Tag family | Unit | Conventional band | Notes |
|---|---|---|---|
| `MOTOR_CORE_RTD<n>_PROCESS_VALUE` | °C | alarm ≈ 120, trip ≈ 130 for class F | Stator/core RTD; per-plant settings override |
| `MOTOR_HOT_AIR_TEMP<n>` | °C | alarm ≈ 80–90 | Air-cooler outlet |
| `THRST_BRG_THRST_PD_RTD<n>_PROCESS_VALUE`, `THRUST_AXIAL_PAD_<n>` | °C | alarm ≈ 85, trip ≈ 90 | White-metal thrust pad limits are conservative and plant-specific |
| `PUMP_INLET_COOLING_WATER_TEMPERATURE<n>` | °C | alarm ≈ 40–45 | |
| `MTR_NDE_BRG_VBRTN<n>`, `PMP_THRST_BRG_VBRTN<n>`, `PUMP_MOTOR_DE_VIBRATION<n>` | **µm or mm/s — must be confirmed** | — | Shaft-relative displacement (µm) and bearing-housing velocity (mm/s) differ by ~2 orders of magnitude; guessing wrong makes every alarm meaningless. Leave bounds unset until the SCADA tag engineering units are supplied |
| `EXCITATION_FLD_CURR_PROCESS_VALUE` | A | 0 … rated field current | |
| `EXCITATION_FLD_VLTG_PROCESS_VALUE` | V | 0 … rated field voltage | |
| `PUMP_REACTIVE_POWER` | MVar | ± rating | |
| `HOPD_VALVE_POS_PROCESS_VALUE`, `EOPD_VALVE_POS_PROCESS_VALUE` | % | 0 … 100 | Valve position; values outside 0–100 indicate a scaling fault |
| `ID`, `TIMESTAMP` | — | — | Metadata, never bound (§4.3) |

### A.3 How units get applied

Units live on `MachineSignalBinding.unit`, seeded from `DictionaryPoint.unit` when the registry point is
activated (§5). The `PUMP` catalogue templates loaded by `load_pump_catalogue` already carry unit metadata
for matched (`exact`/`alias`) points, so Annex A is only needed for points the catalogue does not resolve.
A one-column CSV from the plant (`tag, engineering_unit, alarm_lo, alarm_hi, trip_lo, trip_hi`) would replace
this annex entirely and is the preferred input.
