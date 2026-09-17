# Two external asks, made precise

Both of the remaining blockers need someone other than the developer. This file
states exactly what is needed, why, and how to verify it afterwards, so neither
turns into a vague request.

---

## Ask 1: one snapshot taken while a pump is running

### Why

The reference row `PH_3.full-snapshot.json` was captured with **the station shut
down** - every bay reports `st=I` and `MOTOR_ON_STATUS=0`, with zero active power,
current and voltage. A unit is a claim about magnitude, so a stopped machine cannot
confirm one: volts predicts ~11000 and kilovolts predicts ~11, and the observation
is 0 either way.

That is why 264 dictionary points are withheld rather than approved. A single
running snapshot settles most of them at once.

### We checked whether we already had one. We do not.

Every payload in the repository was scanned. The only one containing a running bay
is `contrib/cosmos/samples/ph3_snapshots.json`, and its own note says:

> *"Synthesised in the NEXT hour bucket with P3 running, so bucket enumeration, the
> read_latest fallback and a status transition are all exercised."*

Its `ACTIVE_POWER = 8.75` and `PUMP_CURRENT_AVG = 412.5` were **authored for the
tests**, not measured. Approving a unit from them would be circular: it would
confirm the fixture author's choice, not the plant's scale.

**This matters beyond the review.** That synthetic snapshot is what currently seeds
the local emulator, so the dev UI shows a *running* station with plausible-looking
power and current. Anyone eyeballing that screen to sanity-check a unit would be
reading invented numbers. Treat the dev mimic as a layout check, never as evidence.

### What is actually needed

One snapshot, any hour, with **at least one bay at `st=R`**. Same shape and same
export path as the row already supplied - untrimmed `dex`, values verbatim,
nothing rounded or cleaned. Out-of-range readings should be preserved exactly as
they arrive; they are informative.

Two or three snapshots a few minutes apart would be better than one, because they
also show which tags move under load and which are static.

### What it settles

`ACTIVE_POWER` (kW vs MW), `PUMP_CURRENT_AVG`, `PUMP_LINE_TO_LINE_VOLTAGE` (V vs
kV), `SPEED`, `DISCHARGE_PRESSURE` (bar vs kg/cm2 vs metres of head), and all seven
vibration families. It also unblocks the two headline mimic totals, which currently
read `null / incomplete` because pump power has no approved point.

### How to use it when it arrives

```zsh
python3 contrib/pump-cassandra/value_ranges.py path/to/running-snapshot.json
```

That prints observed min, median and max per tag family. Compare against the
withheld units, then regenerate and apply the review pack as in HANDOFF section 3.

---

## Ask 2: one Cosmos role assignment, by someone with Owner or Contributor

### Status: the identity now exists; the grant is blocked

The original item said "grant the app identity Data Reader". The real blocker
turned out to be earlier than that: **there was no app identity at all.** The
subscription contains zero managed identities, and the only data-plane grant on the
account was to a human.

So a dedicated identity was created:

| Field | Value |
|---|---|
| Display name | `aimms-pumphouse-connector` |
| Application (client) ID | `08a359e2-7133-43ee-b100-26a0e0db1bd3` |
| Service principal object ID | `b46af2ad-9938-4a75-8997-8870004d244b` |
| Credentials | **none issued** |
| Permissions | **none** - the role assignment below failed |

It currently cannot authenticate and cannot reach anything. It is inert until both
a credential and the role assignment exist.

### The command that needs running

```zsh
az cosmosdb sql role assignment create \
  --account-name epconchatcosmos9d6b \
  --resource-group EpconChat \
  --role-definition-id 00000000-0000-0000-0000-000000000001 \
  --principal-id b46af2ad-9938-4a75-8997-8870004d244b \
  --scope "/dbs/aimms/colls/pumphouse_readings"
```

`...001` is **Data Reader**, not Contributor. The scope is the single container,
not the account - the `aimms` database also holds `telemetry` and `conversations`,
which this connector has no business reading.

### Why the developer cannot run it

The account returns:

```
AuthorizationFailed: ... does not have authorization to perform action
'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments/write'
```

The developer holds **Cosmos DB Operator** on the account, and that role's
definition lists `sqlRoleAssignments/write` and `/delete` under **notActions**.
This is deliberate Azure design: an operator may manage the account but may not
grant data access, because otherwise the role could grant itself data access.

So this needs **Owner**, **Contributor**, or **DocumentDB Account Contributor** on
the account. It is one command and grants strictly read access to one container.

### A related finding worth acting on separately

The only existing data-plane assignment is:

- Principal `f024cd79-82c5-4599-8b29-489f487122ed` - a **human user**, `Aniket@equa.work`
- Role `...002` - **Data Contributor**, which can write and delete documents
- Scope - the **whole account**

That was verified live: a write probe succeeded and was cleaned up. It is fine for
a developer exploring, but it should not be the shape of production access, and
nothing should be deployed relying on it. The connector itself has no write path -
`upsert_item`, `create_item`, `replace_item` and `delete_item` do not appear
anywhere in it - so Data Reader is sufficient.

### How to verify afterwards

```zsh
az cosmosdb sql role assignment list \
  --account-name epconchatcosmos9d6b --resource-group EpconChat \
  --query "[].{principal:principalId, role:roleDefinitionId, scope:scope}" -o table

python src/backend/InvenTree/manage.py check_pumphouse_readiness --source SOURCE_PK --probe --allow-incomplete
```

A successful read probe does **not** prove the absence of write permission. To
check least privilege actually holds, attempt a write with the new identity and
confirm it is refused.
