# Task list: Azure Cosmos DB schema + read-only pumphouse connector

Companion to `plan.md`. Branch `inventTree-aniket`; PR to `IOT` at the end (human review required —
see `AGENTS.md`).

**Definition of done, every ticket**: unit tests for the new behaviour; `prek run --files <changed>`
clean; `ty` clean on touched files; no credential in code, config, fixture or log; commit on
`inventTree-aniket` with a message that says *why*, not just *what*.

| Status | Meaning |
|---|---|
| ✅ | merged on the branch |
| 🔵 | ready to start (no blocker) |
| ⏸ | blocked, blocker named |

---

## Week 0 — carried over from the registry sprint

### 🔵 T0 — Register PH_3 and import the pilot dictionary · 2 h
The registry shipped and merged, but nothing has ever been put through it: the live database holds
0 stations, 0 pump slots, 0 components and 0 dictionary points. Until the pilot payload has been
through preview → import → review, the workflow is tested but unproven, and T11 has nothing to
activate.
- [ ] Register PH_3 via `POST /api/assets/registry/` with the pilot `entity_uuid`, namespace and
      `source_context` selectors (`location_type`, `component_type`, `event_value_type`)
- [ ] Preview `PH_3.pilot-excerpt.json`, confirm `rows_matched` and the counts by match method
- [ ] Import at the returned hash; expect 14 pump slots and the station's dictionary points
- [ ] Review a sample of points (approve/reject) so T11 has approved points to bind
- [ ] Record the resulting counts in this file
**Acceptance**: the Equipment Registry page shows PH_3 with its pumps, components and dictionary,
and re-importing the same file adds nothing.
**Note**: writes domain rows into the dev database — clear with the user first.

---

## Week 1 — schema, dependency, seed, normalisation

### ✅ T1 — Accept the real Cassandra hour-bucket types · 4 h · commit `e60b696ba`
Fix the bug the confirmed DDL exposed, and record the schema.
- [x] `epoch_ms()` in `assets/registry.py` parses `time_period` as `text` or bigint; rejects bool and
      non-numeric strings instead of coercing
- [x] Multi-station hour slice: skip other stations' rows, fail only when none match; expose
      `rows_matched` in the preview
- [x] Tests `test_text_hour_bucket`, `test_multi_station_hour_slice`
- [x] `contrib/pump-cassandra/README.md`: confirmed `CREATE TABLE` + four consequences; gates 1–4
      closed, gate 5 redirected to this spec
- [x] `PH_3.mapping.draft.json` basic-params block + offline tests

### 🔵 T2 — Add the `azure-cosmos` dependency · 2 h
- [ ] `azure-cosmos` in `src/backend/requirements.in` (`azure-identity` is already present)
- [ ] pip-compile `requirements.txt` and `contrib/container/requirements.txt`
- [ ] CVE check on the new transitive tree
- [ ] Rebuild the dev image; `manage.py check` still clean
**Acceptance**: `python -c "import azure.cosmos"` works inside `inventree-dev-server`.

### ⏸ T3 — Cosmos schema artefacts and verifier · 6 h · *blocked: D14*
- [ ] `contrib/cosmos/schema/iwm.pumphouse_readings.json` — hierarchical PK `/station_uuid` +
      `/hour_bucket`, `defaultTtl: -1`, indexing policy excluding `/pd/*`, `/dex/*`, `/data1_raw`
- [ ] `contrib/cosmos/schema/iwm.pumphouse_latest.json` — written now, created later
- [ ] `contrib/cosmos/provision.py` — **verify/diff by default** against the existing account;
      `--create` for the emulator, `--emulator` for the local endpoint; never grants roles, never
      writes data
- [ ] `contrib/cosmos/README.md` — schema rationale, §1.1 mapping table, Annex A units, the Entra ID
      role assignment, verify/seed commands
**Blocker (D14)**: endpoint URI, database id, container id **and the partition key the existing
container was created with** — a container's PK is immutable, so if it is not `/station_uuid` +
`/hour_bucket` we recreate it or adapt the schema.
**Acceptance**: `provision.py --verify` prints "matches schema" or an explicit drift list.

### ⏸ T4 — Cosmos emulator in the dev stack · 3 h · *blocked: T3*
- [ ] `cosmos` profile in `contrib/container/dev-docker-compose.yml` (linux emulator image)
- [ ] Certificate/TLS handling documented for the SDK
- [ ] `provision.py --emulator --create` brings up an empty, correctly-shaped container
**Acceptance**: CI and offline work never need the real Azure account.

### ⏸ T5 — Manual seeder + pilot documents · 6 h · *blocked: T3; D7 affects fidelity*
This is the sprint's data source (migration is deferred, D2).
- [ ] `contrib/cosmos/seed.py`: validates the §3.1 invariants (half-open bucket, UTC `month`,
      `data1_raw` round-trip), computes `month`/`payload_hash`/`data1_raw`, upserts
- [ ] Flags: `--station-uuid` (writes `station_uuid`, `entity_uuid`, `parent_entity_uuid` from one
      value per D13), `--from`/`--count`/`--every-ms` timestamp ladder, `--dry-run`
- [ ] `contrib/cosmos/samples/ph3_snapshots.json`: several timestamps across **two hour buckets**,
      station- and pump-level values, full `dex` tag set (D8), a `pd.P<n>.st` `I → R` transition,
      one doc carrying `dv`/`pmw`/`pmvar`/`pc` flagged `"unconfirmed": true`
- [ ] Unit tests for the validator (rejects out-of-bucket samples, wrong `month`, mismatched
      `data1_raw`)
**Acceptance**: `seed.py --dry-run` output is byte-identical to what is upserted.

### ⏸ T6 — `flatten_snapshot()` normalisation · 7 h · *blocked: T5*
`machine_health/connectors/pumphouse_payload.py`, pure function, no I/O, shared by the connector and
the dump importer.
- [ ] JSON-pointer `external_key` derived from position, so station and pump levels fall out of one
      walk (`/sl`, `/pd/P3/st`, `/dex/PUMP3_…`) — matches `DictionaryPoint.path`
- [ ] Re-parses `data1_raw` and **fails the snapshot** when parsed fields disagree (D5)
- [ ] `observed_at` from `sub_time_period` (fallback `egt`), UTC; `sequence = sub_time_period`
- [ ] Quality rules: unparsable `dex` string → `uncertain`; empty/non-finite → `bad` with value
      `None`; unknown `st` code → `uncertain`, never mapped to a guess (D10: `I` idle, `R` running)
- [ ] Skips `dex.ID`, `dex.TIMESTAMP` and envelope keys unless explicitly bound
- [ ] Respects `MAX_VALUE_BYTES` (2048)
**Acceptance**: every point in the PH_3 sample produces exactly one `Reading` with the expected key,
type and quality; no reading invents a value.

### 🔵 T7 — `IngestionCheckpoint` model · 3 h
- [ ] `assets.IngestionCheckpoint(source FK, station_uuid, hour_bucket str, sub_time_period bigint,
      continuation_token text, updated_at)`, unique `(source, station_uuid)`
- [ ] Rejects backwards movement (replay protection at the source boundary)
- [ ] Migration + admin registration
**Acceptance**: a restarted worker resumes from the stored position, never re-reads the whole hour.

---

## Week 2 — connector, poller, bridge, UI

### ⏸ T8 — `CosmosPumphouseConnector` · 10 h · *blocked: T2, T6, T7*
`machine_health/connectors/cosmos_pumphouse.py`, registered as `cosmos_pumphouse`.
- [ ] `check()` → `(ok, code)` from a **fixed vocabulary** `AUTH|NOT_FOUND|THROTTLED|NETWORK|OK`;
      no endpoint, tag or provider message ever persisted or logged
- [ ] `read_latest()` — query A on the current hour bucket, falling back to the previous one
- [ ] `read_window()` — `bounded_window()`, enumerate buckets, `max_item_count=100`, stop at
      `max_samples`, never round a timestamp
- [ ] `poll(checkpoint)` — query B forward from the checkpoint; change feed deferred
- [ ] Entra ID via `DefaultAzureCredential` + **Cosmos DB Data Reader** (D6); emulator key from an
      env var only
- [ ] All queries parameterised and single-partition; cross-partition explicitly disabled
**Acceptance**: SDK mocked in tests; a test asserts no query is issued without both PK components.

### ⏸ T9 — Scheduled poller · 4 h · *blocked: T8*
- [ ] `assets/tasks.py: poll_cosmos_pumphouse_sources()` at `ScheduledTask.MINUTES, 1`
- [ ] `AIMMS_COSMOS_PUMPHOUSE_ENABLED` kill-switch, default **off** (`get_boolean_setting`)
- [ ] Per-source time budget (≤ 20 s) and document cap so one slow account cannot starve the worker
- [ ] `record_source_error()` on failure; `freshness_threshold_seconds` default **300 s** (D11)
**Acceptance**: with the flag off, the task performs zero network calls.

### ⏸ T10 — `import_pumphouse_dump` command · 3 h · *blocked: T6*
- [ ] JSON rows → `flatten_snapshot` → `ingest_readings`, with `--dry-run`
- [ ] Shares the T6 path exactly — no second normaliser
**Acceptance**: the same file imported twice changes nothing the second time.

### ⏸ T11 — Registry → live bridge · 5 h · *blocked: T7, T0 (needs approved points)*
Closes the "mapping approval does not enable live ingestion" gap.
- [ ] `POST /api/assets/registry/<pk>/activate/` with `{"source": <HealthSource id>}`
- [ ] Creates/refreshes `MachineSignalBinding` for every `DictionaryPoint(status='approved')`;
      rejected and unresolved points are never bound
- [ ] Idempotent and hash-locked like import; deactivation removes only that station's bindings
- [ ] Units seeded from `DictionaryPoint.unit`, else Annex A; thresholds left unset when unconfirmed
      so health reads `unknown` rather than a fabricated `normal`
**Acceptance**: activating twice creates no duplicate bindings; a rejected point never appears.

### ⏸ T12 — Live-source UI · 5 h · *blocked: T11*
- [ ] "Live source" card on the *Dictionary and review* tab of `EquipmentRegistry.tsx`: pick a
      `HealthSource`, show bound/unbound counts, last poll time, last error code
- [ ] Banner text becomes conditional on activation
- [ ] `tsc --noEmit` and `biome check` clean
**Acceptance**: the offline banner disappears only when bindings exist for that station.

### ⏸ T13 — End-to-end integration test · 4 h · *blocked: T4, T5, T8, T9*
- [ ] Seed the emulator → poll → `MachineSignalState` populated with the expected values
- [ ] `read_window` stays bounded and crosses an hour boundary correctly
- [ ] Checkpoint advances; a second poll ingests nothing new
**Acceptance**: runs in CI without the real Azure account.

### ⏸ T14 — Docs and PR · 3 h · *blocked: all*
- [ ] `docs/docs/…/cosmos-connector.md`: setup, RBAC role, kill-switch, failure codes
- [ ] Threat-model note: read-only data-plane role, no credential in DB or API response,
      connector cannot write to a control system
- [ ] PR to `IOT` — **human review required before opening** (`AGENTS.md`)

---

## Summary

| Bucket | Tickets | Hours |
|---|---|---|
| Done | T1 | 4 |
| Ready now | T0, T2, T7 | 7 |
| Blocked on D14 (account details) | T3, T4, T5 | 15 |
| Blocked on earlier tickets | T6, T8–T14 | 41 |
| **Total** | **15** | **67** |

### Critical path
`T2 → T8 → T9 → T13 → T14` for the live read, with `T3 → T5 → T6` feeding T8 and
`T0 → T11 → T12` feeding the UI. T3/T4/T5 (15 h) are unblocked by a single answer: D14.

## Out of scope this sprint
Cassandra → Cosmos migration/CDC job · `pumphouse_latest` maintenance · change-feed polling ·
retention/TTL policy values · writing to any control system (never in scope)

## Open decisions
`D7` discharge/power/pump-count keys · `D7b` `dsc` code set · `D9` units and alarm bounds
(Annex A is a proposal, not plant authority) · `D11` freshness threshold · `D14` account
coordinates and existing container partition key. Defaults for each are recorded in `plan.md` §8.
