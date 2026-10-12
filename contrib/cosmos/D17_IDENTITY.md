# D17: which identity AIMMS reads Cosmos as

Status 2026-10-12: **the read-only role is in place and proven for the app
registration. The remaining questions for the Azure owner are below.**
Account `epconchatcosmos9d6b` (resource group `EpconChat`, subscription
"Microsoft Azure Sponsorship"), database `aimms`, container `pumphouse_readings`.

## What is assigned (Cosmos data-plane roles)

`…0001` is Cosmos DB Built-in Data Reader; `…0002` is Built-in Data Contributor
(read and write).

| Principal | Kind | Role | Scope | Whose |
|---|---|---|---|---|
| `aimms-pumphouse-connector` (app `08a359e2…`, object `b46af2ad…`) | App registration | Reader | `aimms/pumphouse_readings` | AIMMS, used by the local Azure-pointed server |
| `inventree-worker` (object `c85eedb5…`) | Managed identity | Reader | `aimms/pumphouse_readings` | AIMMS, presumably the deployed worker |
| `Aniket@equa.work` (`f024cd79…`) | User | **Contributor** | whole account | Developer, for seeding documents |
| `aimms-experimental` (`d5213280…`) | Managed identity | Reader | whole `aimms` database | unknown |
| `cosmos-to-palantir` (`7ac20b91…`) | App registration | Reader | whole account | another integration |

The only role-assignment write in the last 90 days is by Lokesh@equa.work at
2026-10-09 20:52 UTC; the activity log does not say which one it was.

## Proof

`contrib/cosmos/devtools/prove_read_only.py`, run in `aimms-azure-server`
(credential: `aimms-pumphouse-connector` client secret), 2026-10-12:

```
READ   pumphouse_readings: ok (partition key ['/station_uuid', '/hour_bucket'], sample rows 1)
WRITE  refused by RBAC (HTTP 403): read-only enforced by Azure
SCOPE  telemetry: HTTP 403 (outside the role)
SCOPE  conversations: HTTP 403 (outside the role)
RESULT read-only as required
```

The write probe deletes an id that does not exist, so it changes nothing whatever
the role: a reader gets 403, a writer would get 404.

## Answer to D17 as far as it can be read from here

| Environment | Identity | Evidence |
|---|---|---|
| Local, pointed at Azure | `aimms-pumphouse-connector` via `AZURE_CLIENT_ID/SECRET/TENANT_ID` | proven above |
| Local, emulator | emulator key in `COSMOS_EMULATOR_KEY` | unchanged |
| Deployed | `inventree-worker` managed identity, by its name and scope | **not verified**: our account cannot read the hosting resource group |

## Asks for the Azure owner (Lokesh)

1. **Confirm the deployed app and worker authenticate as `inventree-worker`.**
   The connector builds `DefaultAzureCredential()` with no managed-identity
   client id (`machine_health/connectors/cosmos_pumphouse.py`). If
   `inventree-worker` is a *user-assigned* identity, the deployed container must
   set `AZURE_CLIENT_ID` to that identity's client id, and must not also carry an
   app-registration secret, or the credential chain picks the wrong principal or
   none; a *system-assigned* identity needs neither. We could not tell which,
   because our account cannot read the hosting resource group. Running
   `prove_read_only.py` in the deployed container settles it before polling is
   switched on (HANDOVER open item 2).
2. **The developer's account-wide Data Contributor.** It was granted for seeding
   the pilot documents. Remove it, or narrow it to `aimms/pumphouse_readings`,
   once no more seeding is planned. It is not used by the app.
3. **`aimms-experimental` reads the whole `aimms` database**, including
   `conversations`. If it is an AIMMS identity, narrow it to the container it
   needs; if not, say whose it is.
4. **Client secrets of `aimms-pumphouse-connector`** (`inventree-dev`,
   `inventree-dev-container`) expire on **2027-09-19**. Owner: Aniket@equa.work.
   Put a renewal on the calendar, or move the local setup to `az login`.

Nothing above was changed by us; every check was read-only.
