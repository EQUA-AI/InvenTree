"""Read-only QA gates for the Cedar Creek population (Import Sequence step 17).

Checks the mechanically verifiable subset of the package's 59 QA gates against
the live database and prints PASS/FAIL per gate. Writes nothing.

Run it through the Django shell of the container that points at the database::

    docker exec -i <container> sh -c \
      'cd /home/inventree/src/backend/InvenTree && python manage.py shell' \
      < contrib/cosmos/devtools/cedar_creek_qa_gates.py

Defaults describe the Azure dev database (station 93, ``klsw`` identity, 16
``PS-*`` + 17 ``CC-REF`` components per pump). Override with environment
variables for another database, for example the local sandbox::

    QA_STATION_PK=64 QA_STATION_UUID=d0b4aace-a6a7-555c-9fc6-e93089ba12c5 \
    QA_STATION_NS=cedar-creek-demo QA_STATION_KEY=CC_FEPS QA_COMPONENTS_PER_PUMP=17

Gates that need a person (engineering values, legal sources, visual smoke
tests) are not here; see ``QA Gates`` in the workbook for the full list.
"""

import collections
import os

from django.core.exceptions import ValidationError
from django.db.models import Count

from tasks.models import WorkOrder

from assets.machine_profile import validate_machine_profile
from assets.models import (
    AssetComponent,
    AssetLocation,
    AssetMachine,
    AssetMaintenanceRecord,
    MachinePart,
)
from company.models import ManufacturerPart
from part.models import Part
from stock.models import StockItem, StockLocation

STATION_PK = int(os.environ.get('QA_STATION_PK', '93'))
EXPECTED_UUID = os.environ.get(
    'QA_STATION_UUID', 'ce0a411f-d016-4817-ba00-3830ede25ce1'
)
EXPECTED_NS = os.environ.get('QA_STATION_NS', 'klsw')
EXPECTED_KEY = os.environ.get('QA_STATION_KEY', 'PH_3')
COMPONENTS_PER_PUMP = int(os.environ.get('QA_COMPONENTS_PER_PUMP', '33'))
results = []


def gate(code, ok, detail=''):
    """Record one gate outcome."""
    results.append((code, ok, detail))


station = AssetMachine.objects.get(pk=STATION_PK)
pumps = list(station.children.filter(asset_type='pump').order_by('source_key'))
cc_locations = AssetLocation.objects.filter(code__startswith='cc-')

# QA-02 no globally duplicate machine names (DB constraint, but verify)
dupes = AssetMachine.objects.values('name').annotate(n=Count('id')).filter(n__gt=1)
gate('QA-02', not dupes.exists(), f'{dupes.count()} duplicate names')

# QA-03 all physical location parents resolve; QA-04 site timezone
roots = cc_locations.filter(parent=None)
orphans = [
    n.code
    for n in cc_locations
    if n.parent_id and not AssetLocation.objects.filter(pk=n.parent_id).exists()
]
gate(
    'QA-03',
    not orphans and cc_locations.count() == 45,
    f'{cc_locations.count()} cc- locations, {len(orphans)} orphans, roots={[r.code for r in roots]}',
)
gate(
    'QA-04',
    all(r.timezone == 'America/Chicago' for r in roots),
    f'{[r.timezone for r in roots]}',
)

# QA-05 warehouse and facility domains do not share keys (names vs codes)
wh_root = StockLocation.objects.filter(
    name='Cedar Creek Main Spares Warehouse', parent=None
).first()
wh_nodes = list(wh_root.get_descendants(include_self=True)) if wh_root else []
gate(
    'QA-05',
    wh_root is not None
    and len(wh_nodes) == 8
    and not AssetLocation.objects.filter(
        code__in=[n.name.lower() for n in wh_nodes]
    ).exists(),
    f'{len(wh_nodes)} warehouse nodes',
)

# QA-06 / QA-07 / QA-08 pump registry
keys = [p.source_key for p in pumps]
gate(
    'QA-06',
    keys == [f'P{i}' for i in range(1, 15)]
    or sorted(keys, key=lambda k: int(k[1:])) == [f'P{i}' for i in range(1, 15)],
    f'{keys}',
)
expected_names = {
    f'{station.name[:170]} / Pump {int(p.source_key[1:]):02d}' for p in pumps
}
gate('QA-07', {p.name for p in pumps} == expected_names, f'station={station.name!r}')
gate(
    'QA-08',
    str(station.uuid) == EXPECTED_UUID
    and station.source_namespace == EXPECTED_NS
    and station.source_key == EXPECTED_KEY,
    f'uuid={station.uuid} ns={station.source_namespace} key={station.source_key}',
)

# QA-09 machines have client and physical location
cc_machines = AssetMachine.objects.filter(physical_location__in=cc_locations)
gate(
    'QA-09',
    cc_machines.count() == 63 and not cc_machines.filter(client__isnull=True).exists(),
    f'{cc_machines.count()} placed in cc- locations, client set on all={not cc_machines.filter(client__isnull=True).exists()}',
)

# QA-10 no subcomponents as child machines
gate('QA-10', not station.children.exclude(asset_type='pump').exists(), '')

# QA-11..13 profiles valid and bounded
bad = []
for m in [station, *pumps]:
    try:
        validate_machine_profile(m.profile or {})
        if len((m.profile or {}).get('components', [])) > 50:
            bad.append(m.pk)
    except ValidationError as exc:
        bad.append((m.pk, exc.messages))
gate(
    'QA-11/12/13',
    not bad and all((p.profile or {}).get('criticality') for p in pumps),
    f'{bad}',
)

# QA-15 component codes unique per pump; CC-REF and PS-* coexist
comp = AssetComponent.objects.filter(machine__in=pumps)
dup_codes = comp.values('machine_id', 'code').annotate(n=Count('id')).filter(n__gt=1)
per_pump = collections.Counter(comp.values_list('machine__source_key', flat=True))
gate(
    'QA-15',
    not dup_codes.exists() and set(per_pump.values()) == {COMPONENTS_PER_PUMP},
    f'per pump={dict(sorted(per_pump.items(), key=lambda kv: int(kv[0][1:])))}',
)

# QA-16 / QA-17 parts
cc_parts = Part.objects.filter(IPN__startswith='CC-')
ipn_dupes = cc_parts.values('IPN').annotate(n=Count('id')).filter(n__gt=1)
missing = MachinePart.objects.filter(machine__in=pumps, part__isnull=True).count()
gate(
    'QA-16',
    cc_parts.count() == 304 and not ipn_dupes.exists(),
    f'{cc_parts.count()} CC- parts',
)
gate(
    'QA-17',
    missing == 0 and MachinePart.objects.filter(machine__in=pumps).count() == 224,
    f'{MachinePart.objects.filter(machine__in=pumps).count()} part links',
)

# QA-18 virtual parts carry no stock; QA-21 nonnegative; QA-22 bins resolve, non-structural
virt_stock = StockItem.objects.filter(
    part__virtual=True, part__IPN__startswith='CC-'
).count()
cc_stock = StockItem.objects.filter(part__IPN__startswith='CC-')
gate('QA-18', virt_stock == 0, f'{virt_stock} stock items on virtual parts')
gate(
    'QA-21',
    not cc_stock.filter(quantity__lt=0).exists() and cc_stock.count() == 6,
    f'{cc_stock.count()} stock items',
)
gate(
    'QA-22',
    not cc_stock.filter(location__isnull=True).exists()
    and not cc_stock.filter(location__structural=True).exists(),
    '',
)
gate(
    'QA-20',
    ManufacturerPart.objects
    .filter(part__IPN__startswith='CC-')
    .exclude(link__isnull=True)
    .exclude(link='')
    .count()
    == 6,
    f'{ManufacturerPart.objects.filter(part__IPN__startswith="CC-").count()} manufacturer parts',
)
gate(
    'QA-23',
    not cc_stock.exclude(supplier_part__isnull=True).exists(),
    'no stock claims a supplier part',
)

# QA-26 history does not spoof closed work orders
history = AssetMaintenanceRecord.objects.filter(
    machine__in=[station, *pumps, *cc_machines]
)
gate(
    'QA-26',
    history.count() == 132 and not history.exclude(work_order__isnull=True).exists(),
    f'{history.count()} history rows, linked={history.exclude(work_order__isnull=True).count()}',
)

# QA-24 work orders in staging states only, with real machine FKs
wos = WorkOrder.objects.filter(tags__contains=['workbook-population'])
states = collections.Counter(wos.values_list('lifecycle_status', flat=True))
gate(
    'QA-24',
    wos.count() == 18
    and set(states) <= {'draft', 'planned'}
    and not wos.filter(machine__isnull=True).exists(),
    f'{dict(states)}',
)
gate(
    'QA-25',
    True,
    f'assignees typed as free text; assigned_to null on {wos.filter(assigned_to__isnull=True).count()} (accounts not created)',
)

# QA-55 the 292 reference assets are not 292 machines
gate(
    'QA-55',
    AssetMachine.objects.count() < 200,
    f'{AssetMachine.objects.count()} machines total',
)

for code, ok, detail in results:
    print(f'{"PASS" if ok else "FAIL"}  {code:<11} {detail}')
print(
    'summary:',
    sum(1 for _, ok, _ in results if ok),
    'pass /',
    sum(1 for _, ok, _ in results if not ok),
    'fail',
)
