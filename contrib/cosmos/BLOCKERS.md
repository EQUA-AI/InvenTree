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
