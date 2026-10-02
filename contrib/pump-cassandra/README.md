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
- `component_type=65`, `event_value_type=41` and other abbreviated
  fields still need source documentation for their enum meanings. The user
  confirmed that component/event types are constant selectors in this feed;
  that does not explain the business meaning of their numeric codes.

### Basic params (station envelope in `data1`)

The user listed the pumphouse "basic params". The draft's `basic_params` block
matches them to the abbreviated `data1` keys:

| Key | Basic param | Note |
|---|---|---|
| `egt` | Event Generation Timestamp (`eventGenTs`) | Epoch ms; equals `sub_time_period` in the sample |
| `sl` | Surge Pool Level | Sample equals `/dex/COMMAN_FORBAY_LEVEL`; not yet an approved alias |
| `dv` | Total Discharge Value | Absent from pilot excerpt; unit unconfirmed |
| `pmw` | Input Power (MW) | |
| `pmvar` | Input Power (MVar) | |
| `pc` | Number of Pumps Running | Absent from pilot excerpt |
| `st` | Pumphouse Running Status | Code `I`; code set unconfirmed |
| `pd` | Pump Operation Details | Pump-level map keyed `P1`–`P14` |
| `dsc` | Data Source: SCADA Device | Numeric code `24`; enum table unconfirmed |
| `sr` | Source Text: SCADA Device | String `SCADA` |
| `ext` | Expiration Timestamp | `egt` + default real-time expiry period (300 s observed) |

The key-to-name matches follow the user's list order and the sample content;
confirm each abbreviation against source documentation before binding. The
expiry period is a **source** default and must not be reused as an application
retention TTL or as a reason to drop historical rows.

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

These roles describe the logical layout. The physical schema has since been
confirmed (below) and agrees with them.

### Confirmed physical schema

The source keyspace uses one table per calendar month, suffixed `YYYYMM` in **UTC**:

```sql
CREATE TABLE cass_business_data_klsw.iwm_data_202507 (
    parent_entity_uuid uuid,
    location_type text,
    component_type int,
    time_period text,
    event_value_type int,
    sub_time_period bigint,
    entity_uuid uuid,
    data1 text,
    data2 text,
    PRIMARY KEY (
        (parent_entity_uuid, location_type, component_type, time_period,
         event_value_type),
        sub_time_period, entity_uuid
    )
)
```

Consequences that any reader must respect:

- **`time_period` is `text`, not a number.** It carries epoch milliseconds as a
  string. An export may present it either way, so parse both and never compare a
  string bucket against an integer sample. `assets.registry.plan_dictionary`
  accepts both forms.
- **One hour bucket is one partition**, shared by every pumphouse under the same
  parent. A slice on `sub_time_period` is a single-partition read that needs no
  `ALLOW FILTERING`, which is the efficient access pattern the gate asked for.
- **`entity_uuid` is the *second* clustering column**, after `sub_time_period`.
  Cassandra cannot restrict it server-side without also fixing `sub_time_period`,
  so a per-station read is a per-row filter applied by the client. An hour slice
  is therefore multi-station by nature; the planner keeps the selected station's
  rows and skips the others rather than rejecting the export.
- **Monthly tables require fan-out.** A window crossing a month boundary must
  enumerate `iwm_data_YYYYMM` for each month it touches, deriving the suffix in
  UTC from the bucket start.
- `data2` is null in every observed row; its purpose is still undocumented.
- The partition carries **both** the basic station parameters (`pd`) and the
  extension payload (`dex`); there is no separate extension partition.

### Time interpretation

Interpreting the large numbers as Unix epoch milliseconds yields:

| Field | UTC value |
|---|---|
| `time_period` | 2025-07-18 15:00:00 |
| `sub_time_period` and `egt` | 2025-07-18 15:59:58.616 |
| `ext` | 2025-07-18 16:04:58.616 |

`dex.TIMESTAMP = "1.752854398616E9"` interpreted as epoch seconds exactly matches
`egt` after multiplication by 1000. The user confirmed `egt` is the Event
Generation Timestamp and `ext` the Expiration Timestamp (`egt` + default
real-time expiry period, 300 seconds here). That expiry is a source-side
real-time validity window, **not** an application TTL policy or ingestion lag.
The user has separately confirmed hourly buckets and nominal five-second sampling;
the schema must still establish the partition/clustering rules. `egt` and `ext`
should not override row observation time.
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

1. **Confirm physical schema and remaining payload meanings.** *Done for the
   schema:* source station identity, constant selectors, hourly buckets, sample
   timestamps and the composite `PRIMARY KEY` are confirmed and recorded under
   "Confirmed physical schema". Still open: `data2` purpose, the `dsc` code set,
   quality conventions, and the keys carrying total discharge, input power
   (MW/MVar) and the running-pump count. Status codes are confirmed as `I` idle
   and `R` running.
2. **Add asset identity and hierarchy.** *Done.* Integer primary keys preserved;
   UUIDs, station/pump type and parent relations, cycle and Client validation live
   in `assets.models.AssetMachine`; this draft crosswalk is registered through
   `assets.registry.register_station`.
3. **Add component occurrences and reviewed dictionary points.** *Done.*
   `assets.registry_models.AssetComponent` and `DictionaryPoint` link occurrences
   to catalogue Parts, with station-level points owned once by the station.
4. **Add preview/import tooling.** *Done.* `assets.registry.plan_dictionary` /
   `import_dictionary` behind the hash-locked preview → import endpoints in
   `assets.registry_api`, preserving original keys, exact paths, type/unit review
   state and provenance.
5. **Build the read-only adapter or dump importer.** *Superseded in scope:* the
   live read is being built against **Azure Cosmos DB for NoSQL**, not Cassandra —
   see `specs/002-cosmos-pumphouse-connector/plan.md`. The rules stand unchanged:
   bounded single-partition queries with paging and checkpoints, never a table
   scan or `ALLOW FILTERING`, one normalization path shared by the live adapter
   and the dump importer, raw history kept external, and historical imports out of
   latest state unless a reviewed freshness/ordering policy permits it. A
   Cassandra reader is only needed if the deferred Cassandra → Cosmos migration
   job is built in this repository.

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
hour boundaries and cadence, timestamp consistency, the eleven basic params and
expiry rule, eight alias examples,
exact-spelling exceptions, and unresolved keys. This validates the
**draft configuration and representative matching logic**, not a production
connector, the full supplied JSON or the full 1,000–1,200-tag dictionary.
