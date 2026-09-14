---
title: Cosmos pumphouse connector
---

# Cosmos pumphouse connector

The `cosmos_pumphouse` adapter reads station snapshots from Cosmos DB's NoSQL API.
Reviewed dictionary points determine which readings become machine signal values.
It does not migrate Cassandra data, write equipment commands, or infer alarm limits.

## Configure a deployment

Apply the backend migrations through `assets.0014_station_activation`. Existing
sources keep their configuration; a deployment administrator must explicitly assign
their Client and link station checkpoints through activation. The migration does
not guess which Client owns a source or which registered station owns an old cursor.

In the Health Source admin, select connector type `cosmos_pumphouse`, assign the
station's Client, enable the source, and supply the following non-secret configuration:

```json
{
  "endpoint": "https://<account>.documents.azure.com:443/",
  "database": "<database>",
  "readings_container": "pumphouse_readings",
  "stations": ["<source-entity-uuid>"],
  "partition_key_mode": "hierarchical",
  "max_docs_per_poll": 200
}
```

`stations` contains source entity UUIDs, not the registry's local equipment UUIDs.
One account source can serve several stations belonging to the assigned Client.
The container uses `/station_uuid` and `/hour_bucket` as its hierarchical partition
key. The exact container/index definition and provisioning instructions are in the
repository's `contrib/cosmos/README.md`.

For Azure, leave `secret_ref` empty: the adapter uses `DefaultAzureCredential`.
Assign the application identity the Cosmos DB Built-in Data Reader role at the
required data scope. The application does not need a management-plane role or a
data writer role. Verify the deployed identity and network path separately from
the local emulator test. New sources default to 300-second freshness; check this
value explicitly on existing sources.

## Review and activate a station

1. Register the station in the Equipment Registry with its Client and source identity.
2. Import its dictionary and review ownership, catalogue parameter, data type and unit.
   Approve verified mappings only. Unknown units and thresholds remain unconfirmed.
3. In **Dictionary and review → Live source**, select the configured source and
   activate the station. This requires work-order view, add and change permission
   and the station's Client scope.
4. Check the bound/unbound counts. Activation creates only approved bindings and
   links their polling checkpoint. It does not enable the global scheduler flag.
5. Enable `AIMMS_COSMOS_PUMPHOUSE_ENABLED=true` in the deployment configuration and
   restart the server and Django-Q2 worker so both use the updated setting.
6. Check the card's last poll time and error code, then confirm current values
   against known source readings. A successful poll may find no new documents.

The API supports the same workflow: GET
`/api/assets/registry/<station pk>/activate/?source=<source pk>` returns a
`preview.source_hash`. POST `{"source": <pk>, "source_hash": "<hash>"}` to activate;
DELETE with that body deactivates. A changed mapping/configuration invalidates the
preview. An in-progress poll requires retry after its lease is released.

New checkpoints start five minutes before activation. Refresh and reactivation
preserve existing progress. Revoking approval or changing a point's measurement
meaning disables its binding and removes cached state. After review, refresh the
approved bindings. Confirmed thresholds survive only when measurement meaning is
unchanged; activation never supplies guessed defaults.

## Pause and diagnose polling

The global flag defaults to false. Turning it off stops future scheduled reads;
an already running request can finish. To stop one station, use **Deactivate
station**. This pauses its checkpoint and removes its dictionary-managed bindings
and cached values, preserving the accepted cursor and manually managed bindings.

Each sweep has a 50-second budget; a station has up to 20 seconds and 200 documents.
Requests use bounded timeouts and no SDK retries. Budgets are checked between
operations, so they do not interrupt an in-flight request. A two-minute lease
recovers after a worker crash. Empty ranges advance scan progress with a five-minute
overlap for recent delayed documents; older arrivals need a separate backfill policy.

| Code | Check |
|---|---|
| `CONFIG` | Client/station ownership, source UUID and required connection fields |
| `AUTH` | Application identity, token acquisition and data-reader assignment |
| `NOT_FOUND` | Database/container names and the provisioned container |
| `THROTTLED` | Account capacity and polling load; the next sweep retries |
| `NETWORK` | Endpoint reachability and request timeouts |
| `SNAPSHOT` | Snapshot shape, timestamps and raw/parsed consistency |
| `INGEST` | Reviewed bindings and readings rejected by shared ingestion validation |

Errors are recorded per station. Provider exception text is not sent to operators.
Missing, stale or unconfirmed readings must not be treated as zero or healthy.

## Local checks and offline imports

`import_pumphouse_dump <file> --station <pk> --source <pk> --dry-run` validates a
bounded dump against existing bindings. Remove `--dry-run` to import atomically.
The command makes no Cosmos calls and does not advance live polling checkpoints.

The opt-in `machine_health.tests.test_cosmos_emulator` test uses the real SDK against
a disposable loopback emulator database. It checks partition isolation, history
across two hours, capped resume, scheduler ingestion and replay. See the runbook for
the command and `.github/workflows/cosmos_integration.yaml` for CI setup. This does
not validate production RBAC, networking, or complete station data.

## Mimic dashboard and estate onboarding

Registered pumphouse pages include a **Pumphouse mimic** tab with sparse pump selection,
selected-unit measurements, grouped reviewed points, source status, totals and threshold
alarms. The page polls cached API state while visible. Stale, disabled, unreviewed or failed
reads stay unavailable. Derived totals require valid reviewed inputs from every registered bay.
Alarm limits are never inferred. The shared SVG/layout geometry is explicitly provisional.

Use `export_dictionary_review --station <pk>` and `apply_dictionary_review --review <file>`
for full dictionary review packs and exact catalogue crosswalks. The estate command
`onboard_pumphouse_estate <manifest> --source <pk> --dry-run` previews registration/import/review;
remove `--dry-run` to commit atomically and add `--activate` for approved bindings. Replays
preserve station UUIDs and polling progress. No command enables the global polling flag.

`validate_pumphouse_layout` checks the SVG contract and per-station coverage.
`check_pumphouse_readiness --source <pk>` checks local readiness; `--probe` additionally contacts
Cosmos. `benchmark_pumphouse_reads --source <pk>` measures bounded read duration and query RU
without writing samples or checkpoints. These reports do not establish production acceptance.
See `contrib/cosmos/HANDOFF.md` and its example manifest for the complete developer procedure.

## Trust boundaries

This follows InvenTree's [threat model](../concepts/threat_model.md): deployment
administrators, plugins and templates remain trusted. Only administrators configure
connection endpoints and Client ownership. Operator APIs expose source names and
fixed status codes, excluding endpoints, configuration and credential references.

Azure access uses a read-only data-plane identity; the adapter exposes no write or
control operation. An environment-variable account key is accepted only for the
local emulator. No credential value is stored in the source row or returned by the
registry API. Registry mutations require exact Client scope and work-order roles;
ingestion resolves pointers only inside the registered station and its children.

Untrimmed snapshots, station inventory and mimic reference images are currently
unavailable. The existing abridged samples are test fixtures and cannot establish
complete plant coverage, installation details, units or alarm limits.
