# Live Python OSDK transport plan

The optional single-object backend pilot is implemented. Live Foundry
verification and native list/AI/history adoption are separate, pending stages.
New files under `docs/` remain ignored; this operator runbook is part of the
source change on `aimms-pltr`.

## Sequence and acceptance

1. **Server transport:** exact generated SDK package/version, read-only client
   credentials, verified TLS, fixed property projection, process deadline and
   bounded result protocol. Test SDK calls, refusal paths and worker cleanup.
2. **Native pilot:** authenticated single machine/location reads, native role,
   scope and explicit grant before network access and before disclosure.
   Require an independently reviewed field policy and exact remote identity,
   incarnation, environment and client matches. Off by default; no cache.
3. **Tenant setup (operator inputs required):** Foundry hostname, generated
   Python OSDK 2.x distribution/module/version and wheel hashes, its selected
   ontology and object API names, property mapping, secret-store reference,
   staging synthetic source object and native actor/grant. Lock the generated
   wheel and its transitive runtime in the deployment environment. There is no
   universal generated OSDK package to add to the shared requirements file.
   Run staging checks from an enrollment-approved network; confirm the backend
   runtime's egress is covered by the enrollment ingress allowlist.
4. **Staging qualification:** run the offline command, then use actual native
   authentication against the pilot endpoint. Exercise authorized/foreign,
   missing grant, revoked role/token/grant, expired/rotated application secret,
   missing object, bad mapping, wrong environment/incarnation, timeout and
   interrupted SDK calls. Retain sanitized receipts bound to source, installed
   wheel hashes and staging configuration. Never retain tokens or secrets.
5. **Consumer rollout:** separately qualify population traversal, paging,
   collation, totals, snapshot/writer completeness, source freshness, the 179
   outstanding field/relationship decisions and all-domain native authorization
   before installing S2 adapters or adding S3/S6/AI/export paths. Historical
   attribution and deletion-readiness policy remain independent. Close C/K
   gates only against their original evidence requirements and human review.

Steps 1–2 have local synthetic tests. No tenant connection, real client
authorization, deployment or C/K acceptance is implied by those tests.

## Configure the staging pilot

The supplied staging origin is `https://equaai.palantirfoundry.com`. The
SDK coordinates supplied for the backend are distribution
`aimms_backend_staging_sdk`, version `0.2.0`, with object imports under
`aimms_backend_staging_sdk.ontology.objects`. The transport's `sdk_module`
setting is the root `aimms_backend_staging_sdk`; it appends `.ontology.objects`
itself. The reported generated identifier is `aimmsMachine`, with a string
primary key named `machineKey`. The supplied artifact credential enabled a
download of the exact wheel, whose SHA-256 matched the package index. Static
inspection and a network-isolated import/constructor/signature probe verified
the lowercase class, string primary key, HTTP Config options and projected-get
interface. The generated client defaults to the previously verified ontology.
The SDK and its 29-package dependency closure are installed in a separate
inspection environment; the Django environment is unchanged. The authenticated
MCP token was not reused for artifact downloads, and artifact credentials are
not stored in this repository or the runtime lock.

Public Python SDK identifiers are selected with their exact case, including
lowercase names; dotted/private identifiers and Python keywords are rejected.
The complete native property mapping, exact remote key, confirmed native
staging backend/environment and service credential-store reference are still
required. Historical candidate IDs and namespace prefixes are not current
authority. The current lookup uses the decimal native pk; composite
`machineKey` values require separately reviewed key resolution. These inputs
do not enable the transport or qualify the generated SDK against Foundry.

The supplied hosted application is
`https://aimms-demo-code.equaai.palantirfoundry.com/`, with browser callback
`https://aimms-demo-code.equaai.palantirfoundry.com/auth/callback`. These are
browser application coordinates; the backend transport origin remains the
Foundry origin above. They do not identify the generated Python SDK package or
its selected ontology.

Current connectivity after the supplied ingress change: the Foundry origin
returns HTTP 307 to `/workspace`, which returns HTTP 307 to `/multipass/login`.
The hosted application still returns HTTP 403 and Palantir's **Network
Blocked** page, identifying this connection as outside the enrollment ingress
allowlist. Fresh no-cache GET checks reproduce these different outcomes; TLS
verification passes for both origins. Foundry sign-in reachability does not
establish credential validity, SDK access or resource authorization.
An enrollment administrator can confirm the rule applicable to the hosted
application and an approved staging runtime/network. Repeat authenticated SDK
checks from that approved runtime; live SDK access remains unqualified.

The supplied application-specific Ontology MCP endpoint is now reachable:
unauthenticated access returns HTTP 401 **Bearer token required**, and both
protected-resource and authorization-server discovery return HTTP 200 with
verified TLS. These observations are separate from the hosted app's ingress
failure. The supplied application RID identifies a Developer Console
application; it is not the missing ontology RID.

The MCP connection has been registered in repository-local Claude Code user
settings using the supplied public client ID and callback port 8080. OAuth
sign-in is complete and Claude Code reports **Connected**. A separate MCP
Python client initialized a live session, listed 12 tools and successfully
read the ontology RID, accessible object-type catalog and telemetry schema.
No entity data was retrieved. No SQL, anomaly, semantic-search or action tools
were invoked. Detailed receipts are retained in ignored local review evidence.
Its loopback redirect is
`http://localhost:8080/callback`, separate from the hosted browser app's
`/auth/callback`. Resume authentication from this repository with
`claude mcp login --no-browser aimms-demo-code-app`. Keep login links, codes,
tokens, client secrets and user MCP configuration outside Git. A Claude Code
registration does not attach tools to this Codex chat or configure the Python
OSDK backend. Direct MCP metadata inspection is now verified separately from
the generated Python SDK/service-user read, which remains unqualified.

The accessible catalog does not expose a machine or location type meeting the
native pilot contract. The available raw telemetry type has four string
properties, an experimental release status and a source-record primary key;
its metadata describes the timestamp as unnormalized text. It lacks the six
distinct mapped properties and native client/lifecycle bindings required below.
This observation is scoped to this connection's accessible catalog, not the
entire ontology. Create or expose the reviewed machine/location types and
bindings, generate/pin their Python SDK and configure service credentials
before enabling the pilot. A telemetry consumer needs its own reviewed
normalization and native association contract.

From a machine/network where Foundry opens, use Developer Console to open the
AIMMS application and select the Python SDK setup/documentation. Supply its
package/module/version and initialization instructions without tokens or
secrets, together with the reviewed object/property mappings and credential
store reference. The hosted app and callback URLs cannot supply these details
from this blocked connection.

Create a restricted backend application in Foundry Developer Console. Generate
the Python SDK for the selected ontology and machine/location types. Grant its
service user only the required staging resources and request the
`api:use-ontologies-read` scope for a new restricted application. The service user's permissions do not grant native
AIMMS users access. Keep client secrets in the server secret store.

Trusted server settings must assign a `TransportConfig` instance to
`AIMMS_PALANTIR_TRANSPORT`. It contains the HTTPS origin (no path/trailing slash),
exporter environment namespace, installed distribution/module/exact version,
one or two `ObjectMapping` instances, secret reference, credential resolver and
field policy. There is no browser or request-based configuration surface.
Use a deployment-owned Django settings module which imports the normal
`InvenTree.settings` and then assigns this object, selected through
`DJANGO_SETTINGS_MODULE`. Keep that module and secret-provider wiring outside
the repository. Bound credential lookup in the resolver itself; the worker's
hard deadline starts after credential resolution. Allow one second for worker
cleanup after a deadline.

The resolver accepts the secret reference and returns
`integration_state.palantir_transport.Credentials(client_id, client_secret)`.
It is called afresh on every read. `authorize_fields(request, domain, pk,
fields)` must return literal `True` for the exact tuple `('pk', 'client',
'name')`, and independently authorize any field restrictions that native role,
scope and grant checks do not express. No permissive policy is supplied.

Each mapping names a generated Python object class and its Python properties
for `pk`, `client`, `client_incarnation`, `incarnation`, `environment`, `name`.
Those six property
names must be distinct. Use names from the generated SDK documentation, not
display labels or arbitrary property paths. Example structural configuration:

```python
from integration_state.palantir_transport import ObjectMapping, TransportConfig

# All values and functions below come from your reviewed deployment module.
AIMMS_PALANTIR_TRANSPORT = TransportConfig(
    origin='https://equaai.palantirfoundry.com',
    environment=reviewed_export_namespace,
    distribution=generated_distribution,
    sdk_module=generated_import_name,
    sdk_version=locked_generated_version,
    mappings=(ObjectMapping(
        domain='machines', object_type=machine_python_type,
        pk=machine_pk_property, client=client_pk_property,
        client_incarnation=client_birth_timestamp_property,
        incarnation=machine_uuid_property, environment=namespace_property,
        name=machine_name_property, key_type='str',
    ),),
    secret_reference=application_secret_reference,
    resolve_credentials=read_application_credentials,
    authorize_fields=reviewed_native_field_policy,
)
```

The remote primary key is the native decimal pk, as string or integer according
to `key_type`. Remote `pk` and `client` properties must be positive integers.
Machine incarnation is `str(AssetMachine.uuid)`; location incarnation is the
exact native `created_at.isoformat()` string. Export the configured namespace
verbatim, and the owning client's exact `created_at.isoformat()` as
`client_incarnation` so that reusing its integer pk cannot reuse an old binding.
These identities must be populated in Foundry before reading; this
transport does not ingest data or infer missing ownership. The generated SDK
binds its ontology: deployment must verify the package was generated for the
intended ontology; this transport does not discover or override it by RID.

Run with your normal server configuration:

```sh
python manage.py check_palantir_transport --require-configured
```

This command checks the package metadata/layout without importing the SDK,
resolving secrets or accessing the network. Its `live_verified` is always false.
Then issue an authenticated native GET to one of:

```text
/api/integration/palantir/machines/<native-pk>/
/api/integration/palantir/locations/<native-pk>/
```

A successful response contains only `pk`, `client`, `name` and an explicit
`single_observation` consistency label. No full object, token, digest, host or
private mapping is returned. Missing configuration/domain returns 404; denials
return 403; selected-provider failures return fixed 502/503/504 errors, never
native fallback or an empty successful result. Set the configuration back to
`None` to disable the pilot. Native asset lists and AI/history are unchanged.
The service also refuses reads during `INVENTREE_RESTORE_HOLD` and with
unqualified database routers, including a hold enabled during a remote read.
Only GET and OPTIONS are supported; there is no write/action endpoint.

Each read obtains a new application token in a short-lived worker
(`should_refresh=False`); the next read reacquires it. SDK config verifies TLS,
uses a finite request timeout and disables configured proxies. The worker
inherits no application/token/proxy environment variables. Parent-enforced
wall time covers SDK authentication and any SDK retries; no parent retry is
added. A nonblocking per-server-process limit admits at most four concurrent
credential/SDK reads. Worker output is capped at 16 KiB, SDK output is discarded and the
process group is killed/reaped on completion or failure. This Linux/POSIX
implementation is intended for the existing backend deployment. Configure CA
lookup through `SSL_CERT_FILE`/`SSL_CERT_DIR` if required.

Limits: SDK/network input and worker memory are not capped here; stage in an
appropriately constrained service/container. The generated package is trusted
executable code, not sandboxed. There is no turn-wide snapshot, atomic source
and native read, ABA protection, retroactive revocation, historical attribution
or sync completeness guarantee. Version/layout checks are not independent
wheel integrity verification. No SDK API signature compatibility or live
behavior is claimed until the actual generated package has passed step 4.

## Optional isolated SDK worker

The generated SDK can use a dedicated interpreter selected by trusted server
configuration as `TransportConfig.worker_python`. Leave it `None` to use the
Django interpreter. A configured value must be an absolute executable file
path; it is never accepted from request parameters or provider data. Reads
still use the same credential pipe, timeout, byte budget and native checks.
The offline management check inspects package metadata in that interpreter
without importing the SDK, resolving credentials or contacting Foundry.

`requirements-sdk-3.13.in` and its generated hash lock describe the verified
Linux x86-64 / CPython 3.13 worker dependency set. This optional environment is
separate from backend requirements. Its wheels must be obtained through the
approved artifact repositories and kept outside Git. For an owned worker
environment and a prepared wheelhouse:

```sh
python3.13 -m venv "$AIMMS_SDK_RUNTIME"
"$AIMMS_SDK_RUNTIME/bin/python" -m pip install \
    --no-index --require-hashes --only-binary=:all: \
    --find-links "$AIMMS_SDK_WHEELHOUSE" \
    -r contrib/palantir/requirements-sdk-3.13.txt
```

Set `worker_python` to that environment's `bin/python` in the deployment-owned
settings module. The lock was generated with UV 0.11.12, using the inspected
wheelhouse as the only package source. To regenerate it from reviewed wheels:

```sh
uv pip compile --no-index --find-links "$AIMMS_SDK_WHEELHOUSE" \
    --python-version 3.13 --generate-hashes --no-header --no-annotate \
    contrib/palantir/requirements-sdk-3.13.in \
    -o contrib/palantir/requirements-sdk-3.13.txt
```

The actual generated machine schema exposes native-looking `source_id` and
`source_uuid` fields and a string `client_key`; it does not expose separate
native client-incarnation and environment fields. Schema names alone do not
establish live ownership or a current native identity. A reviewed mapping/key
contract and confirmed staging context remain necessary. Import and metadata
checks do not qualify a live service-user or native machine-data read.

## Azure Container Apps credentials

The backend is reported to run on Azure Container Apps. Store the saved
`aimms-backend-staging` client secret in Azure Key Vault. Its public client ID
and the exact Key Vault secret URI are still operator inputs. These credentials
are separate from the artifact-download token and the browser/MCP application.

Configure the reference chain in the Azure portal:

1. Enable a managed identity on the Container App under **Security → Identity**.
   Grant that identity **Key Vault Secrets User** access to the secret, using
   the vault's Azure RBAC configuration. Ensure the vault's network rules allow
   retrieval from this deployment.
2. Under **Security → Secrets → Add**, create `foundry-secret`, select
   **Key Vault reference**, enter the secret URI and select that identity.
3. Create a new revision, edit the backend container's environment variables
   and supply the following values. Deploy the revision with the reviewed
   transport configuration and SDK runtime.

| Environment variable | Container App configuration |
| --- | --- |
| `AIMMS_PALANTIR_CLIENT_ID` | Plain value: the backend application's public client ID |
| `AIMMS_PALANTIR_CLIENT_SECRET` | Source: **Reference a secret**; select `foundry-secret` |

Azure injects the resolved value. A literal Key Vault URL in a plain environment
variable does not perform this lookup. In the existing deployment-owned
`TransportConfig`, select the built-in resolver explicitly:

```python
from integration_state.palantir_transport import (
    ENV_SECRET_REFERENCE,
    environment_credentials,
)

# Use these two keyword arguments in the reviewed TransportConfig above:
# secret_reference=ENV_SECRET_REFERENCE,
# resolve_credentials=environment_credentials,
```

This resolver reads only the two fixed variable names, refuses missing or
malformed values and unresolved reference expressions, and returns fixed public
errors. It neither contacts Key Vault nor logs/caches credentials. The parent
process supplies credentials to the SDK worker through its private stdin pipe;
the worker does not inherit these environment variables. Azure managed identity
authorizes Key Vault retrieval; Foundry client credentials authorize SDK reads.
No Azure SDK dependency is needed for this platform-resolved path. Variables
alone do not configure or enable `AIMMS_PALANTIR_TRANSPORT`.

For automatic rotation, use a versionless URI such as
`https://<vault>.vault.azure.net/secrets/<secret-name>`. Microsoft documents
refresh within 30 minutes and automatic restart of active revisions using the
secret as an environment variable. A versioned URI pins that version; update
the reference and roll out the revision deliberately. Process environment
variables do not update themselves. Coordinate Foundry rotation and deployment:
invalidating the old client secret before new containers receive the replacement
can interrupt authentication. A prompt controlled rollout should be verified
with an authorized staging read rather than waiting on automatic refresh.

The local checks use synthetic credentials and a synthetic SDK. Azure identity,
Key Vault access, deployed secret injection and live Foundry authentication have
not been verified. The mapping and staging qualification requirements above
still apply. See Microsoft's
[Container Apps secrets guide](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
for reference setup, networking and rotation behavior.

## Official SDK references


- [Python OSDK 2.x, object projections and paging semantics](https://www.palantir.com/docs/foundry/ontology-sdk/python-osdk/)
- [Public Config options](https://www.palantir.com/docs/foundry/ontology-sdk/python-osdk-migration/)
- [ConfidentialClientAuth usage](https://www.palantir.com/docs/foundry/compute-modules/osdk-integration/)
- [Restricted application setup](https://www.palantir.com/docs/foundry/developer-console/create-application/)
- [Service-user client credentials](https://www.palantir.com/docs/foundry/consumer-mode/client-credentials-setup/)
- [Current application restrictions and scope prefixes](https://www.palantir.com/docs/foundry/developer-console/application-restrictions/#scope-prefixes)
- [Ontology MCP authentication and authorization](https://www.palantir.com/docs/foundry/ontology-mcp/authentication-and-authorization/)
