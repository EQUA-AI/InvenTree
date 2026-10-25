"""Explicit synthetic alarm exercises, isolated from plant measurements."""

import hashlib
import json
import math
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tasks.scope import enforce_machine_scope

from assets.health_models import (
    AnomalySeverity,
    AnomalyStatus,
    MachineAnomaly,
    MachineSignalBinding,
    SignalQuality,
)
from assets.models import AssetMachine
from machine_health.services.alarm_notifications import notify_alarm

DETECTOR = 'test_alarm_ui'


def signal_test_alarms(machine):
    """Return bounded exercise annotations separately from measured signals."""
    result = {}
    alarms = MachineAnomaly.objects.filter(
        machine=machine,
        detector=DETECTOR,
        status__in=[AnomalyStatus.OPEN, AnomalyStatus.ACKNOWLEDGED],
        metrics__synthetic=True,
        bindings__machine=machine,
        bindings__active=True,
    ).values('pk', 'bindings__pk', 'metrics', 'first_observed_at', 'status')
    for alarm in alarms:
        binding_id = alarm['bindings__pk']
        metrics = alarm['metrics']
        if metrics.get('binding_id') != binding_id:
            continue
        result.setdefault(binding_id, []).append({
            'anomaly_id': alarm['pk'],
            'status': alarm['status'],
            'test_value': metrics['test_value'],
            'unit': metrics['unit'],
            'triggered_at': alarm['first_observed_at'].isoformat(),
        })
    return result


def pump_bindings(machine):
    """List actual active channels, retaining the component ownership boundary."""
    return (
        MachineSignalBinding.objects
        .filter(machine=machine, active=True)
        .filter(
            Q(dictionary_point__isnull=True)
            | Q(
                dictionary_point__machine=machine,
                dictionary_point__component__isnull=True,
            )
            | Q(
                dictionary_point__machine=machine,
                dictionary_point__component__machine=machine,
            )
        )
        .select_related('dictionary_point__component', 'state', 'source')
        .order_by('display_name', 'pk')
    )


def targets(machine):
    """Return actual equipment relationships rather than guessed motor tags."""
    from machine_health.services.summary import _binding_rows

    bindings = list(pump_bindings(machine))
    rows = _binding_rows(
        bindings,
        machine if machine.asset_type == 'pumphouse' else machine.parent,
        timezone.now(),
        include_designation=False,
    )
    pending = set(
        MachineAnomaly.objects.filter(
            machine=machine,
            detector=DETECTOR,
            status__in=[
                AnomalyStatus.OPEN,
                AnomalyStatus.ACKNOWLEDGED,
                AnomalyStatus.SUPPRESSED,
            ],
        ).values_list('metrics__binding_id', flat=True)
    )
    results = []
    for binding, row in zip(bindings, rows, strict=True):
        point = binding.dictionary_point
        component = point.component if point and point.component_id else None
        value = row['value']
        numeric = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
        bounds = [
            row['limits'][name] for name in ['normal_max', 'warn_max', 'critical_max']
        ]
        finite_bounds = [
            bound for bound in bounds if bound is not None and math.isfinite(bound)
        ]
        supported = (
            not isinstance(value, (str, bool))
            and (not point or point.data_type not in {'boolean', 'string', 'status'})
            and binding.signal_kind not in {'status', 'boolean', 'digital'}
            and not binding.external_key.upper().endswith(('_STATUS', '_STATE'))
            and (numeric or bool(finite_bounds))
        )
        floor = max(
            [
                *finite_bounds,
                *(
                    [value]
                    if numeric
                    and not row['stale']
                    and row['quality'] == SignalQuality.GOOD
                    else []
                ),
            ],
            default=None,
        )
        suggested = (
            floor + max(abs(floor) * 0.1, 0.1)
            if floor is not None and supported
            else None
        )
        results.append({
            'binding_id': binding.pk,
            'name': binding.display_name,
            'component_id': component.pk if component else None,
            'component_name': component.name if component else None,
            'component_code': component.code if component else None,
            'unit': binding.unit,
            'critical_max': binding.critical_max,
            'external_key': binding.external_key,
            'value': row['value'],
            'quality': row['quality'],
            'stale': row['stale'],
            'observed_at': row['observed_at'],
            'limits': row['limits'],
            'eligible': supported and binding.pk not in pending,
            'ineligible_reason': 'existing_test'
            if binding.pk in pending
            else None
            if supported
            else 'non_numeric',
            'value_floor': floor,
            'suggested_test_value': suggested,
            'limit_source': 'configured'
            if binding.critical_max is not None
            else 'unconfigured',
        })
    return results


def mimic_test_alarms(dictionary, points, bindings, conditions):
    """Project a separate synthetic alarm row, never a fabricated point reading."""
    by_binding = {binding.pk: point_id for point_id, binding in bindings.items()}
    by_point = {point.pk: point for point in dictionary}
    candidates = {
        binding.pk: binding
        for binding in MachineSignalBinding.objects.filter(
            pk__in=[
                (row['metrics'] or {}).get('binding_id')
                for row in conditions
                if row['detector'] == DETECTOR
            ],
            active=True,
        ).select_related('dictionary_point__component__part')
    }
    rows = []
    for anomaly in conditions:
        metrics = anomaly['metrics'] or {}
        if anomaly['detector'] != DETECTOR or not metrics.get('synthetic'):
            continue
        point = by_point.get(by_binding.get(metrics.get('binding_id')))
        binding = candidates.get(metrics.get('binding_id'))
        if binding is None or binding.machine_id != anomaly['machine_id']:
            continue
        mapped = binding.dictionary_point
        if mapped and (
            mapped.machine_id != binding.machine_id
            or (
                mapped.component_id
                and mapped.component.machine_id != binding.machine_id
            )
        ):
            continue
        component = mapped.component if mapped and mapped.component_id else None
        measured = points.get(point.path) if point else None
        rows.append({
            **(
                measured
                or {
                    'pointer': binding.external_key,
                    'unit': binding.unit,
                    'group': component.name if component else '',
                    'part_code': component.part.IPN if component else '',
                    'thresholds_configured': any(
                        getattr(binding, name) is not None
                        for name in [
                            'normal_min',
                            'normal_max',
                            'warn_min',
                            'warn_max',
                            'critical_min',
                            'critical_max',
                        ]
                    ),
                }
            ),
            'label': anomaly['title'],
            'synthetic': True,
            'value': metrics['test_value'],
            'quality': 'synthetic',
            'reason': None,
            'observed_at': anomaly['first_observed_at'].isoformat(),
            'age_seconds': max(
                0, (timezone.now() - anomaly['first_observed_at']).total_seconds()
            ),
            'unchanged_for_seconds': None,
            'condition': anomaly['severity'],
            'machine': anomaly['machine_id'],
            'anomaly': anomaly['pk'],
            'anomaly_status': anomaly['status'],
            'severity': anomaly['severity'],
        })
    return rows


@transaction.atomic
def trigger_test_alarm(
    *,
    machine,
    actor,
    binding_id,
    component_id,
    idempotency_key,
    test_value=None,
    command_context=None,
):
    """Persist a labelled test condition without writing any telemetry rows."""
    if not getattr(settings, 'AIMMS_TEST_ALARMS_ENABLED', False):
        raise PermissionDenied('Test alarms are disabled in this deployment.')
    if not actor.is_active or not actor.is_superuser:
        raise PermissionDenied('Only an administrator can trigger a test alarm.')
    machine = AssetMachine.objects.select_for_update().get(pk=machine.pk)
    enforce_machine_scope(actor, machine)
    token = str(UUID(str(idempotency_key)))
    fingerprint = hashlib.sha256(f'{actor.pk}:{token}'.encode()).hexdigest()
    if test_value is not None and (
        isinstance(test_value, bool) or not math.isfinite(test_value)
    ):
        raise ValidationError('The synthetic test value must be finite.')
    selection = [binding_id, component_id]
    if test_value is not None:
        selection.append(float(test_value))
    if command_context is not None:
        selection.append(command_context)
    request_hash = hashlib.sha256(
        json.dumps(selection, separators=(',', ':')).encode()
    ).hexdigest()
    previous = MachineAnomaly.objects.filter(
        machine=machine, detector=DETECTOR, fingerprint=fingerprint
    ).first()
    if previous:
        if previous.metrics.get('request_hash') != request_hash:
            raise ValidationError('This request key belongs to another selection.')
        transaction.on_commit(lambda: notify_alarm(previous), robust=True)
        return previous
    if MachineAnomaly.objects.filter(
        machine=machine,
        detector=DETECTOR,
        metrics__binding_id=binding_id,
        status__in=[
            AnomalyStatus.OPEN,
            AnomalyStatus.ACKNOWLEDGED,
            AnomalyStatus.SUPPRESSED,
        ],
    ).exists():
        raise ValidationError(
            'A test alarm already exists for this signal. Open its Health record before starting another exercise.'
        )
    target = next(
        (row for row in targets(machine) if row['binding_id'] == binding_id), None
    )
    if not target or not target['eligible']:
        raise ValidationError(
            'The selected channel cannot accept a numeric test exercise.'
        )
    binding = pump_bindings(machine).get(pk=binding_id)
    state = getattr(binding, 'state', None)
    point = binding.dictionary_point
    actual_component = point.component_id if point else None
    if actual_component != component_id:
        raise ValidationError('Signal does not belong to the selected component.')
    limit = binding.critical_max
    if test_value is None and limit is None:
        raise ValidationError(
            'An explicit synthetic value is required without a configured critical limit.'
        )
    value = target['suggested_test_value'] if test_value is None else float(test_value)
    if not math.isfinite(value) or (
        target['value_floor'] is not None and value <= target['value_floor']
    ):
        raise ValidationError(
            'The synthetic value must exceed the measured value and all configured upper bounds.'
        )
    basis = (
        f'configured critical limit {limit:g}'
        if limit is not None
        else 'exercise severity; no configured critical limit'
    )
    anomaly = MachineAnomaly.objects.create(
        machine=machine,
        source=binding.source,
        detector=DETECTOR,
        detector_version='1',
        fingerprint=fingerprint,
        title=f'TEST alarm — {binding.display_name}'[:255],
        evidence_summary=(
            f'Synthetic exercise: {value:g} {binding.unit}; {basis}. '
            'Not a measurement or a confirmed plant condition.'
        ),
        severity=AnomalySeverity.CRITICAL,
        status=AnomalyStatus.OPEN,
        first_observed_at=timezone.now(),
        last_observed_at=timezone.now(),
        metrics={
            'test_alarm': True,
            'synthetic': True,
            'component_id': component_id,
            'binding_id': binding.pk,
            'test_value': value,
            'unit': binding.unit,
            'critical_max': limit,
            'measurement_at_trigger': state.value if state else None,
            'observed_at': state.observed_at.isoformat() if state else None,
            'measurement_quality': target['quality'],
            'measurement_stale': target['stale'],
            'limit_source': target['limit_source'],
            'triggered_by': actor.pk,
            'request_hash': request_hash,
        },
    )
    anomaly.bindings.add(binding)
    transaction.on_commit(lambda: notify_alarm(anomaly), robust=True)
    return anomaly


def recover_test_alarms(work_order, actor, *, kind='verified_closeout', reason=''):
    """Resolve only marked synthetic conditions; callers retain purpose and audit."""
    resolved = []
    for anomaly in MachineAnomaly.objects.select_for_update().filter(
        work_order=work_order,
        machine_id=work_order.machine_id,
        detector=DETECTOR,
        status__in=[
            AnomalyStatus.OPEN,
            AnomalyStatus.ACKNOWLEDGED,
            AnomalyStatus.SUPPRESSED,
        ],
    ):
        if not anomaly.metrics.get('synthetic'):
            raise ValidationError('An unmarked condition cannot be test-recovered.')
        anomaly.status = AnomalyStatus.RESOLVED
        anomaly.resolved_at = timezone.now()
        anomaly.metrics = {
            **anomaly.metrics,
            'recovered_by': actor.pk,
            'recovered_at': anomaly.resolved_at.isoformat(),
            'recovery_kind': kind,
            'recovery_reason': reason,
        }
        anomaly.save(update_fields=['status', 'resolved_at', 'metrics', 'updated_at'])
        resolved.append(anomaly.pk)
    return resolved
