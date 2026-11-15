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
ontology, generated SDK distribution/module/version, object/property mappings
and credential-store reference are still required. Recording this origin does
not enable the transport or verify an authenticated Foundry read.

The supplied hosted application is
`https://aimms-demo-code.equaai.palantirfoundry.com/`, with browser callback
`https://aimms-demo-code.equaai.palantirfoundry.com/auth/callback`. These are
browser application coordinates; the backend transport origin remains the
Foundry origin above. They do not identify the generated Python SDK package or
its selected ontology.

Current connectivity blocker: unauthenticated HTTPS GET checks from the agent
environment returned HTTP 403 and Palantir's **Network Blocked** page for both
origins. The page states that this connection is outside the enrollment's
ingress allowlist. TLS verification passed. This blocks access before SDK
authentication and does not establish credential validity or resource access.
An enrollment administrator can confirm an existing approved staging runtime,
network or VPN. Repeat connectivity and authenticated SDK checks from that
approved runtime; this agent's current network has not been qualified.

From a machine/network where Foundry opens, use Developer Console to open the
AIMMS application and select the Python SDK setup/documentation. Supply its
package/module/version and initialization instructions without tokens or
secrets, together with the reviewed object/property mappings and credential
store reference. The hosted app and callback URLs cannot supply these details
from this blocked connection.

Create a restricted backend application in Foundry Developer Console. Generate
the Python SDK for the selected ontology and machine/location types. Grant its
service user only the required staging resources and request the
`api:ontologies-read` scope. The service user's permissions do not grant native
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

## Official SDK references

- [Python OSDK 2.x, object projections and paging semantics](https://www.palantir.com/docs/foundry/ontology-sdk/python-osdk/)
- [Public Config options](https://www.palantir.com/docs/foundry/ontology-sdk/python-osdk-migration/)
- [ConfidentialClientAuth usage](https://www.palantir.com/docs/foundry/compute-modules/osdk-integration/)
- [Restricted application setup](https://www.palantir.com/docs/foundry/developer-console/create-application/)
- [Service-user client credentials](https://www.palantir.com/docs/foundry/consumer-mode/client-credentials-setup/)
