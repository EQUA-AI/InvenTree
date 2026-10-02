# Handover — AIMMS / Cosmos pumphouse connector

**Date:** 2026-09-13
**Branch:** local `IoT`, isolated worktree `InvenTree-IoT`; implementation commits are local.
**Upstream:** `origin/IOT`; opening a PR requires prior human review.
**Commit policy:** commit every completed section to `IoT`; check `git status` for current state.

This document exists so the next engineer can pick the sprint up cold. Read it with
[`plan.md`](./plan.md) (architecture and decisions) and [`tasks.md`](./tasks.md) (the ticket list).
Where the two disagree with this file, this file is newer.

---

## Remembered project constraint — confirmed by user 2026-09-13

The user does **not currently have** the untrimmed station snapshots, station inventory,
or the two mimic reference images. Do not repeatedly request these inputs or invent
plant facts. The user subsequently authorized completing all software with provisional
geometry and configurable mappings. The receiving developer will supply the missing inputs
and make plant-specific corrections. Those inputs block plant acceptance, not implementation;
the existing abridged fixtures are suitable only for tests.

## Full implementation handoff — subsequent user instruction

The user has authorized implementing all remaining sections with provisional mappings and
schematics so the developer with snapshots can finish plant-specific verification. Missing
inputs no longer block implementing the tools or UI. They still prevent claiming verified
plant coverage, real station onboarding, or production readiness. Do not manufacture real
station identities, source readings, units, thresholds, or reference-image fidelity.

**T15 implementation complete:** shared versioned layout JSON, generic station/pump SVGs and
`validate_pumphouse_layout` coverage checks are implemented. The layout is deliberately
`provisional`; every unresolved pointer is reported, and approved points outside the drawing
are listed with a reason (they remain available in the detail table). Two asset/escaping tests
passed. Plant review must update the exact pointers/geometry and approve the layout.

**T16 implementation complete:** the scoped station mimic endpoint returns sparse bay keys,
selected-unit values, source status, reviewed-point groups, explicit null reasons and actual
threshold alarms. Totals prefer a reviewed station measurement or sum every registered bay
with unit conversion; missing/stale/bad contributors produce null. API/activation regression
tests and type checks passed. No network calls occur during mimic reads.

**T18 tooling complete:** `export_dictionary_review --station <pk>` emits exact-path review
packs, including unresolved tags and portable catalogue crosswalks. Applying a pack checks its
dictionary hash and station identity, rejects conflicting decisions, and supports explicit
part/component/parameter mappings. Withholding a formerly approved point now revokes approval
and disables its live binding. 27 review tests passed, including a 700-tag dictionary export.
The developer must still populate and approve the real full dictionaries; no readings or
plant-specific mappings were fabricated.

**T17 implementation complete:** the Pumphouse mimic tab is mounted on registered station
pages. It renders overview/unit geometry, sparse selectable bays, reviewed point groups,
source status, complete-input totals and actual threshold alarms. Stale/disabled/failed reads
never display cached values as current; hidden panels pause polling. Three isolated Chromium
checks passed (selection/pointer binding, stale/disabled, network failure/hidden view); TypeScript,
Biome and Lingui extraction/compilation passed. The browser checks are included in CI and
require no live backend. The provisional banner remains until the layout receives plant review.

**T19 implementation complete:** `onboard_pumphouse_estate` registers sparse station/pump
inventories, imports snapshots, applies explicit review packs and activates one account source
with separate station checkpoints. Dry runs roll back; a failed station rolls back the estate;
replays preserve registered UUIDs and polling progress. Review packs can be replayed after
application but are refused after source observations change. `check_pumphouse_readiness`
reports local configuration/coverage gaps and optionally probes connectivity, without claiming
production acceptance. `benchmark_pumphouse_reads` measures bounded, read-only query RU and
duration without ingesting values or moving checkpoints.

**Receiving developer:** start with [`contrib/cosmos/HANDOFF.md`](../../contrib/cosmos/HANDOFF.md).
It includes manifest/review examples, exact pointer configuration (including numbered `dex`
keys), layout validation, activation, readiness and benchmark commands. Missing snapshots,
inventory, reference images, approved units/thresholds/status codes and the deployed identity
remain explicit plant/platform acceptance work. No production onboarding is claimed.

## Final local validation record

- Combined regression run: 277 tests, successful with one opt-in emulator test skipped.
- Follow-up connector/estate run: 61 tests passed, including the real SDK emulator test,
  RU metadata handling and incomplete-window checks.
- Three Chromium mimic tests passed, including numeric `dex` pointer substitution, stale and
  disabled values, failed requests hiding cached data, and hidden views pausing polling.
- TypeScript, backend `ty`, Lingui extraction/compilation and migration consistency passed.
- API schema generation succeeded and includes the station mimic endpoint. The repository
  still reports pre-existing schema diagnostics (4 warnings, 122 unique errors); this is not
  a claim that the entire repository schema is clean.
- Commit hooks run on each completed section. Tests use isolated SQLite and a disposable
  loopback Cosmos emulator; they do not establish PostgreSQL lock scheduling or production
  Azure identity/capacity. The emulator omits RU headers, which are reported as unknown.

## Implementation continuation — local `IoT` (2026-09-13)

The worktree now starts from remote `IOT` commit `9576f17f39`; no commits from
`equa/customizations` were imported. The older `inventTree-aniket` checkout and pull
instructions below are historical. Continue on local `IoT`.

**T9 is implemented**, including prerequisites the earlier multi-station assessment missed:

- `assets.tasks.poll_cosmos_pumphouse_sources` is registered every minute and returns
  before database access or connector construction while the default-off flag is disabled.
- Checkpoints explicitly link to a local registered `station`. Their `station_uuid`
  remains the **source entity UUID**, not `AssetMachine.uuid`. Ingestion rejects absent
  or mismatched ownership before network access. Bindings are resolved within that station
  and its children; duplicate pointers inside that scope fail rather than choosing a row.
- Scheduled attempts keep per-station success/error timestamps and fixed error codes.
  Least-recently attempted stations go first, including after failure or budget exhaustion.
  A conditional 120-second database lease prevents overlapping station polls and recovers
  after a worker crash. Poll budgets are 20 seconds/station, 50 seconds/sweep, at most
  200 documents/station (a lower `max_docs_per_poll` is honoured).
- SDK requests have a five-second timeout, bounded by remaining worker time; automatic
  SDK retries are disabled so the next scheduled poll owns retry. Time limits are checked
  between requests/documents/batches; an in-flight operation must return before Python can
  check its deadline. This is cooperative budgeting, not process preemption.
- `scan_until` records the exclusive end of fully scanned ranges, separately from the last
  accepted sample. Empty hours can advance it. A five-minute overlap revisits recent delayed
  documents; arrivals behind the accepted sample or older than that overlap need a separate
  backfill policy. A failed, interrupted or capped range is not marked fully scanned.
- All batches of one snapshot and its accepted checkpoint commit atomically. Rejected
  readings fail the snapshot. Errors expose `CONFIG`, `SNAPSHOT`, or `INGEST` where appropriate,
  in addition to the existing provider codes.
- History reads derive station scope from the requested machine and its linked checkpoint.
  Connector and credential transports are closed after scheduled and trend reads.
- Newly created Cosmos sources default to 300-second freshness; explicit values and
  existing rows are preserved. Check existing sources before enabling them.

**Validation:** 200 tests passed across `assets.test_tasks`,
`assets.test_ingestion_checkpoint`, and `machine_health.tests`, using an isolated SQLite
test database with `--keepdb`. This includes the twelve-station failure/isolation case,
scoped history, lease recovery, deadline interruption, delayed documents and snapshot rollback.
No real Cosmos or emulator integration was run here; that remains T13.

**T10 completed:** `import_pumphouse_dump <file> --station <local pk> --source <pk>`
accepts Cosmos rows, Cassandra `data1` rows and the seed-file snapshot envelope. It validates
explicit source/station identity, bounds files to 8 MiB / 2000 rows, uses `flatten_snapshot`
and `in_batches`, and imports the entire file atomically. `--dry-run` rolls back all writes;
pure replay also preserves source timestamps. The command never contacts Cosmos or moves
polling checkpoints. Validation: 37 importer/normalizer tests passed; type checks passed.

**T11 completed:** activation status/preview (`GET .../activate/?source=<pk>`) and
hash-locked `POST`/`DELETE` now bridge approved dictionary points into live bindings.
`HealthSource.client` must be explicitly assigned by a deployment administrator; migration
`0014_station_activation` leaves legacy sources unassigned, so they are not offered to users.
Only same-Client sources configured for the station source UUID are offered. New checkpoints
start five minutes before activation; existing cursors are preserved. Deactivation pauses
polling and removes only this station/source's dictionary-managed bindings. Manual bindings
are preserved. Unit/type/owner review is revalidated; thresholds stay unset until confirmed.
Review revocation or remapping clears cached state and disables affected bindings immediately.
Activation and polling lease claims share a station lock; active polls reject activation edits.
Status responses contain no endpoint, credentials or credential reference.

**Validation:** 253 backend tests passed, including activation, registry/review, scheduling,
offline import and connector regression coverage; type checks and migration consistency passed.
The tests use isolated SQLite and do not prove PostgreSQL lock scheduling or Azure integration.

**T12 completed:** the Dictionary and review tab has a live-source card with authorized
source selection, hash-locked activation/deactivation, counts, last poll/error and refresh.
The banner distinguishes no bindings, activated-but-paused and polling-enabled states.
Status refreshes every 30 seconds. Client reassignment hides previously linked sources and
bindings from the former Client; inactive/unconfigured stations are not shown as polling-enabled.
Validation: TypeScript and Biome passed; Lingui catalogs extracted/compiled; Chromium smoke
checks passed for activate/deactivate, preview bodies, paused polling and view-only controls.
The 11 activation API tests also passed after the Client-reassignment status regression check.

**T13 completed:** a real-SDK test creates an isolated emulator database with the checked-in
hierarchical partition/index definition, seeds two hour buckets plus another station's row,
resumes after a one-document cap, reads bounded history, and runs the scheduled poll twice.
The latest value and cursor advance once; replay leaves cached state unchanged. This passed
locally against the emulator image pinned by digest in the new CI workflow. The workflow is
added but has not run on GitHub. Tests refuse non-loopback provisioning endpoints and remove
their temporary database. No real Azure account was used.

**T14 documentation completed:** `docs/docs/aimms/cosmos-connector.md` documents
configuration, reviewed activation, the global flag, pause/resume, budgets, failure codes,
offline import, emulator validation and trust boundaries. Existing machine-health and threat
model pages link to it. T14's PR step is not performed: repository instructions require human
review before opening a PR, and no push or PR was requested.

**Current state:** T9–T19 software and operational documentation are implemented locally.
T15 geometry is provisional; T18 full production dictionary review and T19 real estate rollout
belong to the receiving developer's acceptance work. The global flag remains off by default.
No production migrations, cloud changes, push or PR opening have been performed.

Completed implementation commits: T9 `d208867a0`, T10 `136f8acd7`, T11 `e069904c2`,
T12 `c3530ef18`, T13 `2e0b59f5f`, T15 `48e92d21c`, T16 `397aa663b`,
T18 `bc503d3c8`, T17 `2dd6d05f5`, T19 `f1d136c84`. See branch history for the final handoff commit.
All original estimates, next-ticket directions and deployment claims below are historical.

---

## 1. Original handover snapshot (superseded by the continuation above)

### 1.1 One-paragraph summary

Feature 002 builds a **read-only** connector that reads pumphouse telemetry out of Azure Cosmos DB
(NoSQL API) and turns it into `MachineSignalState` inside InvenTree. The Cassandra → Cosmos
migration is *not* in this sprint (decision D2); documents are hand-seeded. Everything up to and
including the connector itself is built and tested (T0–T8, 43 h). What is left is wiring it into the
scheduler, giving operators a way to activate a station, and proving it end to end (T9–T14, 26 h),
the pumphouse mimic dashboard that the original plan omitted entirely (T15–T18, 31 h), and rolling
out to the rest of the **10–12 station estate** (T19, 6 h). Remaining work is **63 h**.
Nothing on the critical path is blocked on an answer any more — only on someone doing the work, plus
one Azure role assignment (D17) before go-live.

### 1.2 Ticket board

| ID | Title | Est | Status |
|---|---|---|---|
| T0 | Register PH_3 + import pilot dictionary | 2 h | ✅ done |
| T1 | Accept real Cassandra hour-bucket types | 4 h | ✅ done |
| T2 | `azure-cosmos` dependency | 2 h | ✅ done |
| T3 | Cosmos schema artefacts + verifier | 6 h | ✅ done (one deferred sub-item, §1.4) |
| T4 | Cosmos emulator in the dev stack | 3 h | ✅ done |
| T5 | Manual seeder + pilot documents | 6 h | ✅ done, seeded to the real account |
| T6 | `flatten_snapshot()` normalisation | 7 h | ✅ done |
| T7 | `IngestionCheckpoint` model | 3 h | ✅ done |
| T8 | `CosmosPumphouseConnector` | 10 h | ✅ done, 53 tests |
| **T9** | **Scheduled poller** | **6 h** | 🔵 **next — start here** |
| **T10** | **`import_pumphouse_dump` command** | **3 h** | 🔵 ready, needs no Azure access |
| **T11** | **Registry → live bridge (`activate/`)** | **5 h** | 🔵 ready — its data blocker cleared, §1.3 |
| T12 | Live-source UI card | 5 h | ⏸ blocked on T11 |
| T13 | End-to-end integration test on the emulator | 4 h | ⏸ blocked on T9 |
| T14 | Docs + PR to `IOT` | 3 h | ⏸ blocked on all of the above |
| T15 | Mimic layout contract + overview & unit SVGs | 8 h | ⏸ blocked on T18 |
| T16 | Station mimic state API | 5 h | ⏸ blocked on T9, T11 |
| T17 | `PumphouseMimic.tsx` live dashboard | 10 h | ⏸ blocked on T15, T16 |
| T18 | Full `dex` dictionary import + review | 8 h | 🔴 needs an untrimmed production snapshot |
| T19 | Onboard the rest of the estate (10–12 pumphouses) | 6 h | ⏸ blocked on T11 |

**Critical path:** `T9 → T13 → T14` for the live read, `T11 → T12` for the admin UI,
`T11 → T18 → T15/T16 → T17` for the mimic dashboard, and `T11 → T19` for the estate.
T10 and T11 are independent of T9 and can be done in parallel by a second pair of hands.

> **T15–T19 were added on 2026-09-13 and are not in the original 67 h estimate.** Two things were
> missing: the pumphouse schematic from the reference images, and the fact that the estate is
> **10–12 pumphouses**, not one. Remaining work is **63 h, not 24 h**. This is the single biggest
> correction in this handover: do not quote the old number.

> **The estate is 10–12 pumphouses.** *Lakshmi Pump House* (17 pumps, Kaleshwaram KLIP) in image 2
> is one of them and is **not** the station built against — that is `PH_3` / *Effluent Pump Station
> 03*. The backend was checked on 2026-09-13 and carries no single-station or fixed-pump-count
> assumption: no `PH_3`/`14` literals outside one comment, `IngestionCheckpoint` unique on
> `(source, station_uuid)`, Cosmos partitioned on `/station_uuid` + `/hour_bucket`, pump slots
> derived from `pd`. **T9's draft did assume one station per source and has been rewritten.**
> Station names are provisional; `rename_station` relabels safely by source identity, so real plant
> names can land at any time without touching identity, bindings or dictionary points.

### 1.3 Work landed after `tasks.md` was last revised

Three commits are on the branch that `tasks.md` does not yet list. Fold them in when you next
touch it.

- `e13c0b9b9` **`rename_station`** — `src/backend/InvenTree/assets/management/commands/rename_station.py`
  plus `rename_station()` in `assets/registry.py`. Changes a station's human label and its pump-slot
  labels only; `uuid`, `source_namespace`, `source_entity_uuid` and `source_key` are identity and are
  never touched, so re-import, dictionary points and bindings keep resolving. The station is looked
  up by source identity, not by the name being replaced. 215 lines of tests.
- `1d4d7313f` **`apply_dictionary_review`** — applies a version-controlled review file
  (`contrib/pump-cassandra/PH_3.review.json`) instead of clicking 31 tags through a modal. Validation
  is deliberately identical to the HTTP review endpoint so the bulk path cannot launder unreviewed
  mappings past the gate. 366 lines of tests.
- `4fe9c9708` **i18n extraction** — the AIMMS frontend strings are now in all 39 locale catalogues.
  Only `en` has translations; the rest carry empty `msgstr`, which is correct and is Crowdin's job.

**This clears the "31 dictionary points must be reviewed" blocker on T11.** The review file settles
all 31: **25 approved** across 9 entries, **6 withheld** across 4 entries. Units are taken from the
catalogue parameter template and never inferred from sample values. The withheld six, with the
reason each is withheld:

| Path(s) | Why withheld |
|---|---|
| `/sl` | Surge pool level — no component/parameter assigned and the catalogue has no such parameter |
| 3 × `/dex/…VIBRATION…` | Bearing vibration — catalogue defines no unit; ISO 20816 covers both µm and mm/s and the tag name cannot settle it (this is **D9**) |
| `/dex/PUMP5_PUMP_REACTIVE_POWER` | Belongs in MVar to match station-level `pmvar`; the unit registry rejects `MVar`/`Mvar` |
| `/dex/PUMP14_PUMP_THRUST_AXIAL_PAD_1D5` | Data type `unknown`, tag ambiguous between pad temperature and displacement |

Withheld ≠ rejected ≠ unresolved. T11 must bind **only** `status='approved'` points; the withheld six
must stay unbound and must read `unknown`, never a fabricated `normal`.

### 1.4 Known stale / deferred items

- `contrib/cosmos/README.md:289` still says *"Still to come (ticket T5): `seed.py` plus sample
  documents."* T5 shipped in `4520ea38c`. Fix this line.
- T3 has one unchecked sub-item: `schema/pumphouse_latest.container.json`, deferred with plan §3.2.
  `pumphouse_latest` maintenance is explicitly out of scope this sprint — leave it deferred.
- `specs/001-repair-playbooks/spec.md` is an **unfilled Spec-Kit template**. Feature 001 has not been
  started; there is no plan, no tasks, no research. Confirm with the product owner whether it is
  parked or queued behind 002 before anyone assumes it is in flight.

---

## 2. Start here — the next three tickets in detail

### T9 — Scheduled poller (6 h) · *the critical path*

Create `src/backend/InvenTree/assets/tasks.py`. It does not exist yet; that file being absent is the
cleanest signal that T9 is untouched.

- [ ] `poll_cosmos_pumphouse_sources()` registered at `ScheduledTask.MINUTES, 1` (Django-Q2)
- [ ] `AIMMS_COSMOS_PUMPHOUSE_ENABLED` kill-switch, **default off**, read via `get_boolean_setting`.
      Follow the existing `AIMMS_CLOSEOUT_*` settings as the pattern.
- [ ] **Iterate `(source, station_uuid)` checkpoints, not sources.** The estate is 10–12 pumphouses
      behind one account; `IngestionCheckpoint` is unique on `(source, station_uuid)` and `poll()`
      takes a checkpoint, so the connector already supports this — but a loop over `HealthSource`
      would hand eleven pumphouses one shared cursor.
- [ ] **Per-station** time and document budget, a whole-run budget, and a **rotating start point** so
      a slow or erroring station near the front cannot starve those behind it every minute
- [ ] One station's failure must not abort the rest of the sweep
- [ ] `record_source_error()` on failure, **per station** — "the account is down" and "pumphouse 7 is
      down" are different operational facts
- [ ] `freshness_threshold_seconds` default **300 s** (D11)

**Acceptance:** with the flag off, the task issues **zero** network calls. Assert that in a test.
With twelve checkpoints and one erroring, the other eleven still ingest, and the failing one is not
retried first forever.

**Watch for:** T8 fixed a bug where the connector forwarded its `now` (the *source-clock* read
horizon) into `ingest_readings(now=...)` (the *server clock* skew is measured against). `USE_TZ` is
off in this project, so mixing them raises `can't subtract offset-naive and offset-aware datetimes`
on every batch, and `_classify` would have mislabelled it `NETWORK` in production. Two tests pin it.
Do not reintroduce it in the poller.

### T10 — `import_pumphouse_dump` (3 h) · *no Azure access needed, good first ticket*

- [ ] JSON rows → `flatten_snapshot` → `ingest_readings`, with `--dry-run`
- [ ] Reuse the T6 path exactly. **Do not write a second normaliser.**

**Acceptance:** importing the same file twice changes nothing the second time.

### T11 — Registry → live bridge (5 h)

Closes the gap where "mapping approval does not enable live ingestion".

- [ ] `POST /api/assets/registry/<pk>/activate/` taking `{"source": <HealthSource id>}` — the route
      is not in `assets/registry_api.py` yet
- [ ] Create/refresh `MachineSignalBinding` for every `DictionaryPoint(status='approved')`; rejected,
      withheld and unresolved points are never bound
- [ ] Idempotent and hash-locked like import; deactivation removes only that station's bindings
- [ ] Units seeded from `DictionaryPoint.unit`, else Annex A; thresholds left unset when unconfirmed

**Acceptance:** activating twice creates no duplicate bindings; a non-approved point never appears.
Station PH_3 is `pk 17`, 14 pump slots / 30 components / 31 points.

---

## 3. Environment and access

### 3.1 Live coordinates of record

| | |
|---|---|
| Account | `epconchatcosmos9d6b` |
| Resource group | `EpconChat` |
| Database | `aimms` (400 RU/s shared manual throughput — no extra cost, D15) |
| Container | `pumphouse_readings` |
| Partition key | hierarchical: `/station_uuid` + `/hour_bucket` |
| TTL | `defaultTtl: -1` |

### 3.2 Local development

The emulator runs under the `cosmos` profile in `contrib/container/dev-docker-compose.yml`
(vNext multi-arch image, plain HTTP on 8081). Compose commands need `--project-directory .` — see
`contrib/cosmos/README.md`.

```
contrib/cosmos/provision.py   # verify/diff.  --from-json (offline) | --live | --emulator
                              # --create is REFUSED against anything but the emulator
contrib/cosmos/seed.py        # validates §3.1 invariants, then upserts.  --dry-run
contrib/cosmos/samples/ph3_snapshots.json   # 3 docs, two hour buckets, one I → R transition
```

Both scripts are pure stdlib and their tests (11 + 28) run offline. `provision.py` never grants a
role and never writes a document.

**The emulator key goes in `COSMOS_EMULATOR_KEY` and nowhere else.** Against a real endpoint an
account key is refused outright — auth is Entra ID via `DefaultAzureCredential`.

### 3.3 D17 — the one thing to sort before go-live

D16 is **resolved**: a data-plane role exists and was verified by reading container properties and by
seeding and querying back the pilot documents with the connector's own query shape.

**But what was granted is not what the application should run as.** The assignment is role
`00000000-…-000000000002` = **Data Contributor** (read *and* write), scoped to the **whole account**,
on the developer principal `f024cd79-…`. That is right for a human who has to seed documents —
seeding writes, so Data Reader could not have done it. It is wrong for the app. If AIMMS
authenticates as an identity holding Data Contributor, the property the whole design leans on — that
read-only is enforced by *Azure*, not by our own code — is gone, and a connector bug could delete
plant history.

**Action for whoever owns Azure:** assign role `00000000-…-000000000001` (**Cosmos DB Built-in Data
Reader**) to the application's managed identity, scoped to `/dbs/aimms/colls/pumphouse_readings`,
not to the account. Then answer: which identity does AIMMS run as, per environment?

---

## 4. Open decisions still needing a human

| ID | Question | Default in force | Who can answer |
|---|---|---|---|
| **D17** | App identity + Data Reader role, container-scoped | none — dev's Data Contributor is being used | Azure/platform owner |
| **D18** | Mimic layout vs the reference images | **RESOLVED 2026-09-13** — see `tasks.md`; mimic is a `dex` view, `/sl` == forebay level, motor vibration is mm/s | — |
| **D19** | Real plant names + station list for the estate (10–12 pumphouses): `entity_uuid`, SCADA code, name, pump count | **RESOLVED as a conflict** — Lakshmi is a *different* pumphouse, not PH_3. Names stay provisional; `rename_station` applies real ones safely. Blocks **T19** only | Plant / SCADA owner |
| **D9** | Vibration units (µm vs mm/s) and alarm bounds | **Motor** DE/NDE settled as **mm/s** by image 2 ("Motor Vibration 2.1 mm/s"); **bearing pad** vibration still unconfirmed and withheld. Alarm bounds still need the plant's alarm/trip CSV — Annex A is a **proposal**, not plant authority | Plant engineering |
| **D7b** | `dsc` code set meanings | unmapped | Plant / SCADA vendor |
| **D11** | Freshness threshold | 300 s | Ops |
| — | `component_type=65`, `event_value_type=41` enum meanings | undocumented | SCADA vendor |

**Do not clamp suspect values.** `-242.1`, `3276.7`, valve positions below 0 or above 100 are *not
confirmed faults*. Until someone says otherwise they pass through as read.

**Do not draw what we do not receive.** Same rule, applied to T15–T17: anything in the reference
images with no approved `DictionaryPoint` behind it is static geometry. A mimic that appears to show
a valve position or a vibration level we never read is a worse outcome than one that omits it.

`contrib/pump-cassandra/` is an **offline draft proposal**, not a connector — Gate 1 in its README is
still partly open (`data2` purpose, `dsc` codes, quality conventions, and which keys carry total
discharge / input power / running-pump count). Gate 5 is *superseded*: the live read is being built
against Cosmos, not Cassandra.

---

## 5. Ground rules — please keep these

From `AGENTS.md` / `CLAUDE.md` (identical files):

> **Do not open a pull request without manual review by a human. Do not file AI generated issues
> under any circumstances.** AI-generated content in issue/PR discussion may lead to Code of Conduct
> violations.

Security reports follow `docs/docs/SECURITY.md` and must state how the issue interacts with the
threat model.

**Definition of Done for every ticket** (`tasks.md` lines 6–8):

1. Unit tests for the new behaviour
2. `prek run --files <changed>` clean
3. `ty` clean on the touched files
4. **No credential in code, config, fixture or log**
5. Commit on `inventTree-aniket` with a message that says *why*, not what

Backend (Django/DRF, Django-Q2) and frontend (React 19 / Mantine 9 / Vite / Playwright / Lingui) are
separate toolchains — be explicit about which layer a change touches.

### `specs/` is gitignored — new files here need `git add -f`

`.gitignore:70` is a bare `specs/` with no negation rule. The four spec files are tracked (tracking
beats the ignore rule, so editing and committing them works normally), but **a file you add to
`specs/` will be silently skipped** by `git add specs/`, `git add .` and `git commit -a`. It will not
show in `git status` either, so nothing warns you. Use:

```bash
git add -f specs/002-cosmos-pumphouse-connector/
git ls-files specs/           # confirm the new file is listed
```

`git check-ignore specs/<file>` prints nothing for *tracked* files, which reads like "not ignored" and
is misleading — pass `--no-index` to see the real rule. Currently tracked:

```
specs/001-repair-playbooks/spec.md
specs/002-cosmos-pumphouse-connector/HANDOVER.md
specs/002-cosmos-pumphouse-connector/plan.md
specs/002-cosmos-pumphouse-connector/tasks.md
```

---

## 6. Out of scope this sprint

Cassandra → Cosmos migration/CDC · `pumphouse_latest` maintenance · change-feed polling ·
retention/TTL policy values · **writing to any control system — never in scope.**

---

## 7. File map

**Sprint docs**
- `specs/002-cosmos-pumphouse-connector/plan.md` — architecture §3 schema, §4 connector, §7 backlog
  and milestones, §8 decisions, §9 risks, Annex A units
- `specs/002-cosmos-pumphouse-connector/tasks.md` — T0–T14 with ✅/🔵/⏸ legend
- `specs/001-repair-playbooks/spec.md` — empty template, not started

**Backend**
- `machine_health/connectors/cosmos_pumphouse.py` — T8, 518 lines. Error vocabulary
  `AUTH|NOT_FOUND|THROTTLED|NETWORK|OK` plus `CONFIG`. All queries parameterised and
  single-partition; cross-partition explicitly disabled; a partial partition key raises before a
  request is built. No endpoint, tag or provider message is ever logged or persisted.
- `machine_health/connectors/pumphouse_payload.py` — T6 `flatten_snapshot()`, `in_batches()`
- `machine_health/connectors/base.py` — connector registry
- `assets/ingestion_models.py` — T7 `IngestionCheckpoint`, migration `0012_ingestioncheckpoint`
- `assets/registry.py` — `epoch_ms()`, `rename_station()`, import
- `assets/registry_api.py` — **T11's `activate/` route goes here**
- `assets/management/commands/rename_station.py`, `…/apply_dictionary_review.py`
- `assets/tasks.py` — **does not exist; T9 creates it**
- `src/backend/requirements.in:88-92` — `azure-cosmos>=4.9.0`; `azure-identity` floor raised to
  ≥1.16.1 for **CVE-2024-35255**

**Frontend**
- `src/frontend/src/pages/assets/EquipmentRegistry.tsx:373` — the offline banner is hard-coded here.
  T12 makes it conditional on activation.

**Ops**
- `contrib/cosmos/` — schema JSON, `provision.py`, `seed.py`, samples, tests, README runbook
- `contrib/container/dev-docker-compose.yml` — `cosmos` emulator profile
- `contrib/pump-cassandra/` — PH_3 mapping draft, review file, gates README

---

## 8. First hour for the next engineer

1. `git checkout inventTree-aniket && git pull` — you should land on `1d4d7313f`.
2. Read `plan.md` §3 (schema) and §4 (connector design). They are the load-bearing sections.
3. Bring up the emulator: `docker compose --project-directory . -f contrib/container/dev-docker-compose.yml --profile cosmos up -d`
4. `python contrib/cosmos/provision.py --emulator --create` then `python contrib/cosmos/seed.py --dry-run`
   against `samples/ph3_snapshots.json` to confirm your local loop works.
5. Run the existing suites — `test_cosmos_pumphouse.py` (53), `test_seed.py` (28), `test_provision.py` (11),
   `test_dictionary_review.py`, `test_station_rename.py`, `test_ingestion_checkpoint.py`.
   All should be green before you write a line.
6. Start T9.

Chase D17 in parallel on day one — it is the only item with an external dependency and the longest
lead time.
