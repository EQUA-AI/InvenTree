"""O15: scoped append-only human records using the existing Work command ledger."""

import hashlib
import json
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from assets.health_models import MachineSignalBinding
from repair.authority_models import GateAuthorityRecord
from repair.models import RepairPacketGate
from tasks.authority_models import OperationalAuthorityRecord
from tasks.scope import ScopeError, enforce_machine_scope, work_machine_authorized
from tasks.services.investigation_requests import _change_actor
from tasks.services.work_orders import (
    StaleVersion,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
    _require_version,
)
from tasks.work_execution.common import PACKET_WITHHELD, packet_of, person

OPERATIONS_PERMISSION = 'tasks.record_operating_authority'
KINDS = (
    'isolation',
    'permit',
    'access_window',
    *OperationalAuthorityRecord.Kind.values,
)
OUTCOMES = {
    'isolation': ('active', 'released', 'revoked'),
    'permit': ('active', 'released', 'revoked'),
    'access_window': ('active', 'released', 'revoked'),
    'test_acceptance': ('accepted', 'rejected'),
    'operating_permission': ('granted', 'withheld', 'revoked'),
    'observed_return': ('observed_running', 'observed_stopped'),
    # Historic wire code: verify this sample is current, never textual agreement.
    'service_state': ('confirmed', 'corrected', 'operator_recorded'),
}


class RecordFields(serializers.Serializer):
    """Only explicit authored facts; actors and source snapshots are server-owned."""

    kind = serializers.ChoiceField(choices=KINDS)
    outcome = serializers.CharField(max_length=24)
    statement = serializers.CharField(max_length=4000)
    source_reference = serializers.CharField(max_length=1000)
    observed_at = serializers.DateTimeField()
    effective_from = serializers.DateTimeField()
    effective_until = serializers.DateTimeField(required=False, allow_null=True)
    gate_id = serializers.IntegerField(required=False, min_value=1)
    target_id = serializers.IntegerField(required=False, min_value=1)
    duty = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    standby = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    redundancy = serializers.CharField(
        max_length=1000, required=False, allow_blank=True
    )
    restrictions = serializers.CharField(
        max_length=1000, required=False, allow_blank=True
    )
    binding_id = serializers.IntegerField(required=False, min_value=1)
    telemetry_observed_at = serializers.DateTimeField(required=False)
    telemetry_token = serializers.RegexField(r'^[a-f0-9]{64}$', required=False)


def _gate_permitted(actor, gate):
    return actor.has_perm('tasks.execute_workorder') and (
        not gate.required_permission or actor.has_perm(gate.required_permission)
    )


def _targets(work, actor):
    if not work.machine_id:
        return []
    targets = [work.machine]
    if work.machine.parent_id:
        targets.append(work.machine.parent)
    result = []
    for machine in targets:
        try:
            enforce_machine_scope(actor, machine)
        except ScopeError:
            continue
        result.append(machine)
    return result


def telemetry(work, actor):
    """Read reviewed bindings, preserving raw meaning, quality and source clock.

    Existing dictionary: /st and /pd/<bay>/st report status; /pc counts
    running bays; MOTOR_ON_STATUS is a run bit. None establishes duty,
    available standby or redundancy. Dedicated reviewed signal kinds, if
    configured, are displayed verbatim without inventing an encoding.
    """
    result = []
    for binding in MachineSignalBinding.objects.filter(
        machine__in=_targets(work, actor), active=True
    ).select_related('machine', 'source', 'dictionary_point', 'state'):
        point = binding.dictionary_point
        if (
            point is None
            or point.status != 'approved'
            or point.machine_id != binding.machine_id
        ):
            continue
        tag = point.raw_tag.upper()
        if not (
            tag in ('ST', 'PC')
            or tag.endswith('MOTOR_ON_STATUS')
            or binding.signal_kind in ('duty', 'standby', 'redundancy', 'run_status')
        ):
            continue
        state = getattr(binding, 'state', None)
        reading = {
            'binding_id': binding.pk,
            'target_id': binding.machine_id,
            'target_name': binding.machine.name,
            'tag': point.raw_tag,
            'path': point.path,
            'signal_kind': binding.signal_kind or 'run_context',
            'source': binding.source.name,
            'source_id': binding.source_id,
            'source_sequence': state.source_sequence if state else None,
            'source_active': binding.source.active,
            'value': state.value if state else None,
            'observed_at': state.observed_at.isoformat() if state else None,
            'quality': state.quality if state else 'unknown',
            'stale': state.is_stale(binding.source.freshness_threshold_seconds)
            if state
            else None,
        }
        reading['reading_token'] = _source_token({
            key: value for key, value in reading.items() if key != 'stale'
        })
        result.append(reading)
    return sorted(result, key=lambda row: row['binding_id'])


def _basis(work):
    packet = packet_of(work)
    scopes = (
        [row.pk for row in packet._authority_scopes if row.superseded_at is None]
        if hasattr(packet, '_authority_scopes')
        else list(
            packet.approved_scopes.filter(superseded_at__isnull=True).values_list(
                'pk', flat=True
            )
        )
        if packet
        else []
    )
    return {'machine_id': work.machine_id, 'scope_ids': sorted(scopes)}


def _test_basis(work):
    from tasks.models import WorkOrderStepExecution

    if hasattr(work, '_authority_executions'):
        return [
            {
                key: getattr(row, key)
                for key in ('id', 'version', 'status', 'value', 'passed')
            }
            for row in sorted(work._authority_executions, key=lambda row: row.pk)
        ]
    return list(
        WorkOrderStepExecution.objects
        .filter(application__work_order=work)
        .order_by('pk')
        .values('id', 'version', 'status', 'value', 'passed')
    )


def capture_read_sources(work):
    """Share the exact read objects across the overview's scope/test/gate panels.

    Native writers need not share a transaction or advance Work's version.
    A token must describe these displayed objects, never a later re-query.
    """
    from tasks.models import WorkOrderStepExecution

    packet = packet_of(work)
    if packet:
        packet._authority_scopes = list(
            packet.approved_scopes.select_related('approved_by').order_by(
                '-version', '-pk'
            )
        )
        packet._authority_gates = list(
            packet.gates.all().order_by('sequence', 'created_at')
        )
    work._authority_executions = list(
        WorkOrderStepExecution.objects
        .filter(application__work_order=work)
        .select_related('application', 'completed_by')
        .order_by('application__sequence', 'sequence', 'pk')
    )


def clear_read_sources(work):
    """The snapshot belongs only to this serialization, never a later command."""
    packet = packet_of(work)
    for target, names in (
        (packet, ('_authority_scopes', '_authority_gates')),
        (work, ('_authority_executions',)),
    ):
        for name in names:
            if hasattr(target, name):
                delattr(target, name)


def read_gates(packet):
    """Use the overview's captured contract when one is present."""
    if packet is None:
        return []
    if hasattr(packet, '_authority_gates'):
        return packet._authority_gates
    return list(packet.gates.all().order_by('sequence', 'created_at'))


def _gate_basis(gate):
    """The gate contract the recorder reviewed, independently of gate status."""
    return {
        key: getattr(gate, key)
        for key in (
            'id',
            'packet_id',
            'gate_type',
            'is_blocking',
            'is_mandatory',
            'requires_photo',
            'requires_second_person',
            'required_permission',
        )
    }


def _source_token(sources):
    """Canonical digest of the exact serialized source values."""
    return hashlib.sha256(
        json.dumps(sources, sort_keys=True, cls=DjangoJSONEncoder).encode()
    ).hexdigest()


def _context(work, gates=None):
    """Pin sources which can change without advancing the Work version."""
    if gates is None:
        packet = packet_of(work)
        gates = read_gates(packet)
    sources = {
        'basis': _basis(work),
        'tests': _test_basis(work),
        'gates': sorted((_gate_basis(gate) for gate in gates), key=lambda g: g['id']),
    }
    return sources, _source_token(sources)


def record_data(row, *, basis=None, current=None, tests=None):
    """Saved provenance and live expiry are distinct fields."""
    now = timezone.now()
    gate = getattr(row, 'gate', None)
    independent = (
        not gate
        or not gate.requires_second_person
        or bool(row.verified_by_id and row.verified_by_id != row.recorded_by_id)
    )
    source_basis = basis if basis is not None else _basis(row.work_order)
    matches = {
        key: row.basis.get(key) for key in ('machine_id', 'scope_ids')
    } == source_basis
    if gate:
        matches = matches and row.basis.get('gate') == _gate_basis(gate)
    if row.kind == 'test_acceptance':
        matches = matches and row.basis.get('tests') == (
            tests if tests is not None else _test_basis(row.work_order)
        )
    return {
        'id': row.pk,
        'work_order_id': row.work_order_id,
        'version': row.version,
        'kind': row.kind,
        'outcome': row.outcome,
        'statement': row.statement,
        'source_reference': row.source_reference,
        'recorded_by': person(row.recorded_by),
        'recorded_by_id': row.recorded_by_id,
        'recorded_at': row.recorded_at.isoformat(),
        'observed_at': row.observed_at.isoformat(),
        'effective_from': row.effective_from.isoformat(),
        'effective_until': row.effective_until.isoformat()
        if row.effective_until
        else None,
        'effective_state': 'awaiting_verification'
        if not independent
        else 'not_yet_effective'
        if row.effective_from > now
        else 'expired'
        if row.effective_until and row.effective_until <= now
        else 'within_window',
        'basis_state': 'current' if matches else 'changed_or_unrecorded',
        'supersedes': row.supersedes_id,
        'current': current
        if current is not None
        else not type(row).objects.filter(supersedes=row).exists(),
        'gate_id': gate.pk if gate else None,
        'verified_by': person(row.verified_by) if gate else None,
        'verified_by_id': row.verified_by_id if gate else None,
        'gate_name': gate.name if gate else None,
        'target_name': row.target.name if not gate else None,
        'verified_at': row.verified_at.isoformat()
        if gate and row.verified_at
        else None,
        'target_id': getattr(row, 'target_id', None),
        'details': getattr(row, 'details', {}),
        'basis': row.basis,
    }


def _packet_sources_authorized(work, actor):
    """Work provenance never grants a packet's native source identities."""
    packet = packet_of(work)
    return bool(
        packet
        and packet.machine_id == work.machine_id
        and work_machine_authorized(actor, work)
    )


@contextmanager
def _packet_sources(work, actor):
    """Guard packet sources consistently in reads and new command contexts."""
    previous = work.__dict__.get(PACKET_WITHHELD)
    work.__dict__[PACKET_WITHHELD] = not _packet_sources_authorized(work, actor)
    try:
        yield
    finally:
        if previous is None:
            work.__dict__.pop(PACKET_WITHHELD, None)
        else:
            work.__dict__[PACKET_WITHHELD] = previous


def _without_packet_sources(record):
    """Redact packet provenance in a response without rewriting the saved fact."""
    return {**record, 'basis': {**record['basis'], 'scope_ids': []}}


def records(work, *, physical=False, actor=None):
    """Never cross the canonical Work parent when projecting records."""
    model = GateAuthorityRecord if physical else OperationalAuthorityRecord
    packet_authorized = _packet_sources_authorized(work, actor)
    if physical and not packet_authorized:
        return []
    if (
        physical
        and actor
        and work.machine_id not in {target.pk for target in _targets(work, actor)}
    ):
        return []
    query = model.objects.filter(work_order=work).select_related('recorded_by')
    if physical:
        query = query.filter(
            gate__packet__work_order=work, gate__packet__machine_id=work.machine_id
        ).select_related('gate', 'verified_by')
    if not physical:
        ids = (
            [target.pk for target in _targets(work, actor)]
            if actor
            else [work.machine_id]
        )
        query = query.filter(target_id__in=ids).select_related('target')
    rows = list(query)
    superseded = {row.supersedes_id for row in rows if row.supersedes_id}
    basis = (
        _basis(work)
        if packet_authorized
        else {'machine_id': work.machine_id, 'scope_ids': []}
    )
    tests = (
        _test_basis(work) if any(row.kind == 'test_acceptance' for row in rows) else []
    )
    result = [
        record_data(row, basis=basis, current=row.pk not in superseded, tests=tests)
        for row in rows
    ]
    return (
        result
        if packet_authorized
        else [_without_packet_sources(row) for row in result]
    )


def project(work, actor):
    """One overview projection with server-computed recording capabilities."""
    targets = _targets(work, actor) if actor else []
    with _packet_sources(work, actor):
        return _project(work, actor, targets)


def _project(work, actor, targets):
    """Project only packet sources authorized by the enclosing read guard."""
    try:
        live = (
            _change_actor(actor)
            if getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False)
            else None
        )
    except PermissionDenied:
        live = None
    packet = packet_of(work)
    gates = read_gates(packet)
    machine_authorized = work.machine_id in {target.pk for target in targets}
    physical_authorized = (
        machine_authorized and packet and packet.machine_id == work.machine_id
    )
    return {
        'rule': 'operational_authority.v1',
        'work_order_id': work.pk,
        'work_version': work.lifecycle_version,
        'context_token': _context(work, gates)[1],
        'commands': {
            'test_acceptance': bool(
                live and machine_authorized and live.has_perm('tasks.execute_workorder')
            ),
            'operating_permission': bool(
                live and machine_authorized and live.has_perm(OPERATIONS_PERMISSION)
            ),
            'observed_return': bool(
                live and machine_authorized and live.has_perm(OPERATIONS_PERMISSION)
            ),
            'service_state': bool(
                live and targets and live.has_perm(OPERATIONS_PERMISSION)
            ),
        },
        'gates': [
            {
                'id': gate.pk,
                'name': gate.name,
                'requires_second_person': gate.requires_second_person,
                'can_record': bool(
                    live and physical_authorized and _gate_permitted(live, gate)
                ),
            }
            # O24: a shared Work's packet gates belong to its machine's client.
            for gate in (gates if machine_authorized else [])
        ],
        'targets': [{'id': target.pk, 'name': target.name} for target in targets],
        'physical': records(work, physical=True, actor=actor) if packet else [],
        'operational': records(work, actor=actor),
        'telemetry': telemetry(work, actor) if actor else [],
    }


@transaction.atomic
def command(
    *,
    work_order_id,
    actor,
    expected_version,
    expected_context,
    idempotency_key,
    operation,
    fields,
    supersedes=None,
):
    """Reauthorize exact retries before replay; lock the existing Work version."""
    actor = _change_actor(actor)
    # Native repair start/close already lock Packet before Work. Keep that
    # order while serializing new authority against owner edits and deletion.
    from repair.models import RepairPacket
    from tasks.scope import scoped_work_orders

    RepairPacket.objects.select_for_update().filter(
        work_order_id=work_order_id, work_order__in=scoped_work_orders(actor)
    ).first()
    work = _locked_work_order(work_order_id)
    _require_scope(actor, work)
    if type(expected_version) is not int or expected_version < 1:
        raise ValidationError('A positive integer Work version is required.')
    if (
        not isinstance(expected_context, str)
        or len(expected_context) != 64
        or any(char not in '0123456789abcdef' for char in expected_context)
    ):
        raise ValidationError('The reviewed authority context token is required.')
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 128
    ):
        raise ValidationError(
            'A stable idempotency key is required (maximum 128 characters).'
        )
    if operation not in ('record', 'verify') or not isinstance(fields, dict):
        raise ValidationError('Unsupported authority command.')
    if set(fields) - set(RecordFields().fields):
        raise ValidationError('Unsupported authority fields.')
    if any(
        key in fields and (type(fields[key]) is not int or fields[key] < 1)
        for key in ('gate_id', 'target_id', 'binding_id')
    ):
        raise ValidationError('Record identities must be positive integers.')
    if supersedes is not None and (type(supersedes) is not int or supersedes < 1):
        raise ValidationError('A positive superseded record ID is required.')
    if operation == 'verify':
        if set(fields) != {'gate_id'} or supersedes is None:
            raise ValidationError('Verification requires a gate and exact record ID.')
        clean = fields
        physical = True
    else:
        validator = RecordFields(data=fields)
        validator.is_valid(raise_exception=True)
        clean = validator.validated_data
        physical = clean['kind'] in ('isolation', 'permit', 'access_window')
    gate = None
    if physical:
        if work.machine_id not in {target.pk for target in _targets(work, actor)}:
            raise PermissionDenied('The physical asset is no longer authorized.')
        gate = RepairPacketGate.objects.filter(
            pk=clean.get('gate_id'),
            packet__work_order=work,
            packet__machine_id=work.machine_id,
        ).first()
        if gate is None:
            raise ValidationError('Gate is unavailable in this Work.')
        if not _gate_permitted(actor, gate):
            raise PermissionDenied(
                'The gate requires an authorized maintenance recorder.'
            )
    else:
        permission = (
            'tasks.execute_workorder'
            if clean['kind'] == 'test_acceptance'
            else OPERATIONS_PERMISSION
        )
        if not actor.has_perm(permission):
            raise PermissionDenied(f'Missing required permission: {permission}')
        targets = {target.pk: target for target in _targets(work, actor)}
        target_id = clean.get('target_id', work.machine_id)
        if target_id not in targets or (
            clean['kind'] != 'service_state' and target_id != work.machine_id
        ):
            raise PermissionDenied('The recorded asset target is no longer authorized.')
    intent = {
        'operation': operation,
        'fields': fields,
        'supersedes': supersedes,
        'expected_version': expected_version,
        'expected_context': expected_context,
    }
    request_hash = _canonical_hash('operational_authority', actor, intent)
    replay = _replay_or_none(work, idempotency_key, request_hash)
    if replay:
        if not physical and not _packet_sources_authorized(work, actor):
            return {
                **replay.metadata,
                'record': _without_packet_sources(replay.metadata['record']),
            }
        return replay.metadata
    _require_version(work, expected_version)
    with _packet_sources(work, actor):
        checked, context_token = _context(work)
    if expected_context != context_token:
        raise StaleVersion(
            'Scope, tests or safety requirements changed; review the latest Work.'
        )
    model = GateAuthorityRecord if physical else OperationalAuthorityRecord
    prior = (
        model.objects.filter(pk=supersedes, work_order=work).first()
        if supersedes
        else None
    )
    if supersedes and (
        prior is None
        or model.objects.filter(supersedes=prior).exists()
        or (not physical and operation != 'verify' and prior.kind != clean['kind'])
        or (not physical and prior.target_id != target_id)
    ):
        raise StaleVersion(
            'The source record is unavailable or has already been superseded.'
        )
    if physical and prior and prior.gate_id != gate.pk:
        raise ValidationError('A correction must retain the same safety gate.')
    if operation == 'verify':
        if (
            not gate.requires_second_person
            or prior.verified_by_id
            or prior.recorded_by_id == actor.pk
        ):
            raise ValidationError(
                'An independent second person must verify an unverified record.'
            )
        values = {
            field.name: getattr(prior, field.name)
            for field in model._meta.fields
            if field.name
            not in (
                'id',
                'supersedes',
                'version',
                'verified_by',
                'verified_at',
                'recorded_at',
            )
        }
        row = model(
            **values,
            version=work.lifecycle_version + 1,
            supersedes=prior,
            recorded_at=prior.recorded_at,
            verified_by=actor,
            verified_at=timezone.now(),
        )
        row.save()
    else:
        kind = clean['kind']
        if clean['outcome'] not in OUTCOMES[kind]:
            raise ValidationError('Unsupported outcome for this record kind.')
        if prior and prior.kind != kind:
            raise ValidationError('Corrections must retain their record kind.')
        now = timezone.now()
        if clean['observed_at'] > now:
            raise ValidationError('An observation cannot be in the future.')
        until = clean.get('effective_until')
        if until and until <= clean['effective_from']:
            raise ValidationError('Expiry must be after effective start.')
        if (physical or kind == 'operating_permission') and until is None:
            raise ValidationError('This authority requires an explicit expiry.')
        common = {
            key: clean[key]
            for key in (
                'kind',
                'outcome',
                'statement',
                'source_reference',
                'observed_at',
                'effective_from',
            )
        }
        common.update(
            effective_until=until,
            work_order=work,
            version=work.lifecycle_version + 1,
            recorded_by=actor,
            supersedes=prior,
        )
        if physical:
            if any(
                key in clean
                for key in (
                    'target_id',
                    'binding_id',
                    'telemetry_observed_at',
                    'telemetry_token',
                    'duty',
                    'standby',
                    'redundancy',
                    'restrictions',
                )
            ):
                raise ValidationError(
                    'Physical records use their gate, not service-state fields.'
                )
            row = model(gate=gate, **common)
            row.basis = {
                **checked['basis'],
                'gate': next(
                    item for item in checked['gates'] if item['id'] == gate.pk
                ),
            }
        else:
            targets = {target.pk: target for target in _targets(work, actor)}
            target_id = clean.get('target_id', work.machine_id)
            if target_id not in targets or (
                kind != 'service_state' and target_id != work.machine_id
            ):
                raise ValidationError(
                    'Target is unavailable for this Work and record kind.'
                )
            if prior and prior.target_id != target_id:
                raise ValidationError('A correction must retain its asset target.')
            if 'gate_id' in clean:
                raise ValidationError('Operating records cannot modify a safety gate.')
            row = model(target_id=target_id, **common)
            row.basis = dict(checked['basis'])
            if kind == 'test_acceptance':
                row.basis['tests'] = checked['tests']
            if kind == 'service_state':
                row.details = {
                    key: clean.get(key) or None
                    for key in ('duty', 'standby', 'redundancy', 'restrictions')
                }
                if not any(row.details.values()):
                    raise ValidationError(
                        'Record at least one explicit duty, standby, redundancy or restriction fact.'
                    )
                reading = next(
                    (
                        item
                        for item in telemetry(work, actor)
                        if item['binding_id'] == clean.get('binding_id')
                        and item['target_id'] == target_id
                    ),
                    None,
                )
                if clean['outcome'] in ('confirmed', 'corrected'):
                    if (
                        not reading
                        or clean.get('telemetry_token') != reading['reading_token']
                        or not reading['observed_at']
                        or clean.get('telemetry_observed_at')
                        != serializers.DateTimeField().to_internal_value(
                            reading['observed_at']
                        )
                    ):
                        raise StaleVersion(
                            'The telemetry observation changed or is unavailable; review it again.'
                        )
                    raw_value = reading['value']
                    scalar = (
                        raw_value.get('value')
                        if isinstance(raw_value, dict)
                        else raw_value
                    )
                    usable = isinstance(scalar, (str, int, float, bool)) and not (
                        isinstance(scalar, str) and not scalar.strip()
                    )
                    if clean['outcome'] == 'confirmed' and (
                        not usable
                        or clean['telemetry_observed_at'] > now
                        or reading['quality'] != 'good'
                        or reading['stale']
                        or not reading['source_active']
                    ):
                        raise ValidationError(
                            'Stale, inactive or uncertain telemetry requires an explicit operator correction.'
                        )
                    row.basis['telemetry'] = reading
                    if clean['outcome'] == 'confirmed':
                        row.basis['telemetry_verification'] = 'reading_verified_current'
                elif any(
                    key in clean
                    for key in (
                        'binding_id',
                        'telemetry_observed_at',
                        'telemetry_token',
                    )
                ):
                    raise ValidationError(
                        'Use confirm or correct to associate telemetry.'
                    )
            elif any(
                key in clean
                for key in (
                    'binding_id',
                    'telemetry_observed_at',
                    'telemetry_token',
                    'duty',
                    'standby',
                    'redundancy',
                    'restrictions',
                )
            ):
                raise ValidationError(
                    'Service-state fields require a service-state record.'
                )
        row.save()
    work.lifecycle_version += 1
    work.save(update_fields=['lifecycle_version', 'updated_at'])
    with _packet_sources(work, actor):
        recorded = record_data(row)
    receipt = {
        'work_order_id': work.pk,
        'work_version': work.lifecycle_version,
        'idempotency_key': idempotency_key,
        'expected_context': expected_context,
        'record': recorded,
    }
    _append_result(
        work_order=work,
        actor=actor,
        command='operational_authority',
        event_type='authority_' + operation,
        from_status=work.lifecycle_status,
        to_status=work.lifecycle_status,
        reason='Attributed operational fact recorded',
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata=receipt,
    )
    return receipt
