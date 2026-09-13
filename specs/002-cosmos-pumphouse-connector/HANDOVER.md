# Handover — AIMMS / Cosmos pumphouse connector

**Date:** 2026-09-13
**Branch:** `inventTree-aniket` (pushed, up to date with `origin/inventTree-aniket` at `1d4d7313f`)
**PR target at sprint end:** `IOT`
**Working tree:** clean — nothing uncommitted, nothing stashed.

This document exists so the next engineer can pick the sprint up cold. Read it with
[`plan.md`](./plan.md) (architecture and decisions) and [`tasks.md`](./tasks.md) (the ticket list).
Where the two disagree with this file, this file is newer.

---

## 1. Where the work stands

### 1.1 One-paragraph summary

Feature 002 builds a **read-only** connector that reads pumphouse telemetry out of Azure Cosmos DB
(NoSQL API) and turns it into `MachineSignalState` inside InvenTree. The Cassandra → Cosmos
migration is *not* in this sprint (decision D2); documents are hand-seeded. Everything up to and
including the connector itself is built and tested (T0–T8, 43 h). What is left is wiring it into the
scheduler, giving operators a way to activate a station, and proving it end to end (T9–T14, 24 h),
plus the pumphouse mimic dashboard that the original plan omitted entirely (T15–T18, 31 h — see the
note under the ticket board). Remaining work is **55 h**.
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
| **T9** | **Scheduled poller** | **4 h** | 🔵 **next — start here** |
| **T10** | **`import_pumphouse_dump` command** | **3 h** | 🔵 ready, needs no Azure access |
| **T11** | **Registry → live bridge (`activate/`)** | **5 h** | 🔵 ready — its data blocker cleared, §1.3 |
| T12 | Live-source UI card | 5 h | ⏸ blocked on T11 |
| T13 | End-to-end integration test on the emulator | 4 h | ⏸ blocked on T9 |
| T14 | Docs + PR to `IOT` | 3 h | ⏸ blocked on all of the above |
| T15 | Mimic layout contract + overview & unit SVGs | 8 h | ⏸ blocked on T18, **D19 first** |
| T16 | Station mimic state API | 5 h | ⏸ blocked on T9, T11 |
| T17 | `PumphouseMimic.tsx` live dashboard | 10 h | ⏸ blocked on T15, T16 |
| T18 | Full `dex` dictionary import + review | 8 h | 🔴 needs an untrimmed production snapshot |

**Critical path:** `T9 → T13 → T14` for the live read, `T11 → T12` for the admin UI, and
`T11 → T18 → T15/T16 → T17` for the mimic dashboard.
T10 and T11 are independent of T9 and can be done in parallel by a second pair of hands.

> **T15–T18 were added on 2026-09-13 and are not in the original 67 h estimate.** The sprint was
> scoped end-to-end on *ingestion*; the pumphouse schematic from the two reference images — the
> screen an operator actually watches — had no ticket. Remaining work is **55 h, not 24 h**. This is
> the single biggest correction in this handover: do not quote the old number.

> **Read D18 and D19 before touching the mimic.** The images were analysed against the real payload
> on 2026-09-13. They closed three open questions and opened one serious one — including whether the
> station we named "Effluent Pump Station 03" is a Godavari lift-irrigation pumphouse with 17 pumps.

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

### T9 — Scheduled poller (4 h) · *the critical path*

Create `src/backend/InvenTree/assets/tasks.py`. It does not exist yet; that file being absent is the
cleanest signal that T9 is untouched.

- [ ] `poll_cosmos_pumphouse_sources()` registered at `ScheduledTask.MINUTES, 1` (Django-Q2)
- [ ] `AIMMS_COSMOS_PUMPHOUSE_ENABLED` kill-switch, **default off**, read via `get_boolean_setting`.
      Follow the existing `AIMMS_CLOSEOUT_*` settings as the pattern.
- [ ] Per-source time budget ≤ 20 s and a document cap, so one slow account cannot starve the worker
- [ ] `record_source_error()` on failure; `freshness_threshold_seconds` default **300 s** (D11)

**Acceptance:** with the flag off, the task issues **zero** network calls. Assert that in a test.

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
| **D19** | Is image 2 ("Lakshmi Pump House, 17 pumps, Kaleshwaram KLIP, Godavari river") actually PH_3? If so, both the name *Effluent Pump Station 03* and the 14-slot registration are wrong | 14 slots from `pd` P1–P14; name applied from a US-style naming request | Whoever supplied the images + plant |
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
