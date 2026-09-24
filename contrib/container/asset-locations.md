# Physical locations and demo placement

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

## Demo initialization

The bundled `assets/demo_machine_data.json` declares 38 locations and explicit
placement codes for 16 owned demo machines. Five roots represent Plant A, ACME,
Tomahawk Creek WRF, Industrial Water Plant and Collection System. The illustrative
site timezone is America/Chicago. These are demo declarations, not inferred
production geography or authorization boundaries.

After the base machine demo dataset exists, run from the backend directory:

```sh
python manage.py load_asset_location_demo --actor OPERATOR_USERNAME --dry-run
python manage.py load_asset_location_demo --actor OPERATOR_USERNAME
```

The operator must already hold maintenance add/change authority and client scope.
The loader does not create users or grants. It verifies machine ownership using
the existing demo identity and managed-part checks. An unowned location collision
fails the whole transaction. Reruns do not append duplicate histories or move
machines that an operator has already assigned or unassigned. Existing edited
location metadata causes a conflict for review instead of being reset.

Placement starts at import time. Earlier placement remains unknown; historical
maintenance cards, schedules and legacy location labels remain unchanged.

## Verification and release scope

```sh
python manage.py test assets.test_locations assets.test_demo_data --keepdb --noinput
```

The hierarchy suite includes permissions, client isolation, cycle prevention,
archival rules, stale-version conflicts, atomic batches, idempotency and
independent-connection PostgreSQL concurrency checks. Demo tests include rollback,
ownership conflicts and preservation of later operator moves.

The frontend uses Mantine 9 Tree, Select, Modal and useForm APIs, checked against
the installed 9.6.1 types and current documentation at
<https://mantine.dev/llms.txt>. Browser checks run on localhost:5173 cover creating
roots/children, transfers, All Machines, Unassigned, deep links, and reconciliation
of direct versus descendant scope. The existing mobile full-app opt-in remains.

This release is the hierarchy and placement foundation. Location historical
analytics, the richer Machine About overview, criticality/priority workflows,
the shared location-filtered maintenance queue and OEE remain future work. Current
machine and open-work-order counts are not historical downtime metrics.
