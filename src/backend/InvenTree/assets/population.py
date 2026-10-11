"""Plan and apply a UI population workbook against the asset registry.

Two entry points, deliberately separate:

* ``plan_population`` reads the database and returns what *would* change.
  It never writes, so it is safe against any database, including the
  Azure one the handover says to treat as owner-approved-only.
* ``apply_population`` performs the plan inside one transaction. With
  ``dry_run=True`` every write is rolled back after the report is built,
  which is how the operator sees the real outcome (generated pump names,
  profile validation, transfer versions) before anything is kept.

Every write goes through the existing service functions - ``save_location``,
``transfer_machines``, ``register_station``, ``ensure_pump``,
``rename_station`` - so the invariants those functions enforce (scope,
history rows, immutable identity, generated pump labels) hold for imported
data exactly as they do for data entered through the UI.

Components and installed-part links depend on ``Part`` rows that the
catalogue stage creates. Without a catalogue sheet this module reports which
parts are missing; it does not invent them. The inventory and maintenance
stages that follow the plant (warehouse bins, companies, offers, stock,
history, staged work orders) live in ``population_operations``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from rest_framework.exceptions import ValidationError as APIValidationError
from tasks.scope import ScopeError, scope_for_actor

from users.permissions import check_user_role

from .locations import MAX_TRANSFER, save_location, transfer_machines
from .machine_profile import validate_machine_profile
from .models import AssetLocation, AssetMachine, Client
from .population_operations import OperationsError, apply_operations, plan_operations
from .population_workbook import CATEGORY_SEPARATOR, MachineRecord, PopulationWorkbook
from .registry import ensure_pump, pump_display_name, register_station, rename_station

MACHINE_FIELDS = ('description', 'manufacturer', 'model', 'serial', 'active')
LOCATION_FIELDS = ('name', 'kind', 'timezone', 'description', 'archived')
TRANSFER_NAMESPACE = uuid5(NAMESPACE_URL, 'aimms:population:transfer')


class PopulationError(Exception):
    """The workbook conflicts with the database; nothing was written."""


@dataclass
class Step:
    """One planned change, rendered in the report."""

    kind: str
    key: str
    action: str
    detail: str = ''


@dataclass
class Plan:
    """What a run will do, with blocking conflicts listed separately."""

    client_code: str
    client_action: str
    steps: list[Step] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: dict = field(default_factory=dict)
    manual: dict = field(default_factory=dict)

    def add(self, kind: str, key: str, action: str, detail: str = '') -> None:
        """Record one step."""
        self.steps.append(Step(kind, key, action, detail))

    def counts(self) -> dict:
        """Steps grouped as ``{kind: {action: n}}`` for the summary line."""
        out: dict = {}
        for step in self.steps:
            out.setdefault(step.kind, {}).setdefault(step.action, 0)
            out[step.kind][step.action] += 1
        return out

    def as_dict(self) -> dict:
        """JSON-serialisable form."""
        return {
            'client_code': self.client_code,
            'client_action': self.client_action,
            'counts': self.counts(),
            'steps': [asdict(step) for step in self.steps],
            'conflicts': list(self.conflicts),
            'warnings': list(self.warnings),
            'blocked': dict(self.blocked),
            'manual': dict(self.manual),
        }


# -- shared helpers ----------------------------------------------------------


def profile_for(book: PopulationWorkbook, record: MachineRecord) -> dict:
    """The profile the workbook declares for a machine, validated.

    A ``Machine Profiles`` row wins. Otherwise the ``criticality`` and
    ``maintenance_strategy`` columns on the machine row give a minimal profile
    so the summary chips still render. An empty result means "leave the
    stored profile alone".
    """
    declared = book.profiles.get(record.key)
    if declared is not None:
        candidate = dict(declared.profile)
    else:
        candidate = {}
        if record.criticality:
            candidate['criticality'] = record.criticality
        if record.maintenance_strategy:
            candidate['maintenance_strategy'] = record.maintenance_strategy
    return validate_machine_profile(candidate)


def _machine_changes(machine: AssetMachine, record: MachineRecord, profile: dict):
    """Field-level diff between a stored machine and its workbook row."""
    changes = {}
    for name in MACHINE_FIELDS:
        wanted = getattr(record, name)
        if getattr(machine, name) != wanted:
            changes[name] = wanted
    if record.legacy_location and machine.location != record.legacy_location:
        changes['location'] = record.legacy_location
    if profile and machine.profile != profile:
        changes['profile'] = profile
    return changes


def _location_changes(node: AssetLocation, record, parent_pk):
    changes = {}
    for name in LOCATION_FIELDS:
        if getattr(node, name) != getattr(record, name):
            changes[name] = getattr(record, name)
    if node.parent_id != parent_pk:
        changes['parent'] = parent_pk
    return changes


def ensure_actor(actor, client: Client) -> None:
    """Fail early if the actor could not perform the writes the plan needs.

    ``save_location`` and ``transfer_machines`` check the ``work_order`` role
    and a positive client scope. When no scope resolver is configured the
    command grants an in-memory scope for this one client, which is the
    documented service-account path; with a resolver configured the grant
    must already exist (``manage.py grant_client_scope``).
    """
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise PopulationError('An authenticated actor is required.')
    if not getattr(actor, 'is_active', False):
        raise PopulationError('The actor account is inactive.')
    for permission in ('add', 'change'):
        if not check_user_role(actor, 'work_order', permission):
            raise PopulationError(
                f'Actor lacks the work_order.{permission} role needed for locations.'
            )
    try:
        granted = {
            s.client_id
            for s in scope_for_actor(actor)
            if s.client_id is not None and s.site_key is None
        }
    except ScopeError:
        granted = set()
    if client.pk in granted:
        return
    if getattr(settings, 'AIMMS_MAINTENANCE_SCOPE_RESOLVER', None) is None:
        actor.maintenance_scopes = [{'client_id': client.pk}]
        return
    raise PopulationError(
        f'Actor has no maintenance scope for client {client.code!r}; grant one '
        'with manage.py grant_client_scope first.'
    )


def _identity(namespace, entity_uuid, source_key) -> str:
    return f'{namespace}/{entity_uuid}/{source_key}'


def _find_station(book: PopulationWorkbook, station_pk: int | None):
    """The registered station this workbook describes, or ``None``.

    By default the station is matched on its source identity. ``station_pk``
    adopts an already-registered station instead - the case where the plant
    was registered from its telemetry source before the workbook existed and
    the workbook carries a provisional identity. The registered identity is
    immutable and wins; the planner records the difference as a warning.
    """
    if station_pk is None:
        if book.station is None:
            return None
        return AssetMachine.objects.filter(
            asset_type='pumphouse',
            source_namespace=book.station.source_namespace,
            source_entity_uuid=book.station.source_entity_uuid,
        ).first()
    if book.station is None:
        raise PopulationError('--station given but the workbook has no station row.')
    station = AssetMachine.objects.filter(pk=station_pk).first()
    if station is None:
        raise PopulationError(f'No machine with id {station_pk}.')
    if station.asset_type != 'pumphouse':
        raise PopulationError(
            f'Machine {station_pk} is a {station.asset_type}, not a registered station.'
        )
    return station


def _resolve_client(code: str, client_code: str | None, client_name: str | None):
    """The client to populate and the action the plan will take on it.

    ``client_code`` names an existing client to populate instead of the one
    in the workbook (for example a single-site deployment whose scope resolver
    only shows one client). It is never created implicitly.
    """
    override = client_code is not None
    code = client_code or code
    client = Client.objects.filter(code=code).first()
    if client is None:
        if override:
            raise PopulationError(
                f'Client {code!r} does not exist; --client-code must name an '
                'existing client.'
            )
        if not client_name:
            raise PopulationError(
                f'Client {code!r} does not exist; pass --client-name to create it.'
            )
        return None, code, 'create'
    if not client.active:
        raise PopulationError(f'Client {code!r} is inactive.')
    return client, code, 'exists'


# -- planning (read-only) ----------------------------------------------------


def plan_population(
    book: PopulationWorkbook,
    *,
    client_name: str | None = None,
    station_uuid: UUID | None = None,
    client_code: str | None = None,
    station_pk: int | None = None,
):
    """Diff the workbook against the database without writing anything.

    ``station_uuid`` reserves the station's public UUID (pump UUIDs derive
    from it). It overrides a ``public_uuid`` column and must agree with an
    already-registered station. ``client_code`` populates an existing client
    other than the workbook's; ``station_pk`` adopts an already-registered
    station (see ``_find_station``).
    """
    client, code, client_action = _resolve_client(
        book.client_codes[0], client_code, client_name
    )
    plan = Plan(client_code=code, client_action=client_action)

    # Locations ---------------------------------------------------------------
    existing_locations = (
        {node.code: node for node in AssetLocation.objects.filter(client=client)}
        if client
        else {}
    )
    for record in book.locations:
        node = existing_locations.get(record.code)
        parent = (
            existing_locations.get(record.parent_code) if record.parent_code else None
        )
        parent_pk = parent.pk if parent else None
        if node is None:
            plan.add('location', record.code, 'create', record.name)
            continue
        if record.parent_code and parent is None:
            # Parent is created earlier in this same run; pk unknown until then.
            changes = _location_changes(node, record, node.parent_id)
            if node.parent_id is not None:
                changes['parent'] = record.parent_code
        else:
            changes = _location_changes(node, record, parent_pk)
        if changes:
            plan.add('location', record.code, 'update', ', '.join(sorted(changes)))
        else:
            plan.add('location', record.code, 'unchanged')

    # Station and pumps ---------------------------------------------------------
    station_record = book.station
    station = _find_station(book, station_pk)
    if station_record is not None:
        reserved = station_uuid or station_record.public_uuid
        if station is None:
            clash = AssetMachine.objects.filter(name=station_record.name).first()
            if clash is not None:
                plan.conflicts.append(
                    f'Station name {station_record.name!r} is already used by '
                    f'machine {clash.pk} with a different source identity.'
                )
            if reserved and AssetMachine.objects.filter(uuid=reserved).exists():
                plan.conflicts.append(
                    f'Reserved station UUID {reserved} already belongs to another machine.'
                )
            plan.add(
                'station',
                station_record.key,
                'register',
                f'{station_record.name} uuid={reserved or "generated"}',
            )
        else:
            if reserved and station.uuid != reserved:
                plan.conflicts.append(
                    f'Station {station.pk} has UUID {station.uuid}; workbook reserves '
                    f'{reserved}. Public UUIDs are immutable.'
                )
            if client and station.client_id != client.pk:
                plan.conflicts.append(
                    f'Station {station.pk} belongs to another client.'
                )
            registered = _identity(
                station.source_namespace, station.source_entity_uuid, station.source_key
            )
            declared = _identity(
                station_record.source_namespace,
                station_record.source_entity_uuid,
                station_record.source_key,
            )
            if station_pk is not None:
                if registered != declared:
                    plan.warnings.append(
                        f'Station {station.pk} keeps its registered identity '
                        f'{registered}; the workbook identity {declared} is not '
                        'applied (identity is immutable).'
                    )
            elif station.source_key != station_record.source_key:
                plan.conflicts.append(
                    f'Station {station.pk} source_key {station.source_key!r} differs '
                    f'from workbook {station_record.source_key!r}; identity is immutable.'
                )
            if station.name != station_record.name:
                clash = (
                    AssetMachine.objects
                    .filter(name=station_record.name)
                    .exclude(pk=station.pk)
                    .first()
                )
                if clash is not None:
                    plan.conflicts.append(
                        f'Cannot rename station to {station_record.name!r}: machine '
                        f'{clash.pk} already uses that name.'
                    )
                plan.add('station', station_record.key, 'rename', station_record.name)
            else:
                plan.add(
                    'station',
                    station_record.key,
                    'adopt' if station_pk is not None else 'exists',
                )
        _plan_profile(plan, book, station_record, station)

    pumps_by_key = (
        {p.source_key: p for p in station.children.filter(asset_type='pump')}
        if station
        else {}
    )
    for record in book.pumps:
        pump = pumps_by_key.get(record.source_key)
        expected = (
            f'{station_record.name[:170]} / Pump {int(record.source_key[1:]):02d}'
        )
        if record.name != expected:
            plan.warnings.append(
                f'Pump {record.key}: workbook name {record.name!r} differs from the '
                f'generated label {expected!r}; the generated label is kept.'
            )
        if pump is None:
            plan.add('pump', record.key, 'create', record.source_key)
        else:
            plan.add('pump', record.key, 'exists', record.source_key)
        _plan_profile(plan, book, record, pump)

    # Equipment -----------------------------------------------------------------
    for record in book.equipment:
        machine = AssetMachine.objects.filter(name=record.name).first()
        if machine is None:
            plan.add('equipment', record.key, 'create', record.name)
        elif machine.asset_type != 'equipment':
            plan.conflicts.append(
                f'{record.key}: name {record.name!r} belongs to a registered '
                f'{machine.asset_type} (machine {machine.pk}).'
            )
        elif client and machine.client_id not in (None, client.pk):
            plan.conflicts.append(
                f'{record.key}: machine {machine.pk} belongs to another client.'
            )
        else:
            plan.add('equipment', record.key, 'exists', record.name)
        _plan_profile(plan, book, record, machine)

    # Placement -----------------------------------------------------------------
    for record in book.machines:
        if record.location_code is None:
            continue
        machine = _resolve_existing(record, station)
        current = (
            machine.physical_location.code
            if machine is not None and machine.physical_location_id
            else None
        )
        if current == record.location_code:
            plan.add('placement', record.key, 'unchanged', record.location_code)
        else:
            plan.add('placement', record.key, 'move', record.location_code)

    # Parts-dependent stages ------------------------------------------------------
    machines = {
        record.key: _resolve_existing(record, station) for record in book.machines
    }
    _plan_parts(plan, book, machines)
    plan_operations(plan, book, machines)
    return plan


def _plan_profile(plan: Plan, book, record, machine):
    try:
        profile = profile_for(book, record)
    except ValidationError as exc:
        plan.conflicts.append(
            f'{record.key}: profile invalid: {"; ".join(exc.messages)}'
        )
        return
    if machine is None:
        if profile:
            plan.add('profile', record.key, 'set')
        return
    changes = _machine_changes(machine, record, profile)
    if changes:
        plan.add('details', record.key, 'update', ', '.join(sorted(changes)))
    else:
        plan.add('details', record.key, 'unchanged')


def _resolve_existing(record: MachineRecord, station):
    if record.asset_type == 'pumphouse':
        return station
    if record.asset_type == 'pump':
        if station is None:
            return None
        return station.children.filter(
            asset_type='pump', source_key=record.source_key
        ).first()
    return AssetMachine.objects.filter(name=record.name).first()


def _plan_blocked(plan: Plan, book: PopulationWorkbook):
    """Without a catalogue sheet, components can only use parts already present."""
    from part.models import Part

    wanted = book.referenced_ipns
    if not wanted:
        return
    present = set(Part.objects.filter(IPN__in=wanted).values_list('IPN', flat=True))
    missing = sorted(wanted - present)
    plan.blocked = {
        'components': len(book.components),
        'part_links': len(book.part_links),
        'parts_required': len(wanted),
        'parts_present': len(present),
        'parts_missing': missing[:50],
        'parts_missing_count': len(missing),
        'note': (
            'No Parts Catalogue sheet; component occurrences and installed-part '
            'links need the referenced Parts to exist first.'
        ),
    }


PART_FIELDS = (
    'name',
    'description',
    'virtual',
    'purchaseable',
    'trackable',
    'consumable',
    'units',
    'minimum_stock',
)


def _existing_parts(ipns):
    """Map IPN (case-folded) to its Part; more than one match is a conflict."""
    from part.models import Part

    found: dict[str, list] = {}
    for part in Part.objects.filter(IPN__in=ipns).select_related('category'):
        found.setdefault(part.IPN.casefold(), []).append(part)
    return found


def _pathstring(category: tuple[str, ...]) -> str:
    """InvenTree's ``PathStringMixin`` joins tree names with a bare slash."""
    return '/'.join(category)


def _part_changes(part, record, category_path: str):
    changes = {}
    for name in PART_FIELDS:
        wanted = getattr(record, name)
        current = getattr(part, name)
        if name == 'minimum_stock':
            current = int(current or 0)
        elif name == 'units':
            current = current or ''
        if current != wanted:
            changes[name] = wanted
    if (part.category.pathstring if part.category_id else '') != category_path:
        changes['category'] = category_path
    if record.link and (part.link or '') != record.link:
        changes['link'] = record.link
    if not part.active:
        changes['active'] = True
    return changes


def _plan_parts(plan: Plan, book: PopulationWorkbook, machines: dict):
    """Categories, parts, component occurrences and installed-part links."""
    from part.models import PartCategory

    from .models import AssetComponent, MachinePart

    if not book.has_parts_sheet:
        _plan_blocked(plan, book)
        return

    nodes = set()
    for record in book.parts:
        for depth in range(1, len(record.category) + 1):
            nodes.add(record.category[:depth])
    existing_paths = set(
        PartCategory.objects.filter(
            pathstring__in=[_pathstring(node) for node in nodes]
        ).values_list('pathstring', flat=True)
    )
    for node in sorted(nodes):
        path = _pathstring(node)
        plan.add('category', path, 'exists' if path in existing_paths else 'create')

    existing = _existing_parts([record.ipn for record in book.parts])
    parts_by_ipn = {}
    for record in book.parts:
        matches = existing.get(record.ipn.casefold(), [])
        if len(matches) > 1:
            plan.conflicts.append(
                f'IPN {record.ipn} matches {len(matches)} parts; resolve the duplicate.'
            )
            continue
        if not matches:
            plan.add('part', record.ipn, 'create', record.name)
            continue
        part = matches[0]
        parts_by_ipn[record.ipn.casefold()] = part
        if part.category_id and part.category.structural:
            plan.conflicts.append(
                f'Part {record.ipn} sits in structural category {part.category.pathstring}.'
            )
        changes = _part_changes(part, record, _pathstring(record.category))
        if changes:
            plan.add('part', record.ipn, 'update', ', '.join(sorted(changes)))
        else:
            plan.add('part', record.ipn, 'unchanged')

    owner_pks = [m.pk for m in machines.values() if m is not None]
    components = {
        (c.machine_id, c.code): c
        for c in AssetComponent.objects.filter(machine_id__in=owner_pks).select_related(
            'part'
        )
    }
    for record in book.components:
        owner = machines.get(record.machine_key)
        if owner is None:
            plan.add('component', f'{record.machine_key}:{record.code}', 'create')
            continue
        current = components.get((owner.pk, record.code))
        if current is None:
            plan.add('component', f'{record.machine_key}:{record.code}', 'create')
        elif (current.part.IPN or '').casefold() != record.part_ipn.casefold():
            plan.conflicts.append(
                f'Component {record.code} on {record.machine_key} is bound to part '
                f'{current.part.IPN!r}, workbook says {record.part_ipn!r}; review it.'
            )
        elif current.name != record.name or current.review_note != record.review_note:
            plan.add('component', f'{record.machine_key}:{record.code}', 'update')
        else:
            plan.add('component', f'{record.machine_key}:{record.code}', 'unchanged')

    links = {
        (link.machine_id, (link.part.IPN or '').casefold()): link
        for link in MachinePart.objects.filter(machine_id__in=owner_pks).select_related(
            'part'
        )
    }
    for record in book.part_links:
        owner = machines.get(record.machine_key)
        current = links.get((owner.pk, record.part_ipn.casefold())) if owner else None
        key = f'{record.machine_key}:{record.part_ipn}'
        if current is None:
            plan.add('part_link', key, 'create', f'x{record.quantity}')
        elif current.quantity != record.quantity or current.notes != record.notes:
            plan.add('part_link', key, 'update', f'x{record.quantity}')
        else:
            plan.add('part_link', key, 'unchanged')


# -- applying ----------------------------------------------------------------


@transaction.atomic
def apply_population(
    book: PopulationWorkbook,
    actor,
    *,
    dry_run: bool = False,
    client_name: str | None = None,
    station_uuid: UUID | None = None,
    client_code: str | None = None,
    station_pk: int | None = None,
) -> dict:
    """Apply the workbook; roll everything back when ``dry_run`` is set."""
    plan = plan_population(
        book,
        client_name=client_name,
        station_uuid=station_uuid,
        client_code=client_code,
        station_pk=station_pk,
    )
    if plan.conflicts:
        raise PopulationError(
            'Refusing to apply with conflicts:\n  - ' + '\n  - '.join(plan.conflicts)
        )
    code = plan.client_code
    if plan.client_action == 'create':
        client = Client.objects.create(code=code, name=client_name)
    else:
        client = Client.objects.select_for_update().get(code=code)
    ensure_actor(actor, client)

    report: dict = {
        'dry_run': dry_run,
        'workbook_sha256': book.sha256,
        'client': {'code': client.code, 'pk': client.pk, 'action': plan.client_action},
        'locations': {'created': 0, 'updated': 0, 'unchanged': 0},
        'station': None,
        'pumps': {'created': 0, 'existing': 0},
        'equipment': {'created': 0, 'existing': 0},
        'details': {'updated': 0, 'unchanged': 0},
        'placements': {'moved': 0, 'unchanged': 0, 'transfers': 0},
        'warnings': list(plan.warnings),
        'blocked': plan.blocked,
    }

    # Locations ---------------------------------------------------------------
    nodes: dict[str, AssetLocation] = {
        node.code: node for node in AssetLocation.objects.filter(client=client)
    }
    for record in book.locations:
        parent_pk = nodes[record.parent_code].pk if record.parent_code else None
        values = {
            'name': record.name,
            'code': record.code,
            'kind': record.kind,
            'parent': parent_pk,
            'timezone': record.timezone,
            'description': record.description,
            'archived': record.archived,
            'reason': record.reason,
        }
        node = nodes.get(record.code)
        try:
            if node is None:
                node = save_location(actor, {**values, 'client': client.pk})
                report['locations']['created'] += 1
            elif _location_changes(node, record, parent_pk):
                node = save_location(
                    actor, {**values, 'expected_version': node.version}, pk=node.pk
                )
                report['locations']['updated'] += 1
            else:
                report['locations']['unchanged'] += 1
        except APIValidationError as exc:
            raise PopulationError(
                f'Location {record.code} (row {record.row}): {exc.detail}'
            ) from exc
        nodes[record.code] = node

    # Station and pumps ---------------------------------------------------------
    machines: dict[str, AssetMachine] = {}
    station = None
    station_record = book.station
    if station_record is not None:
        try:
            if station_pk is not None:
                # Planned and validated above; lock the row for this transaction.
                station = AssetMachine.objects.select_for_update().get(pk=station_pk)
            else:
                station = register_station(
                    client=client,
                    name=station_record.name,
                    source_namespace=station_record.source_namespace,
                    source_entity_uuid=station_record.source_entity_uuid,
                    source_key=station_record.source_key,
                    public_uuid=station_uuid or station_record.public_uuid,
                )
            if station.name != station_record.name:
                rename_station(station, station_record.name)
                station.refresh_from_db()
        except ValidationError as exc:
            raise PopulationError(
                f'Station {station_record.key}: {"; ".join(exc.messages)}'
            ) from exc
        machines[station_record.key] = station
        report['station'] = {
            'pk': station.pk,
            'uuid': str(station.uuid),
            'name': station.name,
            'source_key': station.source_key,
            'adopted': station_pk is not None,
        }
        created_before = set(
            station.children.filter(asset_type='pump').values_list('pk', flat=True)
        )
        for record in book.pumps:
            pump = ensure_pump(station, record.source_key)
            machines[record.key] = pump
            if pump.pk in created_before:
                report['pumps']['existing'] += 1
            else:
                report['pumps']['created'] += 1
            label = pump_display_name(station, record.source_key)
            if pump.name != label:
                pump.name = label
                pump.full_clean()
                pump.save(update_fields=['name'])

    # Equipment -----------------------------------------------------------------
    for record in book.equipment:
        machine = AssetMachine.objects.filter(name=record.name).first()
        if machine is None:
            machine = AssetMachine(
                name=record.name,
                asset_type='equipment',
                client=client,
                description=record.description,
                manufacturer=record.manufacturer,
                model=record.model,
                serial=record.serial,
                active=record.active,
                location=record.legacy_location,
            )
            try:
                machine.full_clean()
            except ValidationError as exc:
                raise PopulationError(
                    f'{record.key} (row {record.row}): {"; ".join(exc.messages)}'
                ) from exc
            machine.save()
            report['equipment']['created'] += 1
        else:
            if machine.client_id is None:
                machine.client = client
                machine.save(update_fields=['client'])
            report['equipment']['existing'] += 1
        machines[record.key] = machine

    # Details and profiles --------------------------------------------------------
    for record in book.machines:
        machine = machines.get(record.key)
        if machine is None:
            continue
        profile = profile_for(book, record)
        changes = _machine_changes(machine, record, profile)
        if not changes:
            report['details']['unchanged'] += 1
            continue
        for name, value in changes.items():
            setattr(machine, name, value)
        try:
            machine.full_clean()
        except ValidationError as exc:
            raise PopulationError(
                f'{record.key} (row {record.row}): {"; ".join(exc.messages)}'
            ) from exc
        machine.save(update_fields=[*changes, 'updated_at'])
        report['details']['updated'] += 1

    # Placement -----------------------------------------------------------------
    by_destination: dict[str, list[AssetMachine]] = {}
    for record in book.machines:
        machine = machines.get(record.key)
        if machine is None or record.location_code is None:
            continue
        machine.refresh_from_db(fields=['physical_location', 'placement_version'])
        target = nodes[record.location_code]
        if machine.physical_location_id == target.pk:
            report['placements']['unchanged'] += 1
            continue
        by_destination.setdefault(record.location_code, []).append(machine)
    for location_code, batch in by_destination.items():
        destination = nodes[location_code]
        for start in range(0, len(batch), MAX_TRANSFER):
            chunk = sorted(batch[start : start + MAX_TRANSFER], key=lambda m: m.pk)
            fingerprint = ','.join(f'{m.pk}:{m.placement_version}' for m in chunk)
            key = uuid5(
                TRANSFER_NAMESPACE, f'{book.sha256}:{location_code}:{fingerprint}'
            )
            try:
                result = transfer_machines(
                    actor,
                    {
                        'machines': [
                            {
                                'machine_id': m.pk,
                                'expected_placement_version': m.placement_version,
                            }
                            for m in chunk
                        ],
                        'destination_location_id': destination.pk,
                        'reason': f'Workbook population {book.sha256[:12]}',
                        'idempotency_key': key,
                    },
                )
            except APIValidationError as exc:
                raise PopulationError(
                    f'Placement into {location_code}: {exc.detail}'
                ) from exc
            report['placements']['transfers'] += 1
            report['placements']['moved'] += len(result['machines'])

    # Parts, components, installed-part links ---------------------------------------
    parts_report, parts = _apply_parts(book, machines)
    report.update(parts_report)

    # Warehouse, companies, offers, stock, history, work orders ----------------------
    try:
        operations = apply_operations(
            book, actor, machines, parts, f'Workbook population {book.sha256[:12]}'
        )
    except OperationsError as exc:
        raise PopulationError(str(exc)) from exc
    report['warnings'].extend(operations.pop('warnings'))
    report.update(operations)

    report['machines'] = {
        key: {'pk': machine.pk, 'name': machine.name, 'asset_type': machine.asset_type}
        for key, machine in machines.items()
    }
    if dry_run:
        transaction.set_rollback(True)
    return report


def _ensure_category(path: tuple[str, ...], cache: dict):
    """Create the category chain for ``path``; cache by joined path."""
    from part.models import PartCategory

    parent = None
    created = 0
    for depth in range(1, len(path) + 1):
        key = CATEGORY_SEPARATOR.join(path[:depth])
        node = cache.get(key)
        if node is None:
            node = PartCategory.objects.filter(
                name=path[depth - 1], parent=parent
            ).first()
            if node is None:
                node = PartCategory(name=path[depth - 1], parent=parent)
                node.full_clean()
                node.save()
                created += 1
            cache[key] = node
        parent = node
    return parent, created


def _apply_parts(book: PopulationWorkbook, machines: dict) -> tuple[dict, dict]:
    """Upsert the catalogue, then bind components and installed-part links.

    Component occurrences stay ``draft`` and carry the workbook's engineering
    status in ``review_note``; nothing here marks a part as verified installed
    or an approved spare (QA-14, QA-19). Returns the counts and the
    case-folded IPN -> ``Part`` map for the stages that follow.
    """
    from part.models import Part

    from .models import AssetComponent, MachinePart

    out = {
        'categories': {'created': 0, 'existing': 0},
        'parts': {'created': 0, 'updated': 0, 'unchanged': 0},
        'components': {'created': 0, 'updated': 0, 'unchanged': 0, 'skipped': 0},
        'part_links': {'created': 0, 'updated': 0, 'unchanged': 0, 'skipped': 0},
    }
    parts: dict[str, Part] = {}
    if not book.has_parts_sheet:
        out['components']['skipped'] = len(book.components)
        out['part_links']['skipped'] = len(book.part_links)
        return out, parts

    categories: dict = {}
    existing = _existing_parts([record.ipn for record in book.parts])
    for record in book.parts:
        category, created = _ensure_category(record.category, categories)
        out['categories']['created'] += created
        matches = existing.get(record.ipn.casefold(), [])
        if len(matches) > 1:
            raise PopulationError(f'IPN {record.ipn} matches more than one part.')
        path = _pathstring(record.category)
        try:
            if not matches:
                part = Part(
                    name=record.name,
                    IPN=record.ipn,
                    description=record.description,
                    category=category,
                    virtual=record.virtual,
                    purchaseable=record.purchaseable,
                    trackable=record.trackable,
                    consumable=record.consumable,
                    units=record.units,
                    minimum_stock=record.minimum_stock,
                    link=record.link or None,
                    keywords=record.manufacturer[:250],
                    component=True,
                    active=True,
                )
                part.full_clean()
                part.save()
                out['parts']['created'] += 1
            else:
                part = matches[0]
                changes = _part_changes(part, record, path)
                if changes:
                    if 'category' in changes:
                        part.category = category
                    for name, value in changes.items():
                        if name != 'category':
                            setattr(part, name, value)
                    part.full_clean()
                    part.save()
                    out['parts']['updated'] += 1
                else:
                    out['parts']['unchanged'] += 1
        except ValidationError as exc:
            raise PopulationError(
                f'Part {record.ipn} (row {record.row}): {"; ".join(exc.messages)}'
            ) from exc
        parts[record.ipn.casefold()] = part
    out['categories']['existing'] = len(categories) - out['categories']['created']

    provenance = f'Workbook population {book.sha256[:12]}'[:250]
    for record in book.components:
        owner = machines.get(record.machine_key)
        part = parts.get(record.part_ipn.casefold())
        if owner is None or part is None:
            out['components']['skipped'] += 1
            continue
        component, created = AssetComponent.objects.get_or_create(
            machine=owner,
            code=record.code,
            defaults={
                'part': part,
                'name': record.name,
                'provenance': provenance,
                'review_note': record.review_note,
            },
        )
        if created:
            out['components']['created'] += 1
            continue
        if component.part_id != part.pk:
            raise PopulationError(
                f'Component {record.code} on {record.machine_key} is bound to another part.'
            )
        if component.name != record.name or component.review_note != record.review_note:
            component.name = record.name
            component.review_note = record.review_note
            component.save(update_fields=['name', 'review_note'])
            out['components']['updated'] += 1
        else:
            out['components']['unchanged'] += 1

    for record in book.part_links:
        owner = machines.get(record.machine_key)
        part = parts.get(record.part_ipn.casefold())
        if owner is None or part is None:
            out['part_links']['skipped'] += 1
            continue
        link, created = MachinePart.objects.get_or_create(
            machine=owner,
            part=part,
            defaults={'quantity': record.quantity, 'notes': record.notes},
        )
        if created:
            out['part_links']['created'] += 1
        elif link.quantity != record.quantity or link.notes != record.notes:
            link.quantity = record.quantity
            link.notes = record.notes
            link.save(update_fields=['quantity', 'notes'])
            out['part_links']['updated'] += 1
        else:
            out['part_links']['unchanged'] += 1
    return out, parts


__all__ = [
    'Plan',
    'PopulationError',
    'Step',
    'apply_population',
    'ensure_actor',
    'plan_population',
    'profile_for',
]
