"""Offline registry planning and additive imports. Never writes telemetry."""

import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from django.core.exceptions import ValidationError
from django.db import transaction

from common.models import ParameterTemplate
from part.management.commands.load_pump_catalogue import (
    CATALOGUE,
    NAMESPACE,
    validate_catalogue,
)
from part.models import Part

from .models import AssetComponent, AssetMachine, DictionaryPoint

MAX_BYTES = 8 * 1024 * 1024
MAX_POINTS = 25000
PUMP = re.compile(r'^PUMP([1-9][0-9]{0,3})_')
ALIASES = [
    (r'MOTOR_CORE_RTD([1-6])_PROCESS_VALUE', r'MOTOR_CORE_RTD\1'),
    (r'THRST_BRG_THRST_PD_RTD([1-3])_PROCESS_VALUE', r'THRST_BRG_THRST_PD_RTD\1'),
    (r'MTR_NDE_BRG_VBRTN([1-2])_PROCESS_VALUE', r'MTR_NDE_BRG_VBRTN\1'),
    (r'PMP_THRST_BRG_VBRTN([1-3])_PROCESS_VALUE', r'PMP_THRST_BRG_VBRTN\1'),
    (r'EXCITATION_FLD_CURR_PROCESS_VALUE', 'EXCITATION_FLD_CURR'),
    (r'EXCITATION_FLD_VLTG_PROCESS_VALUE', 'EXCITATION_FLD_VLTG'),
    (r'PUMP_MOTOR_DE_VIBRATION2', 'MOTOR_DE_VIBRATION2'),
    (r'PUMP_REACTIVE_POWER', 'REACTIVE_POWER'),
]


def decode_upload(raw):
    """Bound bytes, depth and nodes and reject duplicate keys/non-finite JSON."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ValidationError('Upload must be nonempty JSON no larger than 8 MiB.')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Non-finite JSON number')

    try:
        value = json.loads(
            raw.decode('utf-8'),
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
            parse_float=Decimal,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValidationError(
            'Invalid UTF-8 JSON, duplicate key, or non-finite number.'
        ) from exc
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 200000 or depth > 16:
            raise ValidationError('JSON exceeds nesting or node limits.')
        if isinstance(item, dict):
            if any(len(key) > 255 for key in item):
                raise ValidationError('Source keys must be at most 255 characters.')
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str) and len(item) > MAX_BYTES:
            raise ValidationError('JSON value is too large.')
    return value


def catalogue_targets():
    """Resolve only loader-owned definitions and families, without creating rows."""
    parts = {}
    for part in Part.objects.filter(IPN__startswith='PS-'):
        if (part.metadata or {}).get(NAMESPACE, {}).get('kind') != 'part':
            continue
        code = part.IPN[3:]
        if code in parts:
            raise ValidationError('Ambiguous catalogue IPN; resolve duplicates first.')
        parts[code] = part
    templates = {
        t.name: t
        for t in ParameterTemplate.objects.filter(
            name__startswith='PUMP | ', enabled=True
        )
    }
    targets = {}
    for component in validate_catalogue(json.loads(CATALOGUE.read_text())):
        for definition in component['expanded_parameters']:
            template = templates.get(definition['name'])
            part = parts.get(component['code'])
            if (
                part
                and template
                and (template.metadata or {}).get(NAMESPACE, {}).get('kind')
                == 'template'
            ):
                targets[definition['source_tag']] = (part, template, definition)
    return targets


def register_station(
    *,
    client,
    name,
    source_namespace,
    source_entity_uuid,
    source_key,
    public_uuid=None,
    source_context=None,
):
    """Register one source station idempotently, retaining reserved public identity."""
    with transaction.atomic():
        existing = AssetMachine.objects.filter(
            asset_type='pumphouse',
            source_namespace=source_namespace,
            source_entity_uuid=source_entity_uuid,
        ).first()
        if existing:
            if existing.client_id != client.pk or existing.source_key != source_key:
                raise ValidationError(
                    'Source station identity conflicts with an existing registration.'
                )
            if public_uuid and existing.uuid != UUID(str(public_uuid)):
                raise ValidationError(
                    'Reserved station UUID conflicts with the registered UUID.'
                )
            return existing
        fields = {
            'client': client,
            'name': name,
            'asset_type': 'pumphouse',
            'source_namespace': source_namespace,
            'source_entity_uuid': source_entity_uuid,
            'source_key': source_key,
            'source_context': source_context or {},
        }
        if public_uuid:
            fields['uuid'] = public_uuid
        station = AssetMachine(**fields)
        station.full_clean()
        station.save()
        return station


def ensure_pump(station, key):
    """Register a pump slot, not a verified physical installation or stock item."""
    if not re.fullmatch(r'P[1-9][0-9]{0,3}', key):
        raise ValidationError('Pump source keys must be P1 through P9999.')
    pump, _ = AssetMachine.objects.get_or_create(
        parent=station,
        source_key=key,
        defaults={
            'uuid': uuid5(station.uuid, f'pump:{key}'),
            'asset_type': 'pump',
            'client': station.client,
            'name': f'{station.name[:170]} / Pump {int(key[1:]):02d} [{str(station.uuid)[:8]}]',
            'description': 'Registered source pump slot; installed equipment details require review.',
        },
    )
    return pump


def observed_type(value):
    """Describe transport types only; no values are stored in the dictionary."""
    if value is None:
        return 'unknown'
    if isinstance(value, bool):
        return 'boolean'
    try:
        if Decimal(str(value)).is_finite():
            return 'number'
    except InvalidOperation:
        pass
    return 'text'


def plan_dictionary(station, raw):
    """Build a pure read-only preview from payloads or bounded Cassandra row exports."""
    if station.asset_type != 'pumphouse':
        raise ValidationError('Select a registered pump station for import.')
    decoded = decode_upload(raw)
    rows = decoded.get('rows', [decoded]) if isinstance(decoded, dict) else decoded
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValidationError('Provide one payload/row or a list of at most 100 rows.')
    targets = catalogue_targets()
    points, pumps, observed_types = {}, set(), {}
    digest = hashlib.sha256(raw).hexdigest()

    def add(path, tag, value, owner_key, local_tag, *, allow_match=True):
        if isinstance(value, (dict, list)) or len(path) > 500:
            raise ValidationError(
                'Measurements must be scalar and paths at most 500 characters.'
            )
        if isinstance(value, str) and len(value) > 2048:
            raise ValidationError(
                'Measurement strings must be at most 2048 characters.'
            )
        method, target = 'unresolved', targets.get(local_tag) if allow_match else None
        if target:
            method = 'exact'
        elif allow_match:
            for pattern, replacement in ALIASES:
                if re.fullmatch(pattern, local_tag):
                    target = targets.get(re.sub(pattern, replacement, local_tag))
                    if target:
                        method = 'alias'
                    break
        item = {
            'path': path,
            'raw_tag': tag,
            'owner_key': owner_key,
            'display_name': tag,
            'match_method': method,
            'part': None,
            'part_name': '',
            'template': None,
            'template_name': '',
            'component_code': '',
            'data_type': observed_type(value),
            'unit': '',
            'unit_status': 'unresolved',
            'issue': '',
        }
        if target:
            part, template, definition = target
            item.update(
                part=part.pk,
                part_name=part.name,
                template=template.pk,
                template_name=template.name,
                display_name=definition['display_name'],
                component_code=part.IPN,
                data_type=definition['data_type'],
                unit=template.units,
                unit_status=definition['unit_status'],
            )
        previous = points.get(path)
        kinds = observed_types.setdefault(path, set())
        kind = observed_type(value)
        if kind != 'unknown':
            kinds.add(kind)
        if len(kinds) > 1 or (
            target and item['data_type'] == 'number' and kinds - {'number'}
        ):
            item.update(
                match_method='conflict',
                issue='Inconsistent transport types across rows',
            )
        elif previous and previous['match_method'] == 'conflict':
            item = previous
        points[path] = item
        if len(points) > MAX_POINTS:
            raise ValidationError(
                'More than 25,000 distinct dictionary points; split the export.'
            )

    def pointer(value):
        return value.replace('~', '~0').replace('/', '~1')

    def epoch_ms(value):
        """Read an epoch-ms timestamp that Cassandra may carry as text or bigint.

        ``time_period`` is a ``text`` column and ``sub_time_period`` a ``bigint``,
        so an export of the same row yields a string for one and a number for the
        other. Both are epoch milliseconds; a booleans-are-ints accident and a
        float that cannot be a millisecond count are rejected rather than coerced.
        """
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r'-?[0-9]{1,19}', value):
            return int(value)
        return None

    matched = 0

    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError('Each row/payload must be an object.')
        payload = row
        if 'data1' in row:
            # An hour slice is naturally multi-station: entity_uuid clusters after
            # sub_time_period, so a bounded read returns every station in the
            # bucket. Skip the other stations instead of rejecting the export.
            if str(row.get('entity_uuid')) != str(station.source_entity_uuid):
                continue
            for key, expected in station.source_context.items():
                if key in row and row[key] != expected:
                    raise ValidationError(f'Row selector mismatch: {key}')
            if 'time_period' in row or 'sub_time_period' in row:
                hour = epoch_ms(row.get('time_period'))
                sample = epoch_ms(row.get('sub_time_period'))
                if (
                    hour is None
                    or sample is None
                    or not hour <= sample < hour + 3600000
                ):
                    raise ValidationError(
                        'Sample timestamp is outside its hourly bucket.'
                    )
            payload = row['data1']
            if isinstance(payload, str):
                payload = decode_upload(payload.encode('utf-8'))
        matched += 1
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get('dex'), dict)
            or not isinstance(payload.get('pd'), dict)
        ):
            raise ValidationError('Payload must contain pd and dex objects.')
        if payload['dex'].get('ID', station.source_key) != station.source_key:
            raise ValidationError(
                'Payload dex.ID does not match the selected station code.'
            )
        for key, value in payload.items():
            if key not in {'pd', 'dex', 'sr', 'egt', 'ext', 'dsc'}:
                add('/' + pointer(key), key, value, '', key, allow_match=key == 'st')
        for key, summary in payload['pd'].items():
            if not re.fullmatch(r'P[1-9][0-9]{0,3}', key) or not isinstance(
                summary, dict
            ):
                raise ValidationError(
                    'pd must contain pump objects with keys P1 through P9999.'
                )
            pumps.add(key)
            for tag, value in summary.items():
                add(
                    f'/pd/{key}/{pointer(tag)}',
                    tag,
                    value,
                    key,
                    tag,
                    allow_match=tag == 'st',
                )
        for tag, value in payload['dex'].items():
            if tag in {'ID', 'TIMESTAMP'}:
                continue
            match = PUMP.match(tag)
            owner_key = f'P{match[1]}' if match else ''
            if owner_key:
                pumps.add(owner_key)
            local = tag[match.end() :] if match else tag
            add(
                '/dex/' + pointer(tag),
                tag,
                value,
                owner_key,
                local,
                allow_match=bool(match) or tag == 'COMMAN_FORBAY_LEVEL',
            )
    if not matched:
        raise ValidationError('No row in this export belongs to the selected station.')
    if len(pumps) > 100:
        raise ValidationError(
            'At most 100 pump slots are supported per station import.'
        )
    duplicates = Counter(
        (p['owner_key'], p['template']) for p in points.values() if p['template']
    )
    existing = {
        p.path: p
        for p in DictionaryPoint.objects.filter(station=station).select_related(
            'machine'
        )
    }
    existing_targets = {}
    for point in existing.values():
        if point.template_id and point.status != 'rejected':
            owner_key = (
                '' if point.machine_id == station.pk else point.machine.source_key
            )
            existing_targets.setdefault((owner_key, point.template_id), set()).add(
                point.path
            )
    for point in points.values():
        key = (point['owner_key'], point['template'])
        if point['template'] and (
            duplicates[key] > 1 or existing_targets.get(key, set()) - {point['path']}
        ):
            point.update(
                match_method='conflict',
                issue='Multiple source paths propose the same owner/parameter; review each path',
            )
        point['existing_status'] = (
            existing[point['path']].status if point['path'] in existing else None
        )
    counts = Counter(p['match_method'] for p in points.values())
    counts.update(
        total=len(points),
        new=sum(p['path'] not in existing for p in points.values()),
        preserved=sum(p['path'] in existing for p in points.values()),
    )
    return {
        'station': station.pk,
        'source_hash': digest,
        'rows': len(rows),
        'rows_matched': matched,
        'pumps': sorted(pumps, key=lambda k: int(k[1:])),
        'counts': dict(counts),
        'points': list(points.values()),
    }


@transaction.atomic
def import_dictionary(station, raw, *, expected_hash):
    """Replan and add missing slots/points, preserving every previously reviewed row."""
    station = AssetMachine.objects.select_for_update().get(pk=station.pk)
    plan = plan_dictionary(station, raw)
    if not expected_hash or expected_hash != plan['source_hash']:
        raise ValidationError('Preview this exact file before importing it.')
    owners = {'': station, **{key: ensure_pump(station, key) for key in plan['pumps']}}
    existing = set(
        DictionaryPoint.objects.filter(station=station).values_list('path', flat=True)
    )
    components, pending = {}, []
    for point in plan['points']:
        if point['path'] in existing:
            continue
        owner = owners[point['owner_key']]
        component = None
        if point['part'] and point['match_method'] in {'exact', 'alias'}:
            key = (owner.pk, point['component_code'])
            if key not in components:
                components[key], _ = AssetComponent.objects.get_or_create(
                    machine=owner,
                    code=point['component_code'] + ':1',
                    defaults={
                        'part_id': point['part'],
                        'name': point['part_name'],
                        'provenance': 'Inferred from source tags; physical assignment is unverified.',
                    },
                )
            component = components[key]
            if component.part_id != point['part']:
                raise ValidationError(
                    'An existing component slot has a different catalogue Part; review its assignment.'
                )
        pending.append(
            DictionaryPoint(
                station=station,
                machine=owner,
                path=point['path'],
                raw_tag=point['raw_tag'],
                display_name=point['display_name'],
                component=component,
                template_id=point['template'],
                match_method=point['match_method'],
                data_type=point['data_type'],
                unit=point['unit'],
                unit_status=point['unit_status'],
                source_hash=plan['source_hash'],
                status='draft'
                if point['match_method'] in {'exact', 'alias'}
                else 'unresolved',
                issue=point['issue'],
            )
        )
    DictionaryPoint.objects.bulk_create(pending, batch_size=500)
    return {
        'created': len(pending),
        'preserved': plan['counts']['preserved'],
        'pumps': len(plan['pumps']),
        'source_hash': plan['source_hash'],
    }
