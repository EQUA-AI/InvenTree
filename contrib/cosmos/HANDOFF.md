# Developer handoff: complete the plant configuration

The remaining feature code is implemented on local `IoT`: editable mimic geometry and
layout validation, station mimic API/UI, full-dictionary review packs, atomic estate
onboarding, deployment checks, and a read-only poll-cost benchmark. The schematic is
provisional. No production identities, snapshots, alarm limits or reference images were
invented. All automated checks use isolated fixtures or a local emulator.

## 1. Prepare the deployment

Install the branch's backend/frontend dependencies and apply its database migrations
through `assets.0014_station_activation` using the deployment's normal migration process.
Keep `AIMMS_COSMOS_PUMPHOUSE_ENABLED=false` during initial mapping and review. Configure
one active `cosmos_pumphouse` Health Source for the account, assigned to the correct Client.
The configuration keys are `endpoint`, `database`, `readings_container` and `stations`.
Check the source freshness setting explicitly; the default for new sources is 300 seconds.

For Azure, leave `secret_ref` empty and provide the application identity through the
deployment environment. The platform owner must verify that this identity has Cosmos DB
Built-in Data Reader at the required container scope and no data writer permission. A
successful read probe does not prove the absence of write permission. The connector has
no equipment-control or document-write operation.

These commands assume the repository root and the deployment's Python environment:

```bash
python src/backend/InvenTree/manage.py check_pumphouse_readiness --source SOURCE_PK --allow-incomplete
python src/backend/InvenTree/manage.py check_pumphouse_readiness --source SOURCE_PK --probe --allow-incomplete
```

The first command is local-only. `--probe` performs a read-only container-properties request.
The report's `ready` describes automated configuration checks; `production_ready` remains
unknown because plant acceptance and identity-role verification require external evidence.
The report never prints connection configuration or credential references.

## 2. Supply the estate manifest and full snapshots

Copy `examples/estate.example.json` into your own working directory. Replace every
placeholder with a real identity and use one record per station. `source_key` must match
`dex.ID`; `source_uuid` is the upstream station entity UUID. `source_namespace` and these
identities are immutable after registration. `pumps` contains exact keys such as `P1` and
`P17`; preserve gaps instead of synthesizing missing bay numbers. Snapshot import also
registers the pump keys actually present in the payload.

Snapshot paths are relative to the manifest file. Accepted dictionary inputs are the same
bounded raw payload/Cassandra export shapes used by the registry. Use full snapshots for
coverage; the committed abridged samples demonstrate tests only.

```bash
python src/backend/InvenTree/manage.py onboard_pumphouse_estate estate.json --source SOURCE_PK --dry-run > preview.json
python src/backend/InvenTree/manage.py onboard_pumphouse_estate estate.json --source SOURCE_PK > registered-crosswalk.json
```

The whole manifest commits or rolls back together. It changes only the selected source's
station allowlist, never its endpoint or credential configuration. It does not enable the
global flag. Retain the UUIDs from the committed crosswalk; generated IDs in a rolled-back
preview are temporary. An optional station `uuid` reserves an already agreed local UUID.
Repeating a committed registration preserves station/pump identities. Compare the real
station count, sparse pump keys, source context and shared parent identity with the inventory.

## 3. Review the full dictionary in files

```bash
python src/backend/InvenTree/manage.py export_dictionary_review --station STATION_PK > station-review.json
```

The pack records the local/source station identities and hashes. Existing approvals go
under `approve`; unresolved points go under `pending`. For each point being approved, move
its entry to `approve` and confirm `data_type`, `unit`, `unit_status` and `note`.

An optional `mapping` supplies an exact catalogue crosswalk:

```json
{
  "part_ipn": "EXISTING_CATALOGUE_IPN",
  "component_code": "REVIEWED_COMPONENT_SLOT",
  "parameter": "EXACT_EXISTING_PARAMETER_NAME"
}
```

The parameter must belong to that catalogue part. Missing component occurrences can be
created under the point's existing equipment owner; an occupied component slot cannot be
silently reassigned. Correct ownership in the registry before applying a different owner
key. This works with unusual source spellings, embedded spaces, and `PMP_`/`PUMP_` variants;
the source path remains exact. Add genuinely missing catalogue definitions first.

Use `withhold` entries of `{"paths": ["/exact/path"], "reason": "why unresolved"}` for
explicitly withheld points. Withholding revokes an existing approval and clears its live
state. Do not supply guessed temperature, flow, vibration, power or alarm units.

```bash
python src/backend/InvenTree/manage.py apply_dictionary_review --review station-review.json --dry-run
python src/backend/InvenTree/manage.py apply_dictionary_review --review station-review.json
```

Changed observations or conflicting decisions invalidate an exported review. An identical
already-applied pack can be replayed. Add `"review": "relative/path/station-review.json"`
to its manifest station after the review is ready.

### Status for station 17 (`PH_3`)

Done: the full snapshot is imported and the review pack is exported.

| Artefact | Path |
|---|---|
| Untrimmed source row | `contrib/pump-cassandra/PH_3.full-snapshot.json` |
| Reshaped for the importer | `contrib/pump-cassandra/PH_3.full-snapshot.rows.json` |
| The reshape | `contrib/pump-cassandra/make_rows.py` |
| Exported review pack | `contrib/pump-cassandra/PH_3.full-review.json` |

The station now holds **905 dictionary points**: 606 exact catalogue matches, 252 by alias,
47 unresolved, and **no** owner/parameter conflicts. The 31 excerpt-era points and their 25
approvals were preserved untouched. 225 component occurrences exist, and every matched point
has one. The pack contains 25 `approve` and 880 `pending` entries.

The unit review has since been done from the observed values and applied: **581 approved,
264 withheld with the reason recorded on the point, 60 left pending.** 581 bindings now
exist. The reasoning is in `contrib/pump-cassandra/UNIT_REVIEW.md` and the applied decisions
in `contrib/pump-cassandra/PH_3.unit-review.json`.

The governing finding is that **the reference snapshot was taken with the station shut
down** - every bay reports `st=I` and `MOTOR_ON_STATUS=0`, with zero power, current and
voltage. A unit is a claim about magnitude, so anything that only has a magnitude while
running cannot be confirmed from it. Those tags were withheld rather than guessed. A
snapshot taken while pumping settles most of them in one reading, and is the single most
useful thing to obtain next.

Confirmed: `degC` for 479 temperature points, `Hz` for 14 (six bays sense 50.0 Hz at the
breaker), `percent` for 28 valve positions clustered at the end stops, and unitless for the
motor status and power factor points. Still open:

- **Everything that only has a magnitude while running.** `ACTIVE_POWER` (kW vs MW differ
  by 1000x), `PUMP_CURRENT_AVG`, `PUMP_LINE_TO_LINE_VOLTAGE` (V predicts ~11000, kV ~11),
  `SPEED`, `DISCHARGE_PRESSURE` (bar, kg/cm2 and metres of head are all plausible). All read
  zero or noise. **Get a running snapshot.**
- **Vibration**, seven families, all reading -0.18 to 0.48. The negatives are informative:
  neither velocity RMS nor displacement can be negative, so these are uncalibrated raw
  channels. mm/s matches the reference images and the ISO 20816 convention for motor DE/NDE,
  but confirm it against a running sample instead of assuming it.
- **Reactive power, 14 points.** Expected MVAR, but blocked regardless: `var` is not in the
  unit registry. The same blocker applies to `/pmvar`.
- **70 points carry `data_type: unknown`** because the *catalogue* declares it so for the
  pad and spiral-case channels, not because they were observed as null - the snapshot has
  no nulls. The open question is what the instrument measures, not its unit. The observed
  values now argue that the pad channels are temperatures and `SPIRAL_CASE1` is a pressure;
  see `UNIT_REVIEW.md`. That is an argument, not a confirmation.
- **`EXCITATION_FLD_CURR` needs explaining.** Thirteen bays read about 0 and P6 reads
  1048.29 with its motor off.
- **Station-envelope keys** `/pc`, `/dv`, `/sl`, `/pmw`, `/pmvar` remain unresolved, with no
  catalogue parameter to attach to. `/sl` is bit-identical to `dex.COMMAN_FORBAY_LEVEL`
  across all four samples, which is evidence of an alias but is not yet an approved one.

Twelve families cover 13 bays rather than 14, every one of them missing **pump 7** - its
discharge-pressure transmitter and all eleven winding RTDs. Recording that as missing is
correct; it must not become zero, and it is not by itself a fault.

## 4. Finish the schematic contract against the references

Edit `src/backend/InvenTree/machine_health/layouts/pumphouse.layout.json` and the two SVGs
in `src/frontend/src/assets/mimic/`. The current drawing is a generic intake/header and
pump/motor arrangement, not a reproduction of the missing installation drawings.

- Add the real pressure, RTD, winding, vibration, bearing, cooling, valve, speed, frequency
  and electrical fields from the approved full dictionary. Confirm HOPD/EOPD interpretation.
- An element defines its unique id, view (`station` or `unit`), pointer, role, label and
  coordinates. Its SVG element must carry the identical id and `data-point` template.
- `{pump}` substitutes the exact bay key (`P17`). `{pump_number}` substitutes its numeric
  suffix (`17`), so `/dex/PUMP{pump_number}_...` resolves without positional indexing.
- The API returns every selected equipment dictionary point in the detail table. Approved
  points without diagram placement are explicitly listed as not drawn in the coverage report.
- Extend `status_values` only after source-code meanings are confirmed. `R` and `I` are the
  known running/idle codes; no fault codes have been fabricated.
- Confirm the total definitions and units. A reviewed direct station reading takes priority;
  otherwise every registered bay must supply a fresh compatible value. The sum does not
  assume that an idle or unavailable bay contributes zero. Missing inputs produce null.
- Set `review_status` to `approved` only after the real drawings and coverage are reviewed.

```bash
python src/backend/InvenTree/manage.py validate_pumphouse_layout --station STATION_PK --allow-incomplete
python src/backend/InvenTree/manage.py validate_pumphouse_layout --station STATION_PK
```

The strict command fails on provisional review, SVG/JSON drift, or a drawn pointer without
approval. Run it for every station; omit `--station` to check the entire registered estate.

### Status: what the reviewed dictionary changed

The drawing was written against 25 approved points. There are now 581, across **59
approved families** - every motor core RTD, winding, cooling water inlet and outlet,
hot and cold air temperature, both valve positions, frequency, power factor and
motor on/off status, each repeated per bay. All of it is currently *not drawn*.

More urgently, **three of the five drawn elements pointed at points the review could
not approve**, which is what fails strict validation:

| Element | Pointer | State |
|---|---|---|
| `forebay` | was `/sl` | **Fixed** - repointed to `/dex/COMMAN_FORBAY_LEVEL`, which is approved in `m`. The two are bit-identical across all four samples, so this draws the reviewed point rather than merging two levels. It now resolves: 132.11 m, quality good. |
| `station-status` | `/st` | approved |
| `pump-status` | `/pd/{pump}/st` | approved |
| `pump-power` | `/pd/{pump}/pmw` | **blocked** - unresolved, no catalogue parameter |
| `pump-flow` | `/pd/{pump}/dv` | **blocked** - unresolved, no catalogue parameter |

`pump-power` and `pump-flow` have no approved equivalent to point at. The nearest,
`/dex/PUMP{n}_ACTIVE_POWER`, is still draft because kW and MW cannot be told apart
from a shut-down plant. Both elements were left in place rather than deleted: they
record intent, and `totals.power` and `totals.flow` correctly report `null` with
`reason: incomplete` rather than inventing a sum. They will resolve once a running
snapshot settles the power unit, or once a reviewed alias maps `pmw`/`dv` onto a
catalogue parameter.

So the ordering for the rest of section 4 is: get the running snapshot, then place
the 59 approved families on the drawing. Placing them first would mean laying out a
diagram whose two headline numbers are still blank.

## 5. Activate and measure

```bash
python src/backend/InvenTree/manage.py onboard_pumphouse_estate estate.json --source SOURCE_PK --activate --dry-run
python src/backend/InvenTree/manage.py onboard_pumphouse_estate estate.json --source SOURCE_PK --activate > activated-crosswalk.json
python src/backend/InvenTree/manage.py benchmark_pumphouse_reads --source SOURCE_PK > poll-benchmark.json
```

Activation requires approved mappings and preserves existing accepted cursors. An active
station poll blocks activation changes until its lease is released. Pause scheduled polling
and avoid simultaneous registry edits while applying an estate-wide change.

The benchmark performs real, read-only queries for the last five minutes with the scheduler's
limits: 200 documents/station, 20 seconds/station, 50 seconds/sweep. It does not ingest values
or advance checkpoints. It reports station duration, completed documents and query RU charges;
account/container metadata requests are not included in those query charges. Confirm the
station count, throughput and RU behaviour with representative full snapshots and live cadence.
Missing RU metadata (including with the vNext emulator) is reported as `null`, not zero.
Capped reads report `window_complete: false` and fail the benchmark, even if no request failed.

After the platform and plant checks pass, enable the global flag and restart the server and
Django-Q2 worker. Verify fresh readings and timestamps against the source. The **Pumphouse
mimic** tab is on registered station machine pages. Check stale, disabled and network-failure
behaviour; verify approved thresholds before interpreting any alarm as a plant limit.

## 5a. Known gap: the trend UI is a sparkline only

The backend side of trends is complete. AIMMS stores no time series - `MachineSignalState`
is one row per binding, a current-state cache - so a trend is a *federated* read: the
connector is asked for a window and the answer is bounded before it is returned.

```
GET /api/machine-health/machine/<pk>/trend/?binding=<binding_pk>&from=<iso>&to=<iso>
```

Defaults to the last 24 hours; capped at 2000 samples and a 30-day window. The client names
a **binding, never a tag** - that is the tag-injection boundary. A source that cannot serve
history returns `available: false` rather than a line synthesized from the current value,
because a fabricated line is worse than no line.

The frontend does not yet use any of that. `src/frontend/src/pages/assets/health/SignalTrend.tsx`
is a 135-line sparkline: no parameter picker, no range selection, no axes, no units, no zoom.
So there is currently **no way in the UI to select a time range and chart a parameter**, which
is what the endpoint was built for. Remaining work, roughly a day:

- A parameter picker over the station's approved bindings, and preset ranges (1 h / 24 h /
  7 d / custom) that map onto `from`/`to`.
- Axes labelled with the reviewed unit. An unreviewed unit must render unitless rather than
  borrow a plausible one.
- Surface `available: false` as an explicit "this source does not serve history", distinct
  from "no samples in this window". They are different failures and must not look alike.
- Keep `truncated` visible. The cap silently clips the *newest* end of a wide window; a
  chart that hides that is actively misleading about what the plant did.

Sequencing: this is worth doing after section 3, not before. Until the full dictionary is
reviewed, most bound signals are status codes with no unit, and a correctly built chart
would still look broken - for reasons that are not the chart's fault.

## 6. Re-run checks and release review

```bash
python src/backend/InvenTree/manage.py test assets.test_dictionary_review assets.test_pumphouse_estate machine_health.tests --keepdb --noinput
cd src/frontend
node node_modules/.bin/lingui extract
node node_modules/.bin/lingui compile --typescript
node node_modules/typescript/bin/tsc --noEmit
node node_modules/.bin/playwright test --config playwright.mimic.config.ts
```

The opt-in Cosmos integration test and the GitHub workflow are documented in `README.md`.
The browser suite mocks the API and requires no backend or Azure account. Regenerate/check
the API schema using the normal repository process. The local changes have not been deployed
or pushed, and the added GitHub jobs have not executed remotely. Review the local PR draft
before publication: `AGENTS.md` requires human review before a PR can be opened.
