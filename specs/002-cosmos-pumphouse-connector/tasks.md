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

### ✅ T0 — Register PH_3 and import the pilot dictionary · 2 h · done 2026-09-12
The registry shipped and merged, but nothing had ever been put through it. Now it has.
- [x] Registered PH_3 (`pk 17`, uuid `ce0a411f-…`, namespace `klsw`, entity
      `bafc976f-1ccc-4a91-aaa6-c3eac2470d36`, selectors `PUMP_HOUSE` / `65` / `41`) against the
      `Internal` client
- [x] Previewed `PH_3.pilot-excerpt.json`: 1 row, 1 matched, 14 pump slots, 31 points
- [x] Imported at the previewed hash
- [x] **Idempotency confirmed**: re-import reports `new: 0, preserved: 31` and creates nothing

Resulting state: **14 pump slots, 30 components, 31 dictionary points** — 21 `exact`, 9 `alias`,
1 `unresolved`, 0 `conflict`.

Review is deliberately **not** done here: approving a mapping is a human judgement about which tag
means which measurement, and mass-approving 30 points to unblock a later ticket would put unreviewed
mappings behind an "approved" label. T11 binds only approved points, so someone reviews them in the
Dictionary tab first.

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

### ✅ T2 — Add the `azure-cosmos` dependency · 2 h · commit `0ad1308df`
- [x] `azure-cosmos>=4.9.0` in `src/backend/requirements.in`
- [x] Raised `azure-identity` floor `>=1.15.0` → `>=1.16.1`: the old floor permitted
      **CVE-2024-35255** (elevation of privilege) and the connector puts `DefaultAzureCredential`
      on a data path. The lock already resolved to 1.25.3, but a floor should not allow a
      vulnerable resolution.
- [x] pip-compile: adds `azure-cosmos==4.17.0` and nothing else — no version churn
- [x] CVE check on 4.17.0: clean
- [x] Verified in `inventree-dev-server`: imports fine, `manage.py check` clean

### ✅ T3 — Cosmos schema artefacts and verifier · 6 h · commits `744250a4b`, `2b089fff3`
- [x] `contrib/cosmos/schema/pumphouse_readings.container.json` — hierarchical PK `/station_uuid` +
      `/hour_bucket`, `defaultTtl: -1`, indexing excluding the payload. No database id in the file
- [x] `schema/pumphouse_readings.indexing.json` — the policy alone for `az ... --idx @file`, kept
      identical to the definition by a test
- [x] `provision.py` — verifies offline from `az` output (no credentials, runs in CI), live, or against
      the emulator; `--create` refuses anything but the emulator; never grants a role or writes a document
- [x] `contrib/cosmos/README.md` — runbook, throughput/cost decision, troubleshooting
- [x] 11 offline tests
- [x] **Container created and verified live**: `aimms/pumphouse_readings` matches the definition
- [x] Indexing policy applied through the control plane after Data Explorer refused it (the
      `Cosmos DB Operator` role cannot read account keys, so its data-plane save had no credential)
- [ ] `schema/pumphouse_latest.container.json` — deferred with §3.2, nothing would maintain it yet

### ✅ T4 — Cosmos emulator in the dev stack · 3 h · *done — 11 tests; the connector now runs against a real Cosmos service offline*
- [x] `cosmos` profile in `contrib/container/dev-docker-compose.yml`, pinned to the multi-arch
      vNext image and gated on the image's own `/ready` probe
- [x] Certificate/TLS handling documented — **there is none**: vNext serves plain HTTP on 8081
- [x] `provision.py --emulator --create` brings up an empty, correctly-shaped container
- [x] `INVENTREE_COSMOS_*` and `COSMOS_EMULATOR_KEY` set on the dev server and worker, all
      overridable, so the scripts need no flags beyond `--emulator`
**Acceptance**: met — CI and offline work never need the real Azure account.

**Verified end to end, not just configured.** Created the container, seeded the three pilot
documents, and read them back through the T8 connector: `check()` returned `(True, 'OK')`, a window
read crossed the hour boundary and returned the `I → R` transition at both station and pump level,
and `poll()` produced 95/69/72 readings across the three snapshots. That is T8's hierarchical
partition key, parameterised queries and single-partition scoping proved against a genuine Cosmos
implementation rather than the mock.

**Two things corrected**
- The classic emulator image is **amd64 only** and serves self-signed HTTPS; this machine is arm64,
  so it would have run under emulation *and* needed a root CA installed. The vNext image is
  multi-arch and plain HTTP, which removes the whole "certificate/TLS handling" line item.
- `provision.py` and `seed.py` defaulted to `https://localhost:8081`, which this image does not
  serve. Both now default to `http://`, with a comment saying why so it does not get "fixed" back.

### ✅ T5 — Manual seeder + pilot documents · 6 h · commit `4520ea38c` · *seeded to the real account 2026-09-12*
This is the sprint's data source (migration is deferred, D2).
- [x] `contrib/cosmos/seed.py`: validates the §3.1 invariants (half-open bucket, UTC `month`,
      `data1_raw` round-trip), computes `month`/`payload_hash`/`data1_raw`, upserts
- [x] Flags: `--station-uuid`, `--from`/`--count`/`--every-ms` timestamp ladder, `--dry-run`
- [x] `contrib/cosmos/samples/ph3_snapshots.json`: 3 documents across **two hour buckets**,
      station- and pump-level values, `dex` tags (D8), a `pd.P<n>.st` `I → R` transition
- [x] 28 offline tests for the validator (rejects out-of-bucket samples, wrong `month`, mismatched
      `data1_raw`)
- [x] **D7 closed by a real snapshot**: `dv`/`pmw`/`pmvar` exist at *both* levels; `pc` is a float
- [x] **D13 corrected**: `parent_entity_uuid` is a shared parent, *not* the station uuid; a supplied
      parent is preserved
- [x] **Seeded into the real account** once D16 was granted: 3 documents across buckets
      `1752850800000` (2) and `1752854400000` (1), read back with the connector's query shape
**Acceptance**: met, on the emulator *and* on `epconchatcosmos9d6b/aimms/pumphouse_readings`.


### ✅ T6 — `flatten_snapshot()` normalisation · 7 h · commit `e0321ce3b`
`machine_health/connectors/pumphouse_payload.py`, pure function, no I/O, shared by the connector and
the dump importer.
- [x] JSON-pointer `external_key` derived from position, so station and pump levels fall out of one
      walk (`/sl`, `/pd/P3/st`, `/dex/PUMP3_…`) — matches `DictionaryPoint.path`
- [x] Re-parses `data1_raw` and **fails the snapshot** when parsed fields disagree (D5)
- [x] `observed_at` from `sub_time_period` (fallback `egt`), UTC; `sequence = sub_time_period`
- [x] Quality rules: unparsable `dex` string → `uncertain`; empty/non-finite → `bad` with value
      `None`; unknown `st` code → `uncertain`, never mapped to a guess (D10: `I` idle, `R` running)
- [x] Skips `dex.ID`, `dex.TIMESTAMP` and envelope keys unless explicitly bound
- [x] Respects `MAX_VALUE_BYTES` (2048)
- [x] **`in_batches()`** — a real snapshot flattens to ~762 readings against a 500-reading batch
      limit, so `ingest_readings` would *raise* on a whole snapshot. Paging lives beside the
      function that creates the oversized list rather than being rediscovered in T8.
- [x] RFC 6901 escaping, so a tag containing `/` or `~` stays unambiguous
**Acceptance**: met — 30 tests, no Azure and no database.

**Found while building it**: the batch limit. The first version of the sample-file test asserted a
snapshot fits one batch; it passed only because the checked-in samples are abridged. Against a
realistic 700-tag payload the assertion is false, so the test was replaced with one that states the
real constraint and proves `in_batches` satisfies it.


### ✅ T7 — `IngestionCheckpoint` model · 3 h · commit `1a4403760`
- [x] `assets/ingestion_models.py`: `IngestionCheckpoint(source FK, station_uuid, hour_bucket str,
      sub_time_period bigint, continuation_token, updated_at)`, unique `(source, station_uuid)`
- [x] Forward-only `advance_to()`: an equal or earlier position is refused and reported, and the
      candidate is validated *before* assignment so a refused advance leaves the instance untouched
      in memory as well as in the database
- [x] Half-open bucket validation (`hour_bucket <= sample < hour_bucket + 3_600_000`), text bucket
      parsed like the source stores it
- [x] Migration `0012_ingestioncheckpoint`, applied
- [x] Admin: read-only, no add permission — a hand-edited position would skip or replay samples
- [x] 7 tests incl. bucket edges, cross-bucket advance, uniqueness, and "stores no measurement"

---

## Week 2 — connector, poller, bridge, UI

### ✅ T8 — `CosmosPumphouseConnector` · 10 h · *done — 53 tests, SDK mocked; runnable against Azure once D16 is granted*
`machine_health/connectors/cosmos_pumphouse.py`, registered as `cosmos_pumphouse`.
- [x] `check()` → `(ok, code)` from a **fixed vocabulary** `AUTH|NOT_FOUND|THROTTLED|NETWORK|OK`, plus
      `CONFIG` for a source that cannot be used as configured; no endpoint, tag or provider message
      ever persisted or logged
- [x] `read_latest()` — query A on the current hour bucket, falling back to the previous one
- [x] `read_window()` — `bounded_window()`, enumerate buckets, `max_item_count=100`, stop at
      `max_samples`, never round a timestamp
- [x] `poll(checkpoint)` — query B **strictly after** the checkpoint; change feed deferred
- [x] Entra ID via `DefaultAzureCredential` + **Cosmos DB Data Reader** (D6); an account key is
      refused outright against a real endpoint and read from an env var for the emulator only
- [x] All queries parameterised and single-partition; cross-partition explicitly disabled; a partial
      partition key raises before any request is built
- [x] **Ingests through `in_batches()`** (T6); the checkpoint advances per *snapshot*, only after
      every batch of it is accepted
**Acceptance**: met — tests assert no query is issued without both PK components, and that a
snapshot whose second batch fails leaves the checkpoint untouched.

**Two things found while building it**
- `CONFIG` was added to the error vocabulary. A missing endpoint is not a `NETWORK` fault, and
  reporting it as one sends an operator to look at a firewall for a blank config field.
- **Bug fixed in the ingest path**: the connector was forwarding its `now` (the *source-clock*
  horizon for how far forward to read) into `ingest_readings(now=...)` (the *server clock* that skew
  is measured against). The project runs with `USE_TZ` off, so this raised
  `can't subtract offset-naive and offset-aware datetimes` on every batch — and `_classify` would
  have reported it in production as `NETWORK`. Two tests now pin the behaviour.


### 🔵 T9 — Scheduled poller · 4 h · *ready — T8 done*
- [ ] `assets/tasks.py: poll_cosmos_pumphouse_sources()` at `ScheduledTask.MINUTES, 1`
- [ ] `AIMMS_COSMOS_PUMPHOUSE_ENABLED` kill-switch, default **off** (`get_boolean_setting`)
- [ ] Per-source time budget (≤ 20 s) and document cap so one slow account cannot starve the worker
- [ ] `record_source_error()` on failure; `freshness_threshold_seconds` default **300 s** (D11)
**Acceptance**: with the flag off, the task performs zero network calls.

### 🔵 T10 — `import_pumphouse_dump` command · 3 h · *ready — needs no Azure access*
- [ ] JSON rows → `flatten_snapshot` → `ingest_readings`, with `--dry-run`
- [ ] Shares the T6 path exactly — no second normaliser
**Acceptance**: the same file imported twice changes nothing the second time.

### 🔵 T11 — Registry → live bridge · 5 h · *ready to build — T0/T7 done; end-to-end proof needs the 31 points reviewed*
Closes the "mapping approval does not enable live ingestion" gap.
- [ ] `POST /api/assets/registry/<pk>/activate/` with `{"source": <HealthSource id>}`
- [ ] Creates/refreshes `MachineSignalBinding` for every `DictionaryPoint(status='approved')`;
      rejected and unresolved points are never bound
- [ ] Idempotent and hash-locked like import; deactivation removes only that station's bindings
- [ ] Units seeded from `DictionaryPoint.unit`, else Annex A; thresholds left unset when unconfirmed
      so health reads `unknown` rather than a fabricated `normal`
**Acceptance**: activating twice creates no duplicate bindings; a rejected point never appears.

### 🔵 T12 — Live-source UI · 5 h · *blocked: T11*
- [ ] "Live source" card on the *Dictionary and review* tab of `EquipmentRegistry.tsx`: pick a
      `HealthSource`, show bound/unbound counts, last poll time, last error code
- [ ] Banner text becomes conditional on activation
- [ ] `tsc --noEmit` and `biome check` clean
**Acceptance**: the offline banner disappears only when bindings exist for that station.

### 🔵 T13 — End-to-end integration test · 4 h · *ready — T4, T5, T8, T9 … T9 still outstanding*
- [ ] Seed the emulator → poll → `MachineSignalState` populated with the expected values
- [ ] `read_window` stays bounded and crosses an hour boundary correctly
- [ ] Checkpoint advances; a second poll ingests nothing new
**Acceptance**: runs in CI without the real Azure account.

### 🔵 T15 — Pumphouse mimic: layout contract + SVG asset  6 h  *blocked: T11*
The schematic the two reference images describe. This ticket produces the *drawing* and the
*contract*, not the live behaviour — keeping them apart means the artwork can be redrawn without
touching React, and the binding can be tested without the artwork being final.
- [ ] `src/frontend/src/assets/mimic/pumphouse.svg` — station schematic: suction side, forebay /
      surge pool level indicator, common header, 14 pump bays, discharge. Hand-authored and
      committed, **not** generated at runtime.
- [ ] Every live element carries a stable `data-point` attribute holding the **JSON pointer**
      already used as `DictionaryPoint.path` / `MachineSignalBinding.external_key`
      (`/pd/P03/st`, `/pd/P03/dv`, `/sl`, `/pmw`). No second naming scheme, no index maths in the
      component.
- [ ] `pumphouse.layout.json` — declares which pointers the drawing expects and what each element
      is (`status` | `value` | `level`), so a missing binding is a *validation* failure, not a
      blank box a user has to notice.
- [ ] Validator (backend test or `tsx` script): every `data-point` in the SVG resolves to an
      approved `DictionaryPoint` for PH_3; every approved point is either drawn or explicitly
      listed as not-drawn. **The 6 withheld points must appear in the not-drawn list**, so
      vibration and reactive power cannot silently render as empty gauges.
- [ ] `biome check` clean; SVG has no embedded raster, no external font, no inline script.
**Acceptance**: the validator fails if a pointer is renamed on either side. Deliberately reject a
point and the build tells you which element lost its binding.

### 🔵 T16 — Station mimic state API  4 h  *blocked: T9, T11*
One request paints the whole diagram. Thirty-one per-signal calls on a screen meant to be left open
on a wall display is the wrong shape.
- [ ] `GET /api/machine-health/station/<pk>/mimic/` beside the existing `machine-health` routes in
      `machine_health/api.py`, reusing `_MachineHealthView`'s scope check — station scope, not
      per-machine, so the 14 pumps are authorised once.
- [ ] Response keyed by the same JSON pointers: `{value, unit, quality, observed_at, age_seconds}`
      per point, plus station-level `{source, last_poll_at, last_error_code, enabled}`.
- [ ] **Unknown must be representable.** A point with no reading returns `null` with a reason, never
      `0` and never a last-known value dressed up as current — a stale "Running" on a mimic board is
      how someone walks up to a live pump.
- [ ] `age_seconds` computed server-side against the server clock (the client clock is not
      trustworthy, and this is the same skew trap T8 already hit).
- [ ] Kill-switch off ⇒ `enabled: false` and every point `null`; the UI must have something honest
      to show rather than an empty diagram.
**Acceptance**: one request returns every drawn pointer; a station with no bindings returns a valid
payload with all values `null` and a reason, HTTP 200, not 404.

### 🔵 T17 — `PumphouseMimic.tsx` live dashboard  8 h  *blocked: T15, T16*
- [ ] Component under `src/frontend/src/pages/assets/health/`, mounted as a tab on the AIMMS
      machine page for stations (siblings: `HealthSummary.tsx`, `SignalTable.tsx`)
- [ ] Inlines the SVG, resolves `data-point` → payload, sets text and fill. Pump bay fill from `st`:
      `R` running, `I` idle, `null`/stale a distinct **hatched** state — colour alone is not enough
      for a control-room screen or a colour-blind operator
- [ ] Staleness threshold from `age_seconds`; a whole-diagram banner when the source last reported an
      error code or the kill-switch is off
- [ ] Polls on an interval, pauses when the tab is hidden, and shows *when* the data is from —
      absolute timestamp, not only "2m ago"
- [ ] All labels through `lingui` **and re-extracted** (`invoke int.frontend-compile --extract`), or
      the page ships showing hash IDs again
- [ ] `tsc --noEmit` and `biome check` clean; unit tests for the pointer→element binding and for the
      stale/unknown rendering path
**Acceptance**: seed the emulator, run the poller, the diagram matches the seeded `I → R` transition;
stop the poller and every bay degrades to stale rather than freezing on the last good value.

### 🔵 T14 — Docs and PR  3 h  *blocked: all*
- [ ] `docs/docs/…/cosmos-connector.md`: setup, RBAC role, kill-switch, failure codes
- [ ] Threat-model note: read-only data-plane role, no credential in DB or API response,
      connector cannot write to a control system
- [ ] PR to `IOT` — **human review required before opening** (`AGENTS.md`)

---

## Summary

| Bucket | Tickets | Hours |
|---|---|---|
| Done | T0, T1, T2, T3, T4, T5, T6, T7, T8 | 43 |
| Ready now | T9, T10, T11 | 12 |
| Blocked on earlier tickets | T12, T13, T14 | 12 |
| Mimic dashboard (added 2026-09-13) | T15, T16, T17 | 18 |
| **Total** | **18** | **85** |

> **T15–T17 were missing from the original plan.** The sprint was scoped around getting data *in*;
> the two reference images shared at kick-off describe the pumphouse schematic users actually look
> *at*, and no ticket covered it. They are additive — nothing in T0–T14 changes — but the sprint is
> no longer a two-week, one-dev sprint at 85 h. See **D18** before starting T15.

### What is actually blocking
- **D16 — RESOLVED 2026-09-12.** A data-plane role assignment now exists on the account. Verified by
  reading container properties over the data plane (`provision.py --live` →
  *matches the expected definition*) and by seeding the three pilot documents into
  `aimms/pumphouse_readings` and querying them back with the connector's own query shape
  (hierarchical partition key, parameterised, cross-partition disabled). **T5's acceptance is now
  fully met against the real account.**
- **D17 is now the live question, and it is not the same ask.** What was granted is
  `00000000-...-000000000002` = **Data Contributor** (read *and* write), scoped to the **whole
  account**, on the developer's own principal `f024cd79-…`. That is correct for a human who has to
  seed documents — seeding writes, so Data Reader could not have done it. It is *not* what the
  application should run as. If AIMMS authenticates as an identity holding Data Contributor, the
  property the design relies on — that read-only is enforced by Azure rather than by our own code —
  is lost, and a bug in the connector could delete plant history. Before go-live the app's managed
  identity needs its own assignment: role `…000000000001` (**Data Reader**), scoped to
  `/dbs/aimms/colls/pumphouse_readings` rather than the account.
- **Review of the 31 imported dictionary points** before T11 can bind anything end to end.
- **D18 — the mimic layout is not yet pinned to the reference images.** T15 is written against what
  the data model can actually supply (14 pump bays with `st`/`dv`/`pmw`, station `sl`), not against a
  measured reading of the two images shared at kick-off. Before drawing, re-open them and confirm:
  which quantity sits next to each pump, whether the level indicator is the forebay or the surge
  pool (the same question that withheld `/sl` in the dictionary review), and whether valves or
  headers shown in the drawing correspond to any tag we receive. **Anything in the images with no
  approved point behind it must be drawn as static geometry, never as a live element** — a mimic
  that appears to show a valve position we do not actually receive is worse than one that omits it.

Resolved since the last revision: **D14** (account `epconchatcosmos9d6b`, RG `EpconChat`, database
`aimms`, container `pumphouse_readings` created and verified) and **D7** (a real snapshot confirmed
`dv`/`pmw`/`pmvar`/`pc`, at both station and pump level).

### Critical path
`T8 → T9 → T13 → T14` for the live read, with `T0 → T11 → T12` feeding the UI, and
`T11 → T15/T16 → T17` feeding the mimic dashboard. Nothing on the critical path is now blocked by an
answer — only *running* against Azure is, via D16 — except T15, which wants D18 answered first.

## Out of scope this sprint
Cassandra → Cosmos migration/CDC job · `pumphouse_latest` maintenance · change-feed polling ·
retention/TTL policy values · writing to any control system (never in scope)

## Open decisions
`D7b` `dsc` code set · `D9` units and alarm bounds (Annex A is a proposal, not plant authority) ·
`D11` freshness threshold · `D16` data-plane role assignment. Defaults for each are recorded in
`plan.md` §8.
