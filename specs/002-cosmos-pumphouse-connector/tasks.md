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


### 🔵 T9 — Scheduled poller  6 h  *ready — T8 done*
**Scope corrected 2026-09-13: the estate is 10–12 pumphouses, not one.** `IngestionCheckpoint` is
unique on `(source, station_uuid)` and `poll()` takes a checkpoint, so the connector already supports
many stations per source — but the loop must iterate **checkpoints, not sources**, or eleven
pumphouses will share one station's cursor.
- [ ] `assets/tasks.py: poll_cosmos_pumphouse_sources()` at `ScheduledTask.MINUTES, 1`
- [ ] `AIMMS_COSMOS_PUMPHOUSE_ENABLED` kill-switch, default **off** (`get_boolean_setting`)
- [ ] Iterate every `(source, station_uuid)` checkpoint; one station's failure must not abort the
      others in the run
- [ ] **Per-station** time and document budget, plus a **fair starting point** — resume the sweep
      after the last station handled rather than always starting at the first, so a slow or
      erroring station near the front cannot starve the ones behind it every single minute
- [ ] Hold the whole-run budget too, so twelve stations cannot collectively overrun the worker
- [ ] `record_source_error()` on failure, recorded **per station**, not per source — "the source is
      down" and "pumphouse 7 is down" are different operational facts
- [ ] `freshness_threshold_seconds` default **300 s** (D11)
**Acceptance**: with the flag off, the task performs zero network calls. With twelve checkpoints and
one of them erroring, the other eleven still ingest, and the erroring one is not retried first
forever.

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

### 🔵 T15 — Pumphouse mimic: layout contract + SVG assets  8 h  *blocked: T18*
The two reference images are **two different screens** and both are needed:
*image 1* is a per-pump installation/instrument diagram (the "Pump Unit" detail), *image 2* is a
station HMI overview (pump row, one selected unit's telemetry, plant totals, alarm list).
This ticket produces the *drawings* and the *contract*, not the live behaviour.
- [ ] `src/frontend/src/assets/mimic/pumphouse-overview.svg` — forebay / river, common discharge
      header, and a **bay template repeated at render time from `pd`**, not a fixed row of 14.
      The estate runs 10–12 stations with differing counts, and Lakshmi's numbering is sparse
      (01–06, 09, 10, 13–17), so bays are **keyed by pump key, never indexed by position**
- [ ] `src/frontend/src/assets/mimic/pump-unit.svg` — one pump unit: casing/spiral case, thrust
      bearing, guide/radial pad, coupling, motor, cooling circuit, HOPD/EOPD valves (image 1)
- [ ] Every live element carries a `data-point` attribute holding the **JSON pointer already
      emitted by `flatten_snapshot`**. For the unit diagram that is overwhelmingly
      `/dex/PUMP<n>_<TAG>`; `<n>` is substituted at render time from the selected bay, which is why
      the pump-unit SVG is authored once and not fourteen times.
- [ ] `pumphouse.layout.json` — per element: pointer template, role (`status` | `value` | `level` |
      `valve`), and the label shown. Panel grouping follows image 2's cards (HYD / MTR / BRG / CLR /
      VLV / ELE) so the mapping to the drawing is reviewable without reading TSX.
      **One layout serves the estate**; a station that lacks a tag renders that element as
      not-bound. Per-station layout overrides only if a station genuinely differs — twelve
      near-identical layout files would drift apart within a month
- [ ] Validator: every `data-point` resolves to an **approved** `DictionaryPoint`; every approved
      point is drawn or explicitly listed as not-drawn. Runs **per station**, so onboarding a
      thirteenth pumphouse with an unexpected tag set fails loudly rather than rendering gaps
- [ ] `biome check` clean; no embedded raster, no external font, no inline script in the SVGs.
**Acceptance**: rename a pointer on either side and the validator fails naming the element.

### 🔵 T16 — Station mimic state API  5 h  *blocked: T9, T11*
One request paints the whole diagram. Image 2 shows ~40 live fields for the selected unit plus a
lamp and a valve state for every bay plus two plant totals — that is one payload, not 100 calls.
- [ ] `GET /api/machine-health/station/<pk>/mimic/` beside the existing `machine-health` routes in
      `machine_health/api.py`, reusing `_MachineHealthView`'s scope check at **station** scope so the
      pump machines are authorised once
- [ ] `?unit=<pump key>` selects which bay gets the full field set; without it, bay summaries only
- [ ] Keyed by the same JSON pointers: `{value, unit, quality, observed_at, age_seconds}`, plus
      station-level `{source, last_poll_at, last_error_code, enabled}`
- [ ] **Plant totals are derived server-side and labelled as derived.** Image 2 shows "CURRENT PLANT
      TOTAL POWER" and "TOTAL FLOW RATE"; if they are summed from running bays rather than read from
      a tag, the payload must say so, and a sum over bays with missing values must return `null`, not
      a quietly-low total that reads as a plant derate
- [ ] **Unknown must be representable** — `null` with a reason, never `0`, never a stale value
      presented as current
- [ ] `age_seconds` computed server-side (the client clock is not trustworthy; same skew trap as T8)
- [ ] Kill-switch off ⇒ `enabled: false`, all points `null`
**Acceptance**: one request returns every drawn pointer; a station with no bindings returns HTTP 200
with all values `null` and a reason, not 404.

### 🔵 T17 — `PumphouseMimic.tsx` live dashboard  10 h  *blocked: T15, T16*
- [ ] Overview + unit-detail views under `src/frontend/src/pages/assets/health/`, mounted as a tab on
      the AIMMS station page (siblings: `HealthSummary.tsx`, `SignalTable.tsx`)
- [ ] Clicking a bay in the overview selects it and re-renders the unit diagram and the panels
- [ ] Bay lamp from `st`: running / idle / fault / **stale** / not-bound. Image 2 uses colour alone
      (green-amber-red); we additionally vary shape or hatching — a control-room screen must not
      depend on colour discrimination
- [ ] The **alarm list is derived from thresholds, not invented**. Image 2's alarm panel lists
      winding temp and vibration trips; until the alarm/trip CSV lands (D9) those rows render as
      "no threshold configured", never as a green "normal"
- [ ] Staleness from `age_seconds`; whole-diagram banner when the source last reported an error code
      or the kill-switch is off; absolute timestamp shown, not only "2m ago"
- [ ] Polls on an interval, pauses when the tab is hidden
- [ ] All labels through `lingui` **and re-extracted** (`invoke int.frontend-compile --extract`) or
      the page ships showing hash IDs again
- [ ] `tsc --noEmit` and `biome check` clean; tests for pointer→element binding and the
      stale/unknown/not-bound render paths
**Acceptance**: seed the emulator, run the poller, the diagram matches the seeded `I → R`
transition; stop the poller and every bay degrades to stale rather than freezing on last-good.

### 🔴 T18 — Full `dex` dictionary import and review  8 h  *blocks T15; do this first*
**The mimic cannot be built from the 31 points already reviewed.** Those came from a trimmed sample
carrying 35 `dex` tags. Image 1's unit diagram alone needs roughly 40 tags per pump — discharge
pressure, 6 core RTDs, 11 winding temps, DE/NDE vibration, thrust and guide pad temps, 5 cooling
inlet + 4 outlet temps, 4 cold-air + 2 hot-air temps, HOPD/EOPD valve positions, speed, frequency,
9 electrical quantities — which is ~560 points across 14 bays, plus station commons. **Across a
10–12 station estate that is several thousand points**, which is precisely why the catalogue and
`ALIASES` must do the work and the review must stay a version-controlled file.
- [ ] Obtain one **untrimmed** production snapshot **per station** (the real PH_3 payload is ~700
      `dex` tags; a 17-pump station will carry more)
- [ ] Re-run the dictionary import for PH_3 — `plan_dictionary` already walks `dex`, attributes each
      tag to its pump via the `PUMP<n>_` prefix, and matches against the catalogue, so **no code
      change is expected**; this ticket is mostly catalogue coverage and review
- [ ] Extend `ALIASES` / catalogue for the tag families image 1 names, including the source's own
      spellings — `POWERFATCOR`, `MOTOR_COLD_AIR TEMP3` (embedded space), `spiral_case1` (lower
      case), `PMP_` vs `PUMP_` — these are upstream facts, not typos to silently correct
- [ ] Review via `apply_dictionary_review` as before, not 700 UI modals
**Acceptance**: every `data-point` the T15 layout wants resolves to an approved point, or is listed
as not-drawn with a reason.

### 🔵 T19 — Onboard the rest of the estate (10–12 pumphouses)  6 h  *blocked: T11*
One station is registered. The estate is 10–12, with differing pump counts (Lakshmi has 17, sparsely
numbered) and possibly differing tag sets. The machinery exists — this ticket uses it at scale and
finds what only breaks on the second station.
- [ ] Obtain the station list: `entity_uuid`, SCADA code (`dex.ID`), plant name, pump count
- [ ] `register_pump_station --mapping` per station, one mapping file each; **the registered UUID is
      authoritative and immutable**, so record it back into the mapping file at registration time —
      the drift that bit PH_3 will otherwise bite eleven more times
- [ ] Confirm every station lands in the same container and that `parent_entity_uuid` really is
      shared across the estate (the mapping draft asserts this from one station's evidence; with
      twelve stations it becomes checkable)
- [ ] One `HealthSource` for the account with a checkpoint per station, **not** twelve sources — the
      credential and endpoint are the same; the cursor is what differs
- [ ] Verify station isolation: a query for station A must never return station B's documents, and
      activating A must not create bindings on B
- [ ] Confirm RU cost and poll duration for a full sweep before enabling the kill-switch in anger
**Acceptance**: twelve stations registered, each with its own checkpoint; a full poll sweep stays
inside the worker budget; cross-station leakage test passes.

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
| Ready now | T9, T10, T11 | 14 |
| Blocked on earlier tickets | T12, T13, T14 | 12 |
| Mimic dashboard (added 2026-09-13) | T18, T15, T16, T17 | 31 |
| Estate rollout (added 2026-09-13) | T19 | 6 |
| **Total** | **20** | **106** |

> **T15–T19 were missing from the original plan.** The sprint was scoped around getting data *in*
> for **one** station; the estate is **10–12 pumphouses**, and the two reference images describe the
> schematic users actually look *at*. Nothing in T0–T8 needs rewriting — the backend carries no
> single-station or fixed-pump-count assumption (verified 2026-09-13) — but **T9 did**, and has been
> corrected to iterate checkpoints rather than sources. Remaining work is **63 h**, not 24 h.
> **Start with T18**: the 25 approved points cover only a fraction of image 1, and drawing before the
> dictionary covers the tags means drawing against nothing.

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
- **D18 — RESOLVED 2026-09-13** by reading the two reference images against the payload. Findings,
  all verified against `samples/ph3_snapshots.json` and `PH_3.pilot-excerpt.json`:
  - **The mimic is a `dex` view.** Every entry in image 1's "Example Parameter Mapping" table is a
    `dex` tag: `PUMP4_DISCHARGE_PRESSURE`, `PUMP4_MOTOR_CORE_RTD3`, `PUMP4_PMP_THRST_BRG_VBRTN2`,
    `PUMP4_GUIDED_RADIAL_PAD_1D6`, `PUMP4_HOPD_VALVE_POS_PROCESS_VALUE`, `PUMP4_POWERFATCOR`,
    `PUMP4_spiral_case1`. All present in the real snapshot. `flatten_snapshot` already emits these
    as `/dex/<TAG>` and `plan_dictionary` already attributes them to a pump by prefix — **the
    pipeline supports the mimic with no change**. The gap is dictionary coverage, hence T18.
  - **`/sl` is the common forebay level — the surge-pool question is closed.** `/sl` equals
    `dex.COMMAN_FORBAY_LEVEL` bit-for-bit in all four available samples (132.0436248779297,
    132.0512237548828, 132.1136245727539, 132.45159912109375). Image 2 shows a single forebay drawn
    off the river feeding all bays. `/sl` may be approved as a level in metres. It is a *duplicate*
    of the `dex` tag; bind one, and draw one.
  - **Vibration is mm/s, closing half of D9.** Image 2's "Motor Vibration 2.1 mm/s" settles casing
    velocity over shaft displacement for the motor DE/NDE points. **Bearing pad** vibration
    (`PMP_THRST_BRG_VBRTN*`) is still unconfirmed and stays withheld.
  - **Reactive power is MVAR**, as suspected. The unit registry rejects `MVar`/`Mvar`/`var`, so this
    stays withheld until `var` is added to the custom registry — the blocker is ours, not the
    plant's. Image 2 confirms the quantity is genuinely reactive power, so recording it under `MVA`
    would have been a false statement.
- **D19 — RESOLVED 2026-09-13.** The estate is **10–12 pumphouses**. *Lakshmi Pump House*
  (17 pumps, Kaleshwaram KLIP, Godavari) in image 2 is **one of them, and is not the station we
  built against**; our registered station is `PH_3` / *Effluent Pump Station 03*. So the 14 slots
  and the 17 in the picture were never in conflict — they are different pumphouses. The station
  **name remains provisional and is not blocking**; `rename_station` changes a label safely by
  source identity whenever the plant supplies real names.
  What this *does* change is scope: **nothing may assume one station or a fixed pump count.**
  Verified on 2026-09-13 — the backend is already clean (no `PH_3` or `14` outside a comment;
  `IngestionCheckpoint` is unique on `(source, station_uuid)`; the Cosmos partition key is
  `/station_uuid` + `/hour_bucket`; `plan_dictionary` derives pump slots from `pd`, capped at 100).
  The gaps are **T9** (corrected above — iterate checkpoints, not sources) and **T19** below.
  For the mimic: the bay row is rendered from `pd`, **never from a constant** — Lakshmi's 17 with
  gaps at 07/08/11/12 shows the numbering is sparse, so bays are *keyed* by pump key, not indexed.

Resolved since the last revision: **D14** (account `epconchatcosmos9d6b`, RG `EpconChat`, database
`aimms`, container `pumphouse_readings` created and verified) and **D7** (a real snapshot confirmed
`dv`/`pmw`/`pmvar`/`pc`, at both station and pump level).

### Critical path
`T8 → T9 → T13 → T14` for the live read, with `T0 → T11 → T12` feeding the UI, and
`T11 → T18 → T15/T16 → T17` feeding the mimic dashboard. Nothing on the critical path is blocked by
an answer except **T18**, which needs an untrimmed production snapshot, and **T15**, which needs D19
reconciled before anyone draws bays.

## Out of scope this sprint
Cassandra → Cosmos migration/CDC job · `pumphouse_latest` maintenance · change-feed polling ·
retention/TTL policy values · writing to any control system (never in scope)

## Open decisions
`D7b` `dsc` code set · `D9` units and alarm bounds (Annex A is a proposal, not plant authority) ·
`D11` freshness threshold · `D16` data-plane role assignment. Defaults for each are recorded in
`plan.md` §8.
