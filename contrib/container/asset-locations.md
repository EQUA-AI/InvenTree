# Physical locations and audited placement

This release adds client-owned Sites & Facilities, nested locations, current
machine placement and audited transfers. Machine `location` text is retained as
a legacy label. Existing machines start unassigned; no migration infers location
identity or past placement from those strings.

The workspace uses the existing `work_order` view/add/change roles plus
`AIMMS_MAINTENANCE_SCOPE_RESOLVER`. A physical node is not a scope grant. Unresolved
scope denies access. The deployment's existing explicit-grant resolver is
`tasks.scope.granted_client_scope_resolver`.

Apply migrations with the normal web deployment migration owner. The generic
worker must not independently enable automatic migrations. There are no new
background jobs in this release.

## Production placement

Create physical locations through the authorized location workspace/API, then
move machines through the audited transfer flow. Confirm client scope, location
identity, timezone and actual placement; do not infer them from legacy labels.
Synthetic machine/location loaders are not shipped in this consolidation.
Existing records are retained and are not moved or rewritten by this change.

## Verification and release scope

```sh
python manage.py test assets.test_locations --keepdb --noinput
```

The hierarchy suite includes permissions, client isolation, cycle prevention,
archival rules, stale-version conflicts, atomic batches, idempotency and
independent-connection PostgreSQL concurrency checks.

The frontend uses Mantine 9 Tree, Select, Modal and useForm APIs, checked against
the installed 9.6.1 types and current documentation at
<https://mantine.dev/llms.txt>. Browser checks run on localhost:5173 cover creating
roots/children, transfers, All Machines, Unassigned, deep links, and reconciliation
of direct versus descendant scope. The existing mobile full-app opt-in remains.

This release is the hierarchy and placement foundation. Location historical
analytics, the richer Machine About overview, criticality/priority workflows,
the shared location-filtered maintenance queue and OEE remain future work. Current
machine and open-work-order counts are not historical downtime metrics.
