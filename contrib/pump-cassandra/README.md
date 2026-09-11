# PH_3 Cassandra mapping proposal

This is an **offline draft**, not a Cassandra connector or equipment importer.
It records the identifiers, envelope structure and representative tag variations
from the user-provided Cassandra row. It does **not** store the full raw snapshot,
claim a complete tag count, create database records or enable live ingestion.

## Names and stable identities

For the requested treated-wastewater context, use the generic US-style name
**Effluent Pump Station 03** for source code `PH_3`. This is a provisional display
label, not a researched name of an actual US plant or a verified process duty.
For confirmed water reuse, **Reclaimed Water Pump Station** may be more appropriate;
**Sewage Lift Station** would imply a different, untreated-wastewater duty.

Reserved draft station UUID: `402840f4-90fc-43bc-b15f-30f3ac51e1d3`.

`PH_3.mapping.draft.json` lists separate UUIDs for all fourteen pump slots observed
as `pd.P1` through `pd.P14`. They are UUIDv5 values derived from the station UUID
and the exact registered source key (for example, `pump:P1`). Renaming a station
or pump never changes its identity. A different station has a different namespace,
so its `P1` cannot collide with this station's `P1`.

Generate and persist a UUIDv4 **once per registered station**, not per snapshot.
The user confirmed that `entity_uuid` is the source's station key. Enforce its
uniqueness within the configured source namespace during registration. The draft
records it as `station.source_uuid`, separately from the reserved internal UUID;
neither ID needs to be regenerated. Treat later source-key renames
as explicit aliases, rather than generating replacement identities. A pump-slot
UUID identifies the registered functional location; installed component occurrences
and replacements require separate identities and history.

Preserve these external identifiers separately, without rewriting Cassandra:

| Supplied field | Value | Interpretation |
|---|---|---|
| `parent_entity_uuid` | `dd4b923d-945d-47b3-aff3-de032d15f864` | User-confirmed shared identifier across all pumphouses in this feed |
| `entity_uuid` | `bafc976f-1ccc-4a91-aaa6-c3eac2470d36` | User-confirmed UUID of this pumphouse |
| `dex.ID` | `PH_3` | Source display/code identifier |
| `location_type` | `PUMP_HOUSE` | Source classification, not a confirmed process duty |

The application currently uses integer AssetMachine primary keys. These proposed
UUIDs are **not yet registered in the application**; adding a public UUID field
can preserve the existing integer keys and API compatibility. Do not put station
identity on shared catalogue Parts or use the catalogue classification tag as an
installation assignment.

## What the row tells us

- `data1` contains a station envelope; `data2` is shown as null in the export.
- `pd` contains pump summaries for `P1`–`P14`.
- `dex` contains detailed raw tag/value pairs plus `ID` and `TIMESTAMP` metadata.
- Numbered `PUMPn_...` tags can be associated with a registered `Pn` pump slot.
  Remove **only the first anchored pump prefix** when matching the catalogue.
- `/dex/COMMAN_FORBAY_LEVEL` is a station/shared-infrastructure candidate.
- Top-level `st` and each `/pd/Pn/st` are distinct points; the meaning of `I`
  and status polarity are not confirmed. Keep summary `pmw`, `pmvar`, `sl`, etc.
  separate from similarly named detailed readings until equivalence is verified.
- `component_type=65`, `event_value_type=41`, `dsc=24` and other abbreviated
  fields still need source documentation for their enum meanings. The user
  confirmed that component/event types are constant selectors in this feed;
  that does not explain the business meaning of their numeric codes.

### Confirmed logical row layout

The user's clarification establishes these roles within the supplied feed:

| Fields | Role |
|---|---|
| `parent_entity_uuid`, `location_type`, `component_type`, `event_value_type` | Constant selectors shared across pumphouses |
| `entity_uuid` | Which pumphouse the snapshot belongs to |
| `time_period` | Start of the relevant hourly bucket, in epoch milliseconds |
| `sub_time_period` | Exact sample timestamp within that hour, in epoch milliseconds |
| `data1` | Station JSON containing summaries and detailed pump readings |

The shared parent identifier is **not** itself a station ID, a confirmed physical
parent asset or an application Client/security boundary. Do not create one station
per shared parent, or merge multiple stations' `P1` measurements.

For an uninterrupted five-second cadence, expect **720 snapshots per station per
hour**. A snapshot contains all that station's pumps; it is not a separate row for
each pump or parameter. Treat this as expected volume, not a guarantee: missing,
late, repeated or corrected samples require explicit handling.

Use half-open bucket membership:
`time_period <= sub_time_period < time_period + 3_600_000`.
A timestamp at the next hour boundary belongs to the next bucket. Enumerate the
source hour buckets covering a requested interval, then clip to the requested
time range. Five-second cadence does not imply epoch-grid alignment: the supplied
sample has a nonzero remainder modulo 5,000. Preserve it exactly instead of rounding
or assigning a synthetic timestamp. Row `sub_time_period` supplies observation time;
track application receipt time separately.

These roles describe the **logical** layout, not the Cassandra physical primary
key. We still need the `PRIMARY KEY` declaration to decide whether station filtering
and a sample-time range can be applied together efficiently on the server. Queries
must not assume a clustering-column order or fall back to unbounded scans.

### Time interpretation

Interpreting the large numbers as Unix epoch milliseconds yields:

| Field | UTC value |
|---|---|
| `time_period` | 2025-07-18 15:00:00 |
| `sub_time_period` and `egt` | 2025-07-18 15:59:58.616 |
| `ext` | 2025-07-18 16:04:58.616 |

`dex.TIMESTAMP = "1.752854398616E9"` interpreted as epoch seconds exactly matches
`egt` after multiplication by 1000. `ext - egt` is 300 seconds. That arithmetic
does **not** establish expiry semantics, a TTL policy or ingestion lag. The user
has separately confirmed hourly buckets and nominal five-second sampling; the
schema must still establish the partition/clustering rules. `egt` and `ext`
should not override row observation time without documented semantics.
This July 2025 sample is historical and must not
be shown as a fresh September 2026 reading. Preserve observed/received timestamps
separately; do not substitute import time for observation time.

### Catalogue spelling differences

The draft contains eight explicit candidate alias rules for RTD, bearing vibration,
excitation, drive-end vibration channel 2 and reactive power tags. Examples:

- `PUMP2_MOTOR_CORE_RTD1_PROCESS_VALUE` → Electric Motor / Motor Core RTD 1.
- `PUMP5_PUMP_REACTIVE_POWER` → Motor Electrical System / Reactive Power.
- `PUMP3_PUMP_MOTOR_DE_VIBRATION2` → Motor Drive-End Bearing / Vibration 2,
  pending verification of the actual sensor mounting point.

Use exact matches first, then allowlisted aliases, otherwise retain an unresolved
entry. Never globally strip `_PROCESS_VALUE`: the HOPD/EOPD catalogue keys already
contain that suffix. Preserve embedded spaces, `PUMP_POWERFATCOR`,
`COMMAN_FORBAY_LEVEL` and the repeated `PUMP` in inlet-cooling tags. Preserve the
original raw key/path even after matching a friendly catalogue definition.

One snapshot is not an authoritative full dictionary. Tags absent here may exist
in other rows, operating modes or event types. Import the union of observed tags
from a bounded sample/export, compare with the source dictionary, and report
unmapped, absent and conflicting entries separately. Missing means unknown,
not zero, false, deleted or offline.

### Data-quality review

The sample includes temperature-named values around `-242.1` and `3276.7`, and
valve-position values below zero or above 100. These need review against units,
scaling and source quality/status conventions. They are **not confirmed faults**.
Do not clamp, discard or convert them to normal readings. Do not assume all
numeric strings have GOOD quality. Keep the original representation and preserve
any future source quality codes; engineering limits must come from approved data.

## Implementation gates

1. **Confirm physical schema and remaining payload meanings.** Source station
   identity, constant selectors, hourly buckets and sample timestamps are now
   confirmed. Obtain the redacted `CREATE TABLE` statement
   with the exact composite `PRIMARY KEY`, clustering order and column types;
   identify the keyspace/table. A printed
   column list cannot establish which fields are partition versus clustering keys.
   Confirm payload storage type, `data2` purpose, summary/status codes, quality
   conventions and remaining `egt`/`ext` semantics.
2. **Add asset identity and hierarchy.** Preserve integer primary keys, add UUIDs
   and station/pump type/parent relations, validate cycles and Client consistency,
   then register this draft crosswalk. Backfill existing asset UUIDs safely.
3. **Add component occurrences and reviewed dictionary points.** Link each
   occurrence to an existing catalogue Part. A real motor/bearing occurrence owns
   its points; shared infrastructure owns station-level points once. Do not infer
   a complete physical BOM or replicate all catalogue families onto every pump.
4. **Add preview/import tooling.** Preserve original keys, exact paths, type/unit
   review state and provenance. Require unambiguous source-scoped point identities
   and explicit alias approval. Existing machine-health lookup resolves source +
   external key, so enforce collision-free keys consistently for readings, alarms
   and history before enabling a shared multi-house source.
5. **Build the read-only Cassandra adapter or dump importer.** Use the confirmed
   partition/clustering schema with bounded queries, paging and checkpoints; do
   not default to table scans or `ALLOW FILTERING`. Reuse one normalization path
   for both modes. Keep raw history external and historical imports out of latest
   state unless an explicitly reviewed freshness/ordering policy permits it.

No database credentials are needed for these offline checks. For a future live
connection, use a read-only account and deployment-managed secrets, with TLS/network
controls. Do not put passwords in chat, this JSON, source control or browser config.

## Run the offline checks

Uses only the Python standard library; no new dependency manifests are required.
From the repository root:

```zsh
python3 -m unittest discover -s contrib/pump-cassandra -p 'test_*.py' -v
```

Checks cover UUID determinism and uniqueness, confirmed source identity/selectors,
hour boundaries and cadence, timestamp consistency, eight alias examples,
exact-spelling exceptions, and unresolved keys. This validates the
**draft configuration and representative matching logic**, not a production
connector, the full supplied JSON or the full 1,000–1,200-tag dictionary.
