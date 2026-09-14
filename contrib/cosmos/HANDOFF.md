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
