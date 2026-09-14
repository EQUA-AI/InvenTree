"""Shared, reviewable layout contract and per-station coverage checks."""

import json
import re
from pathlib import Path
from xml.etree import ElementTree

from django.core.exceptions import ValidationError

from assets.models import DictionaryPoint

PUMP_TOKEN = '{pump}'
NUMBER_TOKEN = '{pump_number}'

LAYOUT_PATH = Path(__file__).parent / 'layouts/pumphouse.layout.json'


def load_layout(path=None):
    """Reject malformed templates before either API or validator uses them."""
    layout = json.loads(Path(path or LAYOUT_PATH).read_text(encoding='utf-8'))
    if layout.get('version') != 1 or layout.get('review_status') not in {
        'provisional',
        'approved',
    }:
        raise ValidationError('Unknown layout version or review status.')
    ids = set()
    for element in layout['elements']:
        if element['id'] in ids or element['view'] not in {'station', 'unit'}:
            raise ValidationError('Duplicate layout element or invalid view.')
        ids.add(element['id'])
        if element['role'] not in {'value', 'level', 'valve', 'status'}:
            raise ValidationError('Invalid layout role.')
        expand_pointer(element['pointer'], 'P1')
    for total in layout['totals']:
        expand_pointer(total['direct'], 'P1')
        expand_pointer(total['per_pump'], 'P1')
    states = [value for values in layout['status_values'].values() for value in values]
    if len(states) != len(set(states)):
        raise ValidationError('A status code cannot have several meanings.')
    return layout


def expand_pointer(template, pump):
    """Substitute the exact registered key with JSON-pointer escaping."""
    value = template.replace(
        PUMP_TOKEN, str(pump).replace('~', '~0').replace('/', '~1')
    )
    if NUMBER_TOKEN in value:
        match = re.fullmatch(r'P([1-9][0-9]*)', str(pump))
        if not match:
            raise ValidationError(
                'A numbered pointer requires a registered P-number key.'
            )
        value = value.replace(NUMBER_TOKEN, match[1])
    if not value.startswith('/') or '{' in value or '}' in value:
        raise ValidationError(
            'Layout pointers must be absolute with only the pump placeholder.'
        )
    return value


def station_pumps(station):
    """Use registered slots, retaining sparse keys and inactive equipment."""
    return list(
        station.children.filter(client_id=station.client_id).order_by('source_key')
    )


def layout_coverage(station, layout=None):
    """Report every missing approval and every point displayed outside the diagram."""
    layout = layout or load_layout()
    approved = set(
        DictionaryPoint.objects.filter(station=station, status='approved').values_list(
            'path', flat=True
        )
    )
    wanted = {}
    pumps = station_pumps(station)
    for element in layout['elements']:
        for key in (
            [pump.source_key for pump in pumps] if element['view'] == 'unit' else ['']
        ):
            pointer = expand_pointer(element['pointer'], key)
            wanted[f'{element["id"]}:{key}'] = pointer
    missing = [
        {'element': element, 'pointer': pointer}
        for element, pointer in wanted.items()
        if pointer not in approved
    ]
    return {
        'station': station.pk,
        'layout_version': layout['version'],
        'review_status': layout['review_status'],
        'ready': not missing and layout['review_status'] == 'approved',
        'missing': missing,
        'not_drawn': [
            {'pointer': pointer, 'reason': layout['not_drawn_reason']}
            for pointer in sorted(approved - set(wanted.values()))
        ],
    }


def validate_assets(layout, directory):
    """Require SVG annotations to agree exactly with their layout elements."""
    for view, filename in [
        ('station', 'pumphouse-overview.svg'),
        ('unit', 'pump-unit.svg'),
    ]:
        root = ElementTree.parse(Path(directory) / filename).getroot()
        actual = {}
        for node in root.iter():
            if node.tag.split('}')[-1] in {'script', 'foreignObject', 'image', 'text'}:
                raise ValidationError(
                    'SVG assets must contain only geometry and annotations.'
                )
            if any(
                key.lower().startswith('on') or 'href' in key.lower()
                for key in node.attrib
            ):
                raise ValidationError(
                    'SVG assets cannot contain executable or external references.'
                )
            if 'data-point' in node.attrib:
                key = node.attrib.get('id')
                if key in actual:
                    raise ValidationError('Duplicate SVG point element.')
                actual[key] = node.attrib['data-point']
        expected = {
            element['id']: element['pointer']
            for element in layout['elements']
            if element['view'] == view
        }
        if actual != expected:
            raise ValidationError(
                f'{filename}: SVG data-point annotations differ from the layout: {actual} != {expected}'
            )
