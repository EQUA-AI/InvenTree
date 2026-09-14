"""Portable review packs for large station dictionaries; never infer approval."""

import hashlib
import json

from django.core.exceptions import ValidationError

from assets.models import AssetComponent, DictionaryPoint
from common.models import ParameterTemplate
from part.models import Part


def dictionary_hash(station):
    """Invalidate review packs when observed tags, meanings or prior decisions change."""
    rows = list(
        DictionaryPoint.objects
        .filter(station=station)
        .order_by('path')
        .values(
            'path',
            'source_hash',
            'machine_id',
            'component_id',
            'template_id',
            'data_type',
            'unit',
            'unit_status',
            'status',
            'review_note',
        )
    )
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def export_review(station):
    """Export exact source paths, existing approvals and actionable pending mappings."""
    result = {
        'station_uuid': str(station.uuid),
        'station_source_uuid': str(station.source_entity_uuid),
        'source_namespace': station.source_namespace,
        'dictionary_hash': dictionary_hash(station),
        'approve': [],
        'withhold': [],
        'pending': [],
    }
    for point in (
        DictionaryPoint.objects
        .filter(station=station)
        .select_related('machine', 'component__part', 'template')
        .order_by('path')
    ):
        entry = {
            'paths': [point.path],
            'owner_key': point.machine.source_key,
            'data_type': point.data_type,
            'unit': point.unit,
            'unit_status': point.unit_status,
            'note': point.review_note,
        }
        if point.component_id and point.template_id:
            entry['mapping'] = {
                'part_ipn': point.component.part.IPN,
                'component_code': point.component.code,
                'parameter': point.template.name,
            }
        if point.status == 'approved':
            result['approve'].append(entry)
        else:
            entry['issue'] = (
                point.issue or 'Review mapping, units and ownership before approval.'
            )
            result['pending'].append(entry)
    return result


def map_review_point(point, entry):
    """Apply an explicit exact-path catalogue crosswalk, including unresolved tags."""
    if entry.get('owner_key', point.machine.source_key) != point.machine.source_key:
        raise ValidationError(
            f'{point.path}: owner differs; correct registry ownership first.'
        )
    mapping = entry.get('mapping')
    if mapping is None:
        return
    part = Part.objects.get(IPN=mapping['part_ipn'], active=True)
    template = ParameterTemplate.objects.get(name=mapping['parameter'], enabled=True)
    if not part.parameters_list.filter(template=template).exists():
        raise ValidationError(
            f'{point.path}: parameter does not belong to the selected catalogue part.'
        )
    code = mapping['component_code']
    if not isinstance(code, str) or not code.strip() or len(code) > 100:
        raise ValidationError('A component code of at most 100 characters is required.')
    component, _ = AssetComponent.objects.get_or_create(
        machine=point.machine,
        code=code,
        defaults={
            'part': part,
            'name': part.name,
            'provenance': 'Explicit dictionary review crosswalk',
        },
    )
    if component.part_id != part.pk:
        raise ValidationError(
            f'{point.path}: component slot already uses a different catalogue part.'
        )
    point.component, point.template = component, template
    point.match_method = 'review'
    point.clean()
