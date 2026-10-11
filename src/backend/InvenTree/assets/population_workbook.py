"""Parse a UI population workbook into validated, Django-free records.

The workbook is the Cedar Creek "AIMMS UI population master" layout: one
sheet per staging table, first row is the header. Only the sheets a
population run needs are read; everything else in the file is ignored.

Nothing here touches the database. The parser's job is to turn a
spreadsheet into typed records and to refuse anything that could not be
applied safely: duplicate codes, dangling parents, a station with no
identity, a pump slot whose key the registry would reject. Catching those
here means the planner can assume a coherent input and spend its checks
on the database side instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

MAX_WORKBOOK_BYTES = 64 * 1024 * 1024
MAX_ROWS_PER_SHEET = 10_000

SHEET_LOCATIONS = 'Facility Locations'
SHEET_MACHINES = 'Machine Records'
SHEET_PROFILES = 'Machine Profiles'
SHEET_COMPONENTS = 'Component Occurrences'
SHEET_PART_LINKS = 'Machine Part Links'

REQUIRED_SHEETS = (SHEET_LOCATIONS, SHEET_MACHINES)
OPTIONAL_SHEETS = (SHEET_PROFILES, SHEET_COMPONENTS, SHEET_PART_LINKS)

LOCATION_COLUMNS = (
    'code',
    'name',
    'kind',
    'parent_code',
    'timezone',
    'description',
    'client_code',
    'archived',
    'reason_for_change',
)
MACHINE_COLUMNS = (
    'machine_key',
    'name',
    'asset_type',
    'asset_location_code',
    'legacy_location_label',
    'manufacturer',
    'model',
    'serial',
    'description',
    'active',
    'client_code',
    'source_key',
    'source_namespace',
    'source_entity_uuid',
    'criticality',
    'maintenance_strategy',
    'entry_status',
)
PROFILE_COLUMNS = (
    'machine_key',
    'criticality',
    'maintenance_strategy',
    'components_json',
    'energy_sources_json',
    'fault_codes_json',
    'approved_spares_json',
)
COMPONENT_COLUMNS = (
    'owner_machine_key',
    'occurrence_code',
    'display_name',
    'catalogue_part_ipn',
    'quantity',
)
PART_LINK_COLUMNS = ('machine_key', 'part_ipn', 'quantity', 'notes')
MACHINE_OPTIONAL_COLUMNS = ('public_uuid',)

LOCATION_KINDS = frozenset({
    'site',
    'facility',
    'building',
    'area',
    'line',
    'cell',
    'room',
    'other',
})
ASSET_TYPES = frozenset({'equipment', 'pumphouse', 'pump'})
PUMP_KEY = re.compile(r'P[1-9][0-9]{0,3}')
SLUG = re.compile(r'^[-a-zA-Z0-9_]+$')


class WorkbookError(ValueError):
    """The workbook cannot be applied; the message names the sheet and row."""


@dataclass(frozen=True)
class LocationRecord:
    """One physical-location node to upsert."""

    row: int
    code: str
    name: str
    kind: str
    parent_code: str | None
    timezone: str
    description: str
    client_code: str
    archived: bool
    reason: str


@dataclass(frozen=True)
class MachineRecord:
    """One machine row; identity fields are only set for registry types."""

    row: int
    key: str
    name: str
    asset_type: str
    location_code: str | None
    legacy_location: str
    manufacturer: str
    model: str
    serial: str
    description: str
    active: bool
    client_code: str
    source_key: str
    source_namespace: str
    source_entity_uuid: UUID | None
    criticality: str
    maintenance_strategy: str
    entry_status: str
    public_uuid: UUID | None = None


@dataclass(frozen=True)
class ProfileRecord:
    """Knowledge profile for one machine, already JSON-decoded."""

    row: int
    machine_key: str
    profile: dict


@dataclass(frozen=True)
class ComponentRecord:
    """A component occurrence to place under a machine once its Part exists."""

    row: int
    machine_key: str
    code: str
    name: str
    part_ipn: str
    quantity: int


@dataclass(frozen=True)
class PartLinkRecord:
    """An installed-part quantity to record once its Part exists."""

    row: int
    machine_key: str
    part_ipn: str
    quantity: int
    notes: str


@dataclass
class PopulationWorkbook:
    """Everything a population run needs, in dependency order."""

    path: str
    sha256: str
    client_codes: tuple[str, ...]
    locations: list[LocationRecord] = field(default_factory=list)
    machines: list[MachineRecord] = field(default_factory=list)
    profiles: dict[str, ProfileRecord] = field(default_factory=dict)
    components: list[ComponentRecord] = field(default_factory=list)
    part_links: list[PartLinkRecord] = field(default_factory=list)

    @property
    def station(self) -> MachineRecord | None:
        """The single registered station, if the workbook has one."""
        return next((m for m in self.machines if m.asset_type == 'pumphouse'), None)

    @property
    def pumps(self) -> list[MachineRecord]:
        """Pump slots in source-key order."""
        return sorted(
            (m for m in self.machines if m.asset_type == 'pump'),
            key=lambda m: int(m.source_key[1:]),
        )

    @property
    def equipment(self) -> list[MachineRecord]:
        """Plain equipment rows in sheet order."""
        return [m for m in self.machines if m.asset_type == 'equipment']

    def summary(self) -> dict:
        """Counts the operator sees before anything is planned."""
        return {
            'workbook': self.path,
            'sha256': self.sha256,
            'client_codes': list(self.client_codes),
            'locations': len(self.locations),
            'station': self.station.name if self.station else None,
            'pumps': len(self.pumps),
            'equipment': len(self.equipment),
            'profiles': len(self.profiles),
            'components': len(self.components),
            'part_links': len(self.part_links),
        }


# -- cell coercion -----------------------------------------------------------


def _text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _bool(value, *, sheet: str, row: int, column: str) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in ('true', 'yes', '1', 'y'):
        return True
    if text in ('false', 'no', '0', 'n', ''):
        return False
    raise WorkbookError(
        f'{sheet} row {row}: {column} must be true/false, got {value!r}'
    )


def _int(value, *, sheet: str, row: int, column: str, minimum: int = 0) -> int:
    text = _text(value)
    if not text:
        return minimum
    try:
        number = int(float(text))
    except ValueError as exc:
        raise WorkbookError(
            f'{sheet} row {row}: {column} must be an integer, got {value!r}'
        ) from exc
    if number < minimum:
        raise WorkbookError(f'{sheet} row {row}: {column} must be >= {minimum}')
    return number


def _json_list(value, *, sheet: str, row: int, column: str) -> list:
    text = _text(value)
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkbookError(f'{sheet} row {row}: {column} is not valid JSON') from exc
    if not isinstance(decoded, list):
        raise WorkbookError(f'{sheet} row {row}: {column} must be a JSON list')
    return decoded


# -- sheet access ------------------------------------------------------------


def _rows(
    workbook, sheet: str, columns: tuple[str, ...], optional: tuple[str, ...] = ()
):
    """Yield (row_number, {column: value}) with the header validated.

    ``optional`` columns are read when present and yield ``None`` otherwise.
    """
    if sheet not in workbook.sheetnames:
        raise WorkbookError(f'Missing sheet: {sheet}')
    ws = workbook[sheet]
    iterator = ws.iter_rows(values_only=True)
    try:
        header = [_text(cell) for cell in next(iterator)]
    except StopIteration as exc:
        raise WorkbookError(f'{sheet}: sheet is empty') from exc
    missing = [column for column in columns if column not in header]
    if missing:
        raise WorkbookError(f'{sheet}: missing columns {", ".join(missing)}')
    index = {column: header.index(column) for column in columns}
    for column in optional:
        if column in header:
            index[column] = header.index(column)
    count = 0
    for offset, values in enumerate(iterator, start=2):
        if values is None or all(v is None or _text(v) == '' for v in values):
            continue
        count += 1
        if count > MAX_ROWS_PER_SHEET:
            raise WorkbookError(f'{sheet}: more than {MAX_ROWS_PER_SHEET} rows')
        padded = list(values) + [None] * (len(header) - len(values))
        cell = {column: padded[position] for column, position in index.items()}
        for column in optional:
            cell.setdefault(column, None)
        yield offset, cell


def _load_locations(workbook) -> list[LocationRecord]:
    sheet = SHEET_LOCATIONS
    records: list[LocationRecord] = []
    codes: dict[str, int] = {}
    for row, cell in _rows(workbook, sheet, LOCATION_COLUMNS):
        code = _text(cell['code'])
        if not code or not SLUG.match(code) or len(code) > 64:
            raise WorkbookError(f'{sheet} row {row}: code must be a slug <= 64 chars')
        key = code.lower()
        if key in codes:
            raise WorkbookError(
                f'{sheet} row {row}: code {code} duplicates row {codes[key]}'
            )
        codes[key] = row
        name = ' '.join(_text(cell['name']).split())
        if not name or len(name) > 255:
            raise WorkbookError(f'{sheet} row {row}: name is required (<= 255 chars)')
        kind = _text(cell['kind']).lower()
        if kind not in LOCATION_KINDS:
            raise WorkbookError(f'{sheet} row {row}: unknown kind {kind!r}')
        client = _text(cell['client_code'])
        if not client:
            raise WorkbookError(f'{sheet} row {row}: client_code is required')
        parent = _text(cell['parent_code']).lower() or None
        timezone = _text(cell['timezone'])
        if parent is None and not timezone:
            raise WorkbookError(f'{sheet} row {row}: a root location needs a timezone')
        description = _text(cell['description'])
        if len(description) > 10_000:
            raise WorkbookError(f'{sheet} row {row}: description exceeds 10000 chars')
        reason = _text(cell['reason_for_change']) or 'Workbook population'
        records.append(
            LocationRecord(
                row=row,
                code=key,
                name=name,
                kind=kind,
                parent_code=parent,
                timezone=timezone,
                description=description,
                client_code=client,
                archived=_bool(
                    cell['archived'], sheet=sheet, row=row, column='archived'
                ),
                reason=reason[:1000],
            )
        )
    # Parents must exist, be in the same workspace, and form a forest.
    by_code = {record.code: record for record in records}
    for record in records:
        if record.parent_code is None:
            continue
        parent = by_code.get(record.parent_code)
        if parent is None:
            raise WorkbookError(
                f'{sheet} row {record.row}: parent {record.parent_code} is not in the sheet'
            )
        if parent.client_code != record.client_code:
            raise WorkbookError(
                f'{sheet} row {record.row}: parent belongs to another client'
            )
        seen = {record.code}
        cursor = parent
        while cursor is not None:
            if cursor.code in seen:
                raise WorkbookError(
                    f'{sheet} row {record.row}: parent chain contains a cycle'
                )
            seen.add(cursor.code)
            if len(seen) > 32:
                raise WorkbookError(f'{sheet} row {record.row}: depth exceeds 32')
            cursor = by_code.get(cursor.parent_code) if cursor.parent_code else None
    return _topological(records)


def _topological(records: list[LocationRecord]) -> list[LocationRecord]:
    """Parents before children, preserving sheet order among siblings."""
    ordered: list[LocationRecord] = []
    placed: set[str] = set()
    pending = list(records)
    while pending:
        progress = False
        remaining = []
        for record in pending:
            if record.parent_code is None or record.parent_code in placed:
                ordered.append(record)
                placed.add(record.code)
                progress = True
            else:
                remaining.append(record)
        if not progress:
            raise WorkbookError(f'{SHEET_LOCATIONS}: unresolved parent chain')
        pending = remaining
    return ordered


def _load_machines(workbook, location_codes: set[str]) -> list[MachineRecord]:
    sheet = SHEET_MACHINES
    records: list[MachineRecord] = []
    keys: dict[str, int] = {}
    names: dict[str, int] = {}
    for row, cell in _rows(workbook, sheet, MACHINE_COLUMNS, MACHINE_OPTIONAL_COLUMNS):
        key = _text(cell['machine_key'])
        if not key:
            raise WorkbookError(f'{sheet} row {row}: machine_key is required')
        if key in keys:
            raise WorkbookError(
                f'{sheet} row {row}: machine_key {key} duplicates row {keys[key]}'
            )
        keys[key] = row
        name = ' '.join(_text(cell['name']).split())
        if not name or len(name) > 255:
            raise WorkbookError(f'{sheet} row {row}: name is required (<= 255 chars)')
        if name.casefold() in names:
            raise WorkbookError(
                f'{sheet} row {row}: name duplicates row {names[name.casefold()]}'
            )
        names[name.casefold()] = row
        asset_type = _text(cell['asset_type']).lower()
        if asset_type not in ASSET_TYPES:
            raise WorkbookError(f'{sheet} row {row}: unknown asset_type {asset_type!r}')
        client = _text(cell['client_code'])
        if not client:
            raise WorkbookError(f'{sheet} row {row}: client_code is required')
        location = _text(cell['asset_location_code']).lower() or None
        if location is not None and location not in location_codes:
            raise WorkbookError(
                f'{sheet} row {row}: asset_location_code {location} is not in '
                f'{SHEET_LOCATIONS}'
            )
        source_key = _text(cell['source_key'])
        namespace = _text(cell['source_namespace'])
        raw_uuid = _text(cell['source_entity_uuid'])
        entity_uuid = None
        if asset_type == 'pumphouse':
            if not source_key or not namespace or not raw_uuid:
                raise WorkbookError(
                    f'{sheet} row {row}: a station needs source_key, source_namespace '
                    'and source_entity_uuid'
                )
            if not SLUG.match(namespace) or len(namespace) > 64:
                raise WorkbookError(
                    f'{sheet} row {row}: source_namespace must be a slug'
                )
            try:
                entity_uuid = UUID(raw_uuid)
            except ValueError as exc:
                raise WorkbookError(
                    f'{sheet} row {row}: source_entity_uuid is not a UUID'
                ) from exc
        elif asset_type == 'pump':
            if not PUMP_KEY.fullmatch(source_key):
                raise WorkbookError(
                    f'{sheet} row {row}: pump source_key must be P1..P9999, got '
                    f'{source_key!r}'
                )
        for column in ('manufacturer', 'model', 'serial', 'legacy_location_label'):
            if len(_text(cell[column])) > 255:
                raise WorkbookError(f'{sheet} row {row}: {column} exceeds 255 chars')
        public_uuid = None
        raw_public = _text(cell.get('public_uuid'))
        if raw_public:
            if asset_type != 'pumphouse':
                raise WorkbookError(
                    f'{sheet} row {row}: public_uuid may only be reserved for the station'
                )
            try:
                public_uuid = UUID(raw_public)
            except ValueError as exc:
                raise WorkbookError(
                    f'{sheet} row {row}: public_uuid is not a UUID'
                ) from exc
        records.append(
            MachineRecord(
                row=row,
                key=key,
                name=name,
                asset_type=asset_type,
                location_code=location,
                legacy_location=_text(cell['legacy_location_label']),
                manufacturer=_text(cell['manufacturer']),
                model=_text(cell['model']),
                serial=_text(cell['serial']),
                description=_text(cell['description']),
                active=_bool(cell['active'], sheet=sheet, row=row, column='active'),
                client_code=client,
                source_key=source_key,
                source_namespace=namespace,
                source_entity_uuid=entity_uuid,
                criticality=_text(cell['criticality']).lower(),
                maintenance_strategy=_text(cell['maintenance_strategy']).lower(),
                entry_status=_text(cell['entry_status']),
                public_uuid=public_uuid,
            )
        )
    stations = [m for m in records if m.asset_type == 'pumphouse']
    pumps = [m for m in records if m.asset_type == 'pump']
    if len(stations) > 1:
        raise WorkbookError(
            f'{sheet}: more than one pumphouse row; one station per run'
        )
    if pumps and not stations:
        raise WorkbookError(
            f'{sheet}: pump rows need a pumphouse row in the same sheet'
        )
    pump_keys: dict[str, int] = {}
    for pump in pumps:
        if pump.source_key in pump_keys:
            raise WorkbookError(
                f'{sheet} row {pump.row}: pump source_key {pump.source_key} duplicates '
                f'row {pump_keys[pump.source_key]}'
            )
        pump_keys[pump.source_key] = pump.row
        if pump.client_code != stations[0].client_code:
            raise WorkbookError(
                f'{sheet} row {pump.row}: pump client differs from its station'
            )
    return records


def _load_profiles(workbook, machine_keys: set[str]) -> dict[str, ProfileRecord]:
    sheet = SHEET_PROFILES
    if sheet not in workbook.sheetnames:
        return {}
    profiles: dict[str, ProfileRecord] = {}
    for row, cell in _rows(workbook, sheet, PROFILE_COLUMNS):
        key = _text(cell['machine_key'])
        if key not in machine_keys:
            raise WorkbookError(
                f'{sheet} row {row}: machine_key {key} is not in {SHEET_MACHINES}'
            )
        if key in profiles:
            raise WorkbookError(
                f'{sheet} row {row}: machine_key {key} duplicates row '
                f'{profiles[key].row}'
            )
        profile: dict = {}
        criticality = _text(cell['criticality']).lower()
        strategy = _text(cell['maintenance_strategy']).lower()
        if criticality:
            profile['criticality'] = criticality
        if strategy:
            profile['maintenance_strategy'] = strategy
        for column, field_name in (
            ('components_json', 'components'),
            ('energy_sources_json', 'energy_sources'),
            ('fault_codes_json', 'fault_codes'),
            ('approved_spares_json', 'approved_spares'),
        ):
            profile[field_name] = _json_list(
                cell[column], sheet=sheet, row=row, column=column
            )
        profiles[key] = ProfileRecord(row=row, machine_key=key, profile=profile)
    return profiles


def _load_components(workbook, machine_keys: set[str]) -> list[ComponentRecord]:
    sheet = SHEET_COMPONENTS
    if sheet not in workbook.sheetnames:
        return []
    records: list[ComponentRecord] = []
    slots: dict[tuple[str, str], int] = {}
    for row, cell in _rows(workbook, sheet, COMPONENT_COLUMNS):
        key = _text(cell['owner_machine_key'])
        if key not in machine_keys:
            raise WorkbookError(
                f'{sheet} row {row}: owner_machine_key {key} is not in {SHEET_MACHINES}'
            )
        code = _text(cell['occurrence_code'])
        if not code or len(code) > 100:
            raise WorkbookError(f'{sheet} row {row}: occurrence_code required (<= 100)')
        if (key, code) in slots:
            raise WorkbookError(
                f'{sheet} row {row}: occurrence {code} on {key} duplicates row '
                f'{slots[key, code]}'
            )
        slots[key, code] = row
        ipn = _text(cell['catalogue_part_ipn'])
        if not ipn:
            raise WorkbookError(f'{sheet} row {row}: catalogue_part_ipn is required')
        records.append(
            ComponentRecord(
                row=row,
                machine_key=key,
                code=code,
                name=_text(cell['display_name'])[:255] or code,
                part_ipn=ipn,
                quantity=_int(
                    cell['quantity'], sheet=sheet, row=row, column='quantity', minimum=1
                ),
            )
        )
    return records


def _load_part_links(workbook, machine_keys: set[str]) -> list[PartLinkRecord]:
    sheet = SHEET_PART_LINKS
    if sheet not in workbook.sheetnames:
        return []
    records: list[PartLinkRecord] = []
    pairs: dict[tuple[str, str], int] = {}
    for row, cell in _rows(workbook, sheet, PART_LINK_COLUMNS):
        key = _text(cell['machine_key'])
        if key not in machine_keys:
            raise WorkbookError(
                f'{sheet} row {row}: machine_key {key} is not in {SHEET_MACHINES}'
            )
        ipn = _text(cell['part_ipn'])
        if not ipn:
            raise WorkbookError(f'{sheet} row {row}: part_ipn is required')
        if (key, ipn) in pairs:
            raise WorkbookError(
                f'{sheet} row {row}: ({key}, {ipn}) duplicates row {pairs[key, ipn]}'
            )
        pairs[key, ipn] = row
        records.append(
            PartLinkRecord(
                row=row,
                machine_key=key,
                part_ipn=ipn,
                quantity=_int(
                    cell['quantity'], sheet=sheet, row=row, column='quantity', minimum=1
                ),
                notes=_text(cell['notes']),
            )
        )
    return records


def load_population_workbook(path) -> PopulationWorkbook:
    """Read and validate a population workbook from disk.

    Raises ``WorkbookError`` with a sheet/row reference for anything that
    would make an apply ambiguous. ``openpyxl`` is imported lazily so the
    module can be imported where only the dataclasses are needed.
    """
    from openpyxl import load_workbook

    file = Path(path)
    if not file.is_file():
        raise WorkbookError(f'Workbook not found: {file}')
    size = file.stat().st_size
    if size > MAX_WORKBOOK_BYTES:
        raise WorkbookError(f'Workbook exceeds {MAX_WORKBOOK_BYTES} bytes')
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    try:
        workbook = load_workbook(file, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types
        raise WorkbookError(f'Cannot open workbook: {exc}') from exc
    try:
        for sheet in REQUIRED_SHEETS:
            if sheet not in workbook.sheetnames:
                raise WorkbookError(f'Missing sheet: {sheet}')
        locations = _load_locations(workbook)
        location_codes = {record.code for record in locations}
        machines = _load_machines(workbook, location_codes)
        machine_keys = {record.key for record in machines}
        profiles = _load_profiles(workbook, machine_keys)
        components = _load_components(workbook, machine_keys)
        part_links = _load_part_links(workbook, machine_keys)
    finally:
        workbook.close()
    clients = sorted(
        {record.client_code for record in locations}
        | {record.client_code for record in machines}
    )
    if len(clients) != 1:
        raise WorkbookError(
            'A population workbook must name exactly one client_code; found '
            + (', '.join(clients) or 'none')
        )
    return PopulationWorkbook(
        path=str(file),
        sha256=digest,
        client_codes=tuple(clients),
        locations=locations,
        machines=machines,
        profiles=profiles,
        components=components,
        part_links=part_links,
    )


__all__ = [
    'ComponentRecord',
    'LocationRecord',
    'MachineRecord',
    'PartLinkRecord',
    'PopulationWorkbook',
    'ProfileRecord',
    'WorkbookError',
    'load_population_workbook',
]
