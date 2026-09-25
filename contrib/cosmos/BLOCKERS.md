# Two external asks, made precise

Both of the remaining blockers need someone other than the developer. This file
states exactly what is needed, why, and how to verify it afterwards, so neither
turns into a vague request.

---

## Ask 1: one snapshot taken while a pump is running

### Status: SATISFIED on 2026-09-21, and it settled less than expected.

Running data was found in the local Cassandra dump - no export request was
needed. `Saraswati PH` (`9c09411d-ab94-44b8-a398-8768f58c223e`, `dex.ID` `PH_2`,
12 bays) runs with bay P5 loaded in 2,443 of 2,448 sampled rows, carrying an
untrimmed 867-tag `dex`. The station this ask was written against, `Parvathi PH`
(`bafc976f-…`, `PH_3`), was idle for the whole of July 2025, which is why no
amount of looking at *it* would ever have helped.

**The premise below - "a single running snapshot settles most of them at once" -
turned out to be false.** Measured by comparing the loaded bay P5 against idle
bay P1 in the same snapshot:

| tag | P5 (running) | P1 (idle) | what it settles |
|---|---|---|---|
| `ACTIVE_POWER` | 24.5 | 0 | **MW.** Settled, three ways: it equals station `/pmw` (documented MW) exactly, and the plant's own nameplate gives Saraswati 40 MW rated / 24.5 MW observed. At kW, 24.5 against a 40 MW rating is 0.06% of rated. |
| `PUMP_CURRENT_AVG` | **0** | 0 | **Nothing. The tag does not respond to load.** No snapshot can ever settle it. |
| `PUMP_REACTIVE_POWER` | **0** | 0 | **Nothing.** Same - dead under load. |
| `PUMP_POWERFATCOR` | **0** | 0.378 | **Nothing, and it is inverted**: zero while loaded, non-zero while idle. |
| `PUMP_LINE_TO_LINE_VOLTAGE` | 55.30 | 0 | Responds, but settles nothing: 55.3 fits neither V nor kV on an 11 kV-class motor. |
| `DISCHARGE_PRESSURE` | 18.96 | 0.57 | Responds, but contradicts the nameplate: 18.96 matches no unit against Saraswati's stated 34 m lift head (bar, metres or kg/cm2). |
| `SPEED` | 474.06 | -0.70 | Responds. 474 rpm is plausible for a vertical pump but is not confirmed by anything. |
| `MTR_NDE_BRG_VBRTN1_PROCESS_VALUE` | 59.2575 | -0.07 | Responds, but sits on a value that recurs as a constant elsewhere in the payload, so it may be a sentinel rather than a reading. D9 stands. |

So of the eight families this ask expected to resolve: **one is settled, three
are dead regardless of load, two respond but disagree with the plant's own
figures, and two remain ambiguous.**

**The actionable consequence: stop asking for running snapshots.** Current,
reactive power and power factor will not be settled by better data, because
those tags do not move when the machine is loaded. They need an instrumentation
answer from the plant - are they wired at all? - not another export.

What *is* now approvable on evidence is narrow: `pmw` and `ACTIVE_POWER` as MW
(29 points on PH_3), and `pc` as a dimensionless count (1 point, confirming D7 -
it read 1.0 with exactly one bay running). That is 30 of PH_3's 324 unapproved
points. Two more are blocked by us rather than by data: `pmvar` (15 points)
needs `var`/`MVar` accepted by the unit registry (see HANDOFF.md section 3), and
`sl` needs a surge-pool-level parameter that the catalogue does not define.

The original reasoning is kept below because it is still correct about *why* a
stopped machine cannot confirm a unit. It is only wrong about how much one
running machine fixes.

### Update 2026-09-25: published plant figures settled three of these

Two rows of the table above rested on "Saraswati's stated 34 m lift head". That
figure is wrong for this lift, and it was the contradiction this file had
already flagged. The scheme's published pond levels put Saraswati's pumphouse at
Annaram (120.0 m) lifting to Sundilla (130.0 m), and the station's own forebay
tag reads 115.2 m - so its lift is about 15 m, not 34 m. Correcting the head
turned two "disagrees with the plant's figures" rows into settled units, and
made a third an outright correction.

| tag | settled as | what settled it |
|---|---|---|
| `dv` (30 points) | `cusec` | A published operating report for the Annaram pumphouse gives four pumps yielding 11,724 cusecs - 2,931 each - and the tag reads exactly 2931 whenever its bay runs. The sibling figure for Medigadda (12,708 over six pumps = 2,118) does not match, so the arithmetic identifies the station as well as the unit. At 2,931 ft3/s = 83.0 m3/s, against 18.96 m of head and 24.5 MW, that is 63% wire-to-water; m3/s implies 22,000%, m3/min 37%, L/s 2%. |
| `DISCHARGE_PRESSURE` (29 points) | `mH2O`, **not** the proposed `bar` | 18.96 m is the 14.9 m static lift at the observed forebay level plus about 4 m of losses. Under `bar` the same reading is 193 m of head on a 15 m lift, and under `kg/cm2` 186 m - both impossible, so `bar` is excluded rather than merely unconfirmed. |
| `ACTIVE_POWER` (4 points) | `MW`, **not** the proposed `kW` | These four were the only ones left proposing kW; the catalogue's own Active Power parameter is MW and the 29 siblings approved earlier are MW. |

`cusec` was not a unit the registry knew; it is now defined as a `CustomUnit`
(`cusec = foot**3/second`), which is the deployment-level extension point rather
than a patch to InvenTree's own registry. **The station flow total now resolves**
- 82.997 m3/s at Saraswati - where it had read `incomplete` since the migration.

Applied with `contrib/cosmos/devtools/approve_researched_units.py`, which is
re-runnable and states in each approval note which station carries the direct
evidence and which inherit the unit as the same field of the same document
schema.

**What this did not settle, and why no further reading will.** 442 of the 950
pending points are a single constant across the whole ten-day window - dead
channels, which no published figure revives. And the one bay that ever runs
reports a frozen block: all 68 of P5's tags hold one value across 105 snapshots
spanning ten days, with current, frequency and power factor reading 0 *while
running*, valve position at 118.5%, every vibration channel identical at 59.257,
and many RTDs at the 3276.7 over-range sentinel. That block cannot corroborate a
magnitude, so the families resting on it stay withheld. The remaining asks are
unchanged: they need an instrumentation answer from the plant, not more data.

### Why

The reference row `PH_3.full-snapshot.json` was captured with **the station shut
down** - every bay reports `st=I` and `MOTOR_ON_STATUS=0`, with zero active power,
current and voltage. A unit is a claim about magnitude, so a stopped machine cannot
confirm one: volts predicts ~11000 and kilovolts predicts ~11, and the observation
is 0 either way.

That is why 264 dictionary points are withheld rather than approved. A single
running snapshot settles most of them at once.

> **Superseded 2026-09-21.** The second sentence is wrong - see the status block
> above. A running snapshot settles `ACTIVE_POWER` and nothing else on this
> list, because three of the tags read zero even under load.

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

> **Superseded 2026-09-21** by measurement - see the status block at the top of
> this ask. This list was the expectation; the table up there is the result.
> Only `ACTIVE_POWER` was settled. `PUMP_CURRENT_AVG` cannot be settled by any
> snapshot, because it reads zero on a loaded pump.

`ACTIVE_POWER` (kW vs MW), `PUMP_CURRENT_AVG`, `PUMP_LINE_TO_LINE_VOLTAGE` (V vs
kV), `SPEED`, `DISCHARGE_PRESSURE` (bar vs kg/cm2 vs metres of head), and all seven
vibration families. It also unblocks the two headline mimic totals, which currently
read `null / incomplete` because pump power has no approved point.

The headline-totals claim does still hold: `pmw` becoming approvable is what
lets the station power total resolve instead of reading `incomplete`.

### How to use it when it arrives

```zsh
python3 contrib/pump-cassandra/value_ranges.py path/to/running-snapshot.json
```

That prints observed min, median and max per tag family. Compare against the
withheld units, then regenerate and apply the review pack as in HANDOFF section 3.

---

## Ask 2: one Cosmos role assignment, by someone with Owner or Contributor

### Status: RESOLVED on 2026-09-19. The dashboard now reads live Azure Cosmos.

Both halves of this blocker are cleared. Kept below because the reasoning explains
the current configuration and the reverse procedure.

| Field | Value |
|---|---|
| Display name | `aimms-pumphouse-connector` |
| Application (client) ID | `08a359e2-7133-43ee-b100-26a0e0db1bd3` |
| Service principal object ID | `b46af2ad-9938-4a75-8997-8870004d244b` |
| Role | **Data Reader** (`…0001`), scoped to `/dbs/aimms/colls/pumphouse_readings` |
| Credential | client secret `inventree-dev-container`, expires 2027-09-19 |

Verified end to end, not inferred:

```
check():        ok=True
read_latest():  905 readings
read_window(6h): 240 samples
write probe:    REJECTED - the role really is read-only
trend service:  243 samples, truncated=False, quality GOOD
```

The write probe matters as much as the reads. A working credential proves only
that the identity can authenticate; it does not prove the grant was scoped as
intended. An over-granted identity would have read *and* written happily, and
nothing on the dashboard would have looked different.

The secret lives in `contrib/container/docker.dev.secrets.env`, gitignored via
`*.env`, mode `0600`, wired into the dev server and worker as an **optional**
`env_file` (`required: false`). Absent on an ordinary checkout, which is correct:
without it the connector falls back to the local emulator rather than failing.

**`docker.dev.env` is tracked in git — the secret must never be put there.**

To switch back to the emulator:

```bash
docker exec -e REPOINT=emulator inventree-inventree-dev-server-1 \
  sh -c "cd /home/inventree/src/backend/InvenTree && \
    python manage.py shell < /home/inventree/contrib/cosmos/devtools/repoint_source.py"
```

### The freshness loops die on container recreate

`keep_emulator_fresh.sh` runs **inside** the dev-server container. Recreating that
container kills it, silently. `keep_live_fresh.sh` then keeps copying a tail that
has stopped advancing, so the live account ages out of the 300 s window and every
reading reports stale — indistinguishable from a dead connector.

Restart it in the container, not on the host:

```bash
docker exec -d inventree-inventree-dev-server-1 \
  sh -c "cd /home/inventree && nohup sh contrib/cosmos/devtools/keep_emulator_fresh.sh > /tmp/reseed.log 2>&1"
```

### Historical: why the grant was blocked

The original item said "grant the app identity Data Reader". The real blocker
turned out to be earlier than that: **there was no app identity at all.** The
subscription contains zero managed identities, and the only data-plane grant on the
account was to a human.

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

### A second, independent blocker: the container cannot authenticate at all

Granting the role assignment above is necessary but **not sufficient** to make the
dashboard read from the live account. Even with the role in place, the Django
container has no credential to present. Verified from inside
`inventree-dev-server`, every credential in the default chain fails:

```
EnvironmentCredential:     environment variables are not fully configured
WorkloadIdentityCredential: workload options are not fully configured
ManagedIdentityCredential: no response from the IMDS endpoint
SharedTokenCacheCredential: no accounts were found in the cache
AzureCliCredential:        Azure CLI not found on path
AzurePowerShellCredential: PowerShell is not installed
```

The connector resolves a credential with `DefaultAzureCredential` and, against a
real account, **refuses a key** (`cosmos_pumphouse.py:283-289`) - deliberately, so
that the Data Reader role rather than this code enforces read-only. There is no
hook for an injected token, by design.

So "the dashboard reads live Cosmos" needs **both**:

1. the role assignment above, **and**
2. a credential the container can actually present - which means either issuing a
   client secret for `aimms-pumphouse-connector` and setting `AZURE_CLIENT_ID` /
   `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET`, or deploying somewhere with a
   managed identity.

Installing the Azure CLI into the dev image and mounting `~/.azure` would also
satisfy `DefaultAzureCredential`, but it authenticates as the *human developer* -
who holds account-scoped **Data Contributor** - so it grants the running
application write access to the whole account. That is the opposite of what the
read-only design is for, and should not be used as a shortcut.

Standalone scripts under `contrib/cosmos/devtools/` sidestep this by accepting a
token minted on the host in `COSMOS_ACCESS_TOKEN`. That is fine for a one-off
inspection or copy; it is not a deployment, and the token expires in about an hour.

### The portal's Data Explorer will look empty, and the data is still there

A developer holding **Cosmos DB Operator** cannot read documents in the Azure
portal by default, and the failure is quiet: Data Explorer authenticates to the
data plane with an **account key**, which it obtains by calling `listKeys` - and
`listKeys` is in this role's `notActions`, exactly like
`sqlRoleAssignments/write`. The pane renders empty or errors rather than saying
"you are using the wrong credential type".

This is the same wall described above, met from a different direction. It is not
evidence that a write failed. Confirm with the SDK path before concluding
anything about the data:

```zsh
TOKEN=$(az account get-access-token \
  --resource "https://epconchatcosmos9d6b.documents.azure.com" \
  --query accessToken -o tsv)
docker exec -e COSMOS_ACCESS_TOKEN="$TOKEN" \
  -e COSMOS_ACCESS_TOKEN_EXPIRES=$(( $(date +%s) + 3000 )) \
  inventree-inventree-dev-server-1 sh -c "cd /home/inventree && \
  python contrib/cosmos/devtools/inventory.py \
  --endpoint https://epconchatcosmos9d6b.documents.azure.com:443/"
```

That path uses Entra ID, which this developer *does* hold at the data plane
(account-scoped Data Contributor), so it reads the documents the portal will not
show.

To fix the portal itself, switch Data Explorer to Entra ID: in the account blade
open **Data Explorer**, then the settings cog, and set **"Enable Entra ID RBAC"**
to **True** (the default is *Automatic*, which prefers keys). The session then
authenticates as the signed-in user rather than with a key.

Navigation, for the avoidance of doubt - the account has one database and three
containers, and only one of them holds readings:

```
epconchatcosmos9d6b  ->  aimms  ->  pumphouse_readings
                                    (not telemetry, not conversations)
```

### Everything after authentication already works - verified

Waiting on step 1 is worth doing only if the rest of the read path is sound.
It is. On 2026-09-19 the **real connector** was run against the live account with
the credential substituted and nothing else changed
(`contrib/cosmos/devtools/prove_live_read.py`):

```
endpoint: https://epconchatcosmos9d6b.documents.azure.com:443/
check():  ok=True detail='OK'
read_latest(): 905 readings
read_window(6h): 273 samples
```

That exercises the connector's own partition-key construction, paging, parsing
and flattening against the documents actually in the live container. So the role
assignment is the *only* missing piece - there is no second surprise waiting
behind it.

Two things this also settles:

- **Account keys are not an alternative.** `disableLocalAuth` is `false`, so keys
  would work at the Azure level, but the developer cannot read them:
  `listKeys` is in the same `notActions` list. There is no way around step 1.
- **The chart will draw a flat line.** Every sample of
  `/dex/COMMAN_FORBAY_LEVEL` in the live container reads `132.0436248779297`,
  because all 289 synthetic documents are one snapshot replayed. That is correct
  behaviour on fabricated input, not a broken axis.

### What to do once the grant exists

Step 1 is the only part that needs someone else. The developer **owns the
`aimms-pumphouse-connector` app registration**, so steps 2-5 need no further
favours. Verified 2026-09-19: owner is `Aniket`, `passwordCredentials: 0`.

**1. Someone with Owner / Contributor / DocumentDB Account Contributor** runs the
role assignment command given above. Nothing below works until this lands.

**2. Issue a credential** for the identity (developer can do this):

```zsh
az ad app credential reset \
  --id 08a359e2-7133-43ee-b100-26a0e0db1bd3 \
  --display-name inventree-dev --years 1
```

Record `appId`, `password`, `tenant`. Treat the password as a secret: it is a
credential for an identity that, after step 1, can read plant telemetry.

**3. Give the container the credential.** `DefaultAzureCredential` picks up
`EnvironmentCredential` first, so these three variables are enough - no CLI, no
mounted token cache, no code change:

```yaml
# contrib/container/dev-docker-compose.yml, inventree-dev-server environment:
AZURE_CLIENT_ID: ${AZURE_CLIENT_ID:-}
AZURE_TENANT_ID: ${AZURE_TENANT_ID:-}
AZURE_CLIENT_SECRET: ${AZURE_CLIENT_SECRET:-}
```

Keep the values in a local `.env`, never in the compose file.

**4. Point the source at the live account.** `secret_ref` must be **empty** - the
connector refuses a key against a real endpoint by design, so leaving the
emulator's key reference set produces a `CosmosConfigError` rather than a
fallback:

```python
source = HealthSource.objects.get(pk=1)
source.secret_ref = ''
source.config['endpoint'] = 'https://epconchatcosmos9d6b.documents.azure.com:443/'
source.save()
```

**5. Verify before trusting the dashboard**, because a blank chart and a failed
auth look identical on screen:

```zsh
python src/backend/InvenTree/manage.py check_pumphouse_readiness \
  --source 1 --probe --allow-incomplete
```

### State of the live container

As of 2026-09-19 the live container holds **293 documents**: the 4 original
hand-seeded pilot snapshots from 2025-07-18, plus **289 marked `synthetic: true`**
copied from the emulator to give the dashboard a recent series to draw.

Those 289 are **not telemetry** - see Ask 1: they are one snapshot taken with the
station shut down, replayed onto later timestamps. They exist so the read path can
be demonstrated end to end. Remove them before anyone treats this container as a
record of plant behaviour:

```zsh
python contrib/cosmos/devtools/push_to_live.py \
  --endpoint https://epconchatcosmos9d6b.documents.azure.com:443/ \
  --purge-synthetic --confirm
```

A single push is a point-in-time copy, and it decays in two stages that are easy
to misread. After **300 s** - the source's freshness threshold - every reading in
it reads as stale, which looks exactly like a broken connector. After **six
hours** the trend chart's window has moved past it entirely and the chart is
empty. Measured: 54 minutes after the first push, `last 10 minutes` was already 0.

`keep_live_fresh.sh` holds both open by re-copying the tail every 60 s:

```zsh
nohup sh contrib/cosmos/devtools/keep_live_fresh.sh > /tmp/live_sync.log 2>&1 < /dev/null & disown
pkill -f keep_live_fresh.sh      # stop it
```

It runs on the **host**, not in the container, because the Azure CLI is on the
host and the Cosmos SDK is in the container - neither side has both. It re-mints
the token every cycle rather than once, since a token lasts about an hour and a
loop that minted once would fail silently overnight, with stale readings as the
only symptom.

It requires `keep_emulator_fresh.sh` to be running: it copies what the emulator
produces and invents nothing. If the emulator loop is dead, this one will happily
re-copy the same unchanging tail.

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
