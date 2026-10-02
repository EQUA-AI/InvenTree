"""Station-scoped mimic projection; unusable readings never masquerade as current."""

import math

from django.core.exceptions import ValidationError
from django.utils import timezone

from assets.activation import live_status, point_hash
from assets.health_models import MachineSignalBinding
from assets.models import DictionaryPoint
from InvenTree.conversion import convert_physical_value
from machine_health.mimic_layout import expand_pointer, load_layout, station_pumps

STATUS_POINTER = '/pd/{pump}/st'

LIMITS = (
    'normal_min',
    'normal_max',
    'warn_min',
    'warn_max',
    'critical_min',
    'critical_max',
)


def empty_point(pointer, reason, *, unit='', label='', group=''):
    """Keep unknowns explicit and uniform for both expected and discovered points."""
    return {
        'pointer': pointer,
        'label': label or pointer,
        'group': group,
        'value': None,
        'unit': unit,
        'quality': 'unknown',
        'observed_at': None,
        'age_seconds': None,
        'reason': reason,
        'condition': 'unknown',
        'thresholds_configured': False,
    }


def project_point(point, binding, enabled, now):
    """Expose a value only while its ownership, approval, quality and age remain valid."""
    result = empty_point(
        point.path,
        'not_bound',
        unit=point.unit,
        label=point.display_name,
        group=point.component.name if point.component_id else '',
    )
    if not enabled:
        result['reason'] = 'disabled'
        return result
    if not point.machine.active:
        result['reason'] = 'inactive_equipment'
        return result
    if point.status != 'approved' or point.unit_status not in {'verified', 'unitless'}:
        result['reason'] = 'not_approved'
        return result
    if binding is None:
        return result
    if (
        binding.dictionary_hash != point_hash(point)
        or binding.machine_id != point.machine_id
    ):
        result['reason'] = 'mapping_changed'
        return result
    result['thresholds_configured'] = any(
        getattr(binding, name) is not None for name in LIMITS
    )
    state = getattr(binding, 'state', None)
    if state is None:
        result['reason'] = 'no_data'
        return result
    age = (now - state.observed_at).total_seconds()
    result.update(
        observed_at=state.observed_at, age_seconds=round(age, 3), quality=state.quality
    )
    if age < 0:
        result['reason'] = 'clock_skew'
    elif age > binding.source.freshness_threshold_seconds:
        result['reason'] = 'stale'
    elif state.quality != 'good':
        result['reason'] = 'bad_quality'
    else:
        value = (state.value or {}).get('value')
        if value is None or not isinstance(value, (str, int, float, bool)):
            result['reason'] = 'no_data'
        elif isinstance(value, (int, float)) and not math.isfinite(value):
            result['reason'] = 'bad_quality'
        elif point.data_type == 'number' and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            result['reason'] = 'type_mismatch'
        else:
            result.update(value=value, reason=None, condition=binding.classify(value))
    return result


def total_value(definition, points, pumps, enabled):
    """Prefer a mapped station measurement; otherwise sum every registered bay.

    We do not assume idle bays contribute zero or omit unavailable bays. Review
    must confirm the physical units; convertible reviewed units are normalized.
    """
    direct = points.get(definition['direct'])
    derived = direct is None or direct['reason'] in {'not_bound', 'not_approved'}
    contributors = (
        [expand_pointer(definition['per_pump'], pump.source_key) for pump in pumps]
        if derived
        else [definition['direct']]
    )
    result = {
        'value': None,
        'unit': definition['unit'],
        'derived': derived,
        'method': 'sum_all_registered_bays' if derived else 'source',
        'contributors': contributors,
        'reason': 'disabled' if not enabled else 'incomplete',
    }
    if not enabled or not contributors:
        return result
    values, observed = [], []
    for pointer in contributors:
        point = points.get(pointer)
        if point is None or point['reason'] is not None:
            return result
        value = point['value']
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not point['unit']
        ):
            result['reason'] = 'unconfirmed_unit'
            return result
        try:
            values.append(
                float(
                    convert_physical_value(
                        f'{value} {point["unit"]}', definition['unit']
                    )
                )
            )
        except (ValidationError, ValueError, TypeError):
            result['reason'] = 'incompatible_unit'
            return result
        observed.append(point['observed_at'])
    value = sum(values)
    if not math.isfinite(value):
        result['reason'] = 'bad_quality'
        return result
    result.update(
        value=value,
        reason=None,
        observed_at=min(observed),
        age_seconds=max(points[p]['age_seconds'] for p in contributors),
    )
    return result


def station_mimic(station, *, unit=None, now=None):
    """Build a bounded response without contacting Cosmos or changing any state."""
    now = now or timezone.now()
    layout = load_layout()
    pumps = station_pumps(station)
    if len(pumps) > 128:
        raise ValidationError('Station exceeds the supported 128 pump slots.')
    selected = next((pump for pump in pumps if pump.source_key == unit), None)
    if unit is not None and selected is None:
        raise ValidationError('Unknown pump key for this station.')
    status = live_status(station)
    enabled = status['enabled']
    dictionary = list(
        DictionaryPoint.objects
        .filter(station=station)
        .select_related('machine', 'component')
        .order_by('path')[:20001]
    )
    if len(dictionary) > 20000:
        raise ValidationError('Station dictionary exceeds 20000 points.')
    source_id = status['source']['pk'] if status['source'] else None
    bindings = {}
    for binding in MachineSignalBinding.objects.filter(
        source_id=source_id, dictionary_point__station=station, active=True
    ).select_related('state', 'source'):
        if binding.dictionary_point_id in bindings:
            raise ValidationError('Ambiguous live binding for a dictionary point.')
        bindings[binding.dictionary_point_id] = binding
    points = {
        point.path: project_point(point, bindings.get(point.pk), enabled, now)
        for point in dictionary
    }
    base_reason = 'not_bound' if enabled else 'disabled'
    station_points = {
        point.path: points[point.path]
        for point in dictionary
        if point.machine_id == station.pk
    }
    for element in layout['elements']:
        if element['view'] == 'station':
            pointer = element['pointer']
            station_points.setdefault(
                pointer, empty_point(pointer, base_reason, label=element['label'])
            )
    bays = []
    for pump in pumps:
        rows = {}
        for element in layout['elements']:
            if element['view'] == 'unit':
                pointer = expand_pointer(element['pointer'], pump.source_key)
                rows[pointer] = points.get(
                    pointer, empty_point(pointer, base_reason, label=element['label'])
                )
        status_point = points.get(
            expand_pointer(STATUS_POINTER, pump.source_key),
            empty_point('', base_reason),
        )
        state = 'unknown'
        if status_point['reason'] == 'stale':
            state = 'stale'
        elif status_point['reason'] == 'not_bound':
            state = 'not_bound'
        elif status_point['reason'] is None:
            state = next(
                (
                    name
                    for name, codes in layout['status_values'].items()
                    if status_point['value'] in codes
                ),
                'unknown',
            )
        bays.append({
            'key': pump.source_key,
            'machine': pump.pk,
            'name': pump.name,
            'active': pump.active,
            'state': state,
            'points': rows,
        })
    selected_points = {
        point.path: points[point.path]
        for point in dictionary
        if selected and point.machine_id == selected.pk
    }
    if selected:
        selected_points.update({
            key: value
            for key, value in next(bay for bay in bays if bay['key'] == unit)[
                'points'
            ].items()
            if key not in selected_points
        })
    alarms = [
        {**point, 'machine': item.machine_id}
        for item in dictionary
        if (point := points[item.path])['condition'] in {'warning', 'critical'}
        and point['reason'] is None
    ]
    return {
        'station': station.pk,
        'name': station.name,
        'generated_at': now,
        'source': status['source'],
        'last_poll_at': status['last_poll_at'],
        'last_error_code': status['last_error_code'],
        'enabled': enabled,
        'layout': layout,
        'station_points': station_points,
        'bays': bays,
        'selected_unit': unit,
        'points': selected_points,
        'totals': {
            definition['id']: total_value(definition, points, pumps, enabled)
            for definition in layout['totals']
        },
        'alarms': alarms,
        'unconfigured_thresholds': sum(
            not point['thresholds_configured'] for point in points.values()
        ),
    }
