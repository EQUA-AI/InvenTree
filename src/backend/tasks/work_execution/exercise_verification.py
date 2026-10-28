"""Strict exercise-only projection; never writes operating or release authority."""

import hashlib
from decimal import Decimal, InvalidOperation

from django.db.models import Q

from common.models import Attachment
from repair.intake_scenarios import prepared_scenario
from repair.pump08_exercise import (
    DEFAULT_EVIDENCE_PROFILE,
    EVIDENCE,
    EVIDENCE_PROFILES,
    PROCEDURE_CODE,
    STEP_DEFINITIONS,
    evidence_bytes,
)
from tasks.models import ProcedureRevision, WorkOrderProcedureApplication
from tasks.permissions import APPLY_PROCEDURE, EXECUTE_WORKORDER
from tasks.services.procedure_execution import (
    _application_snapshot,
    _snapshot_hash,
    _validate_evidence,
)

from .common import packet_of, person


def exercise_source(work_order):
    """Require one actual new source, its owning packet and the configured channel."""
    packet = packet_of(work_order)
    if (
        packet is None
        or packet.machine_id != work_order.machine_id
        or work_order.work_order_type != 'corrective'
    ):
        return None
    machine = work_order.machine
    if machine is None or machine.source_key != 'P8' or machine.asset_type != 'pump':
        return None
    sources = list(work_order.anomalies.all()[:2])
    if len(sources) != 1:
        return None
    source = sources[0]
    if source.machine_id != machine.pk or source.repair_packet_id != packet.pk:
        return None
    binding = (
        source.bindings
        .filter(pk=source.metrics.get('binding_id'), machine=machine, active=True)
        .select_related('dictionary_point__component')
        .first()
    )
    point = binding.dictionary_point if binding else None
    component = point.component if point and point.component_id else None
    if (
        component is None
        or point.machine_id != machine.pk
        or component.machine_id != machine.pk
        or source.metrics.get('component_id') != component.pk
    ):
        return None
    target = {'name': binding.display_name, 'component_code': component.code}
    if prepared_scenario(machine, source, target) is None:
        return None
    created = packet.events.filter(event_type='created').order_by('pk').first()
    if (
        created is None
        or (created.metadata.get('source') or {}).get('anomaly_id') != source.pk
    ):
        return None
    return source


def _evidence_registry(work_order, *, codes=None):
    packet = packet_of(work_order)
    event = (
        work_order.events
        .filter(event_type='EXERCISE_EVIDENCE_ATTACHED')
        .order_by('-pk')
        .first()
    )
    if event is None or event.actor_id is None:
        return {}
    metadata = event.metadata.get('result_metadata', {})
    profile = metadata.get('profile', DEFAULT_EVIDENCE_PROFILE)
    if profile not in EVIDENCE_PROFILES:
        return {}
    records = metadata.get('evidence', {})
    if not isinstance(records, dict):
        return {}
    result = {}
    for code, record in records.items():
        if codes is not None and code not in codes:
            continue
        if code not in EVIDENCE or not isinstance(record, dict):
            continue
        attachment = (
            Attachment.objects
            .filter(pk=record.get('id'), upload_user_id=event.actor_id)
            .filter(
                Q(model_type='kanbancard', model_id=work_order.pk)
                | Q(model_type='repairpacket', model_id=packet.pk)
            )
            .first()
        )
        if attachment is None or not attachment.attachment:
            continue
        try:
            with attachment.attachment.open('rb') as stored:
                content = stored.read(8193)
        except Exception:
            continue
        digest = hashlib.sha256(content).hexdigest()
        if digest != record.get('sha256') or content != evidence_bytes(
            code, profile=profile
        ):
            continue
        result[code] = {'id': attachment.pk, 'label': code, 'prepared': True}
    return result


def _recorded_readings(work_order, execution, application, evidence_id, step_event):
    """Read both separately audited inputs bound to the current step version."""
    summary = (
        work_order.events
        .filter(
            event_type='EXERCISE_READINGS_RECORDED',
            metadata__step_execution_id=execution.pk,
            metadata__version=execution.version,
        )
        .order_by('-pk')
        .first()
    )
    if (
        not summary
        or not step_event
        or summary.actor_id != execution.completed_by_id
        or summary.correlation_id != step_event.correlation_id
        or summary.created_at < step_event.created_at
    ):
        return None
    metadata = summary.metadata
    identities = metadata.get('reading_event_ids')
    if (
        metadata.get('application_id') != application.pk
        or metadata.get('step_key') != str(execution.step_key)
        or metadata.get('evidence_id') != evidence_id
        or not isinstance(identities, dict)
        or set(identities) != {'W5', 'W6'}
        or any(type(identity) is not int for identity in identities.values())
        or len(set(identities.values())) != 2
    ):
        return None
    readings = {}
    for channel, identity in identities.items():
        event = work_order.events.filter(
            pk=identity, event_type='EXERCISE_READING_RECORDED'
        ).first()
        if (
            event is None
            or event.actor_id != execution.completed_by_id
            or event.correlation_id != summary.correlation_id
            or event.created_at < execution.completed_at
            or event.created_at > summary.created_at
            or event.metadata.get('application_id') != application.pk
            or event.metadata.get('step_execution_id') != execution.pk
            or event.metadata.get('step_key') != str(execution.step_key)
            or event.metadata.get('version') != execution.version
            or event.metadata.get('channel') != channel
            or event.metadata.get('evidence_id') != evidence_id
        ):
            return None
        try:
            value = Decimal(str(event.metadata['value']))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None
        if not value.is_finite():
            return None
        readings[channel] = value
    try:
        maximum = Decimal(str(metadata['maximum']))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None
    if not maximum.is_finite() or maximum != max(readings.values()):
        return None
    return {
        'W5': str(readings['W5']),
        'W6': str(readings['W6']),
        'maximum': str(maximum),
    }


def project(work_order, actor):
    """Evaluate current native results and retained evidence, with no mutation."""
    source = exercise_source(work_order)
    if source is None:
        recorded = work_order.events.filter(
            event_type='WORK_PACKAGE_CREATED',
            metadata__result_metadata__exercise_profile=PROCEDURE_CODE,
        ).first()
        if recorded is None:
            return {'applicable': False}
        return {
            'applicable': True,
            'work_order_id': work_order.pk,
            'profile': PROCEDURE_CODE,
            'approved': False,
            'passed_count': 0,
            'required_count': 3,
            'source': {
                'anomaly_id': recorded.metadata['result_metadata'].get(
                    'source_anomaly_id'
                ),
                'status': 'unavailable',
            },
            'can_record': False,
            'can_apply': False,
            'evidence': [],
            'checks': [],
            'application_id': None,
            'published_revision_id': None,
            'reasons': ['source_or_ownership_invalid'],
            'grants_operating_permission': False,
            'grants_technical_acceptance': False,
            'provenance': 'prepared_exercise_measurements_only',
        }
    result = {
        'applicable': True,
        'approved': False,
        'passed_count': 0,
        'required_count': 3,
        'work_order_id': work_order.pk,
        'profile': PROCEDURE_CODE,
        'source': {'anomaly_id': source.pk, 'status': source.status},
        'can_record': bool(
            actor is not None
            and work_order.is_active
            and actor.has_perm(EXECUTE_WORKORDER)
        ),
        'can_apply': bool(
            actor is not None
            and work_order.is_active
            and actor.has_perm(APPLY_PROCEDURE)
        ),
        'provenance': 'prepared_exercise_measurements_only',
        'grants_operating_permission': False,
        'grants_technical_acceptance': False,
        'evidence': list(_evidence_registry(work_order).values()),
        'checks': [],
        'reasons': [],
        'application_id': None,
        'published_revision_id': None,
    }
    candidates = ProcedureRevision.objects.filter(
        procedure__code=PROCEDURE_CODE,
        procedure__client_id=work_order.machine.client_id,
        procedure__customer__isnull=True,
        procedure__active=True,
        status='published',
        work_order_type='corrective',
    ).select_related('procedure')
    published = [
        revision
        for revision in candidates
        if revision.procedure.current_revision_id == revision.pk
    ]
    if len(published) == 1:
        result['published_revision_id'] = published[0].pk
    application = (
        WorkOrderProcedureApplication.objects
        .filter(work_order=work_order, primary=True)
        .select_related('revision__procedure')
        .first()
    )
    if application is None:
        result['reasons'].append('published_procedure_not_applied')
        return result
    result['application_id'] = application.pk
    revision = application.revision
    snapshot_steps = application.snapshot.get('steps') or []
    if not isinstance(snapshot_steps, list):
        snapshot_steps = []
    executions = list(application.step_executions.order_by('sequence', 'pk'))
    revision_ok = (
        len(published) == 1
        and published[0].pk == revision.pk
        and application.snapshot.get('revision_id') == revision.pk
        and application.snapshot_hash == _snapshot_hash(application.snapshot)
        and application.snapshot == _application_snapshot(revision)
        and application.drift_status == 'current'
        and application.snapshot.get('revision', {}).get('content_hash')
        == revision.content_hash
        and len(snapshot_steps) == len(executions) == 3
        and application.snapshot.get('scope', {}).get('client_id')
        == work_order.machine.client_id
    )
    if not revision_ok:
        result['reasons'].append('procedure_revision_or_snapshot_invalid')
    registry = {row['label']: row['id'] for row in result['evidence']}
    for expected in STEP_DEFINITIONS:
        rows = [
            execution
            for execution in executions
            if execution.sequence == expected['sequence']
        ]
        execution = rows[0] if len(rows) == 1 else None
        check = {
            'sequence': expected['sequence'],
            'title': expected['title'],
            'value_type': expected['value_type'],
            'unit': expected['unit'],
            'passed': False,
            'recorded': False,
            'value': None,
            'step_key': None,
            'version': None,
            'recorded_by': None,
            'recorded_at': None,
            'evidence_ids': [],
            'suggested_evidence_ids': [
                registry[code]
                for code in expected['evidence_codes']
                if code in registry
            ],
            'prepared_note': expected['note'],
            'note': '',
            'reasons': [],
        }
        if execution is None:
            check['reasons'].append('step_missing')
            result['checks'].append(check)
            continue
        snapshot = (
            execution.step_snapshot if isinstance(execution.step_snapshot, dict) else {}
        )
        matched = [
            step
            for step in snapshot_steps
            if isinstance(step, dict)
            and str(step.get('key')) == str(execution.step_key)
        ]
        criteria_ok = (
            len(matched) == 1
            and matched[0] == snapshot
            and snapshot.get('title') == expected['title']
            and snapshot.get('step_type') == 'verification'
            and snapshot.get('required') is True
            and snapshot.get('value_type') == expected['value_type']
            and bool((snapshot.get('evidence_policy') or {}).get('required'))
            and snapshot.get('min_value') is None
        )
        raw = execution.value
        numeric = expected['value_type'] == 'number'
        step_event = (
            work_order.events
            .filter(
                event_type__in=['STEP_COMPLETED', 'STEP_FAILED'],
                metadata__step_execution_id=execution.pk,
                metadata__version=execution.version,
            )
            .order_by('-pk')
            .first()
        )
        value = (
            raw.get('number' if numeric else 'boolean')
            if isinstance(raw, dict)
            else raw
        )
        if numeric:
            try:
                criteria_ok = (
                    criteria_ok
                    and Decimal(str(snapshot.get('max_value'))) == Decimal('124.9')
                    and snapshot.get('unit') in {'C', '°C', 'degC'}
                )
            except (InvalidOperation, TypeError, ValueError):
                criteria_ok = False
            readings = None
            try:
                number = (
                    Decimal(str(value))
                    if not isinstance(value, bool)
                    else Decimal('NaN')
                )
                if execution.completed_at and registry.get('P08-TEMP-E06'):
                    readings = _recorded_readings(
                        work_order,
                        execution,
                        application,
                        registry['P08-TEMP-E06'],
                        step_event,
                    )
                value_ok = (
                    number.is_finite()
                    and readings is not None
                    and Decimal(readings['W5']) == Decimal('94.0')
                    and Decimal(readings['W6']) == Decimal('96.0')
                    and number == Decimal(readings['maximum'])
                    and number < Decimal('125.0')
                )
            except (InvalidOperation, TypeError, ValueError):
                value_ok = False
        else:
            criteria_ok = criteria_ok and snapshot.get('max_value') is None
            value_ok = value is True
        event = (
            step_event
            if step_event and step_event.event_type == 'STEP_COMPLETED'
            else None
        )
        evidence_ids = event.metadata.get('evidence_ids', []) if event else []
        if not isinstance(evidence_ids, list) or any(
            type(identity) is not int for identity in evidence_ids
        ):
            evidence_ids = []
        evidence_ok = bool(evidence_ids) and all(
            registry.get(code) in evidence_ids for code in expected['evidence_codes']
        )
        try:
            _validate_evidence(work_order, execution, evidence_ids)
        except Exception:
            evidence_ok = False
        recorded = bool(
            execution.status == 'completed'
            and execution.passed is True
            and execution.completed_by_id
            and execution.completed_at
            and execution.completed_at >= application.applied_at
            and event
            and event.actor_id == execution.completed_by_id
            and event.created_at >= application.applied_at
            and event.created_at >= execution.completed_at
            and event.metadata.get('application_id') == application.pk
            and event.metadata.get('step_key') == str(execution.step_key)
            and event.metadata.get('note') == execution.note
        )
        check.update({
            'step_key': str(execution.step_key),
            'version': execution.version,
            'value': value,
            'recorded': execution.completed_at is not None,
            'recorded_by': person(execution.completed_by),
            'recorded_at': execution.completed_at.isoformat()
            if execution.completed_at
            else None,
            'evidence_ids': evidence_ids,
            'note': execution.note,
            'passed': bool(
                revision_ok and criteria_ok and value_ok and evidence_ok and recorded
            ),
        })
        if numeric:
            check['readings'] = readings
        if not criteria_ok:
            check['reasons'].append('criteria_invalid')
        if not recorded:
            check['reasons'].append('current_pass_not_recorded')
        if not value_ok:
            check['reasons'].append('value_does_not_match_prepared_evidence')
        if not evidence_ok:
            check['reasons'].append('required_prepared_evidence_missing')
        result['checks'].append(check)
    result['passed_count'] = sum(check['passed'] for check in result['checks'])
    result['approved'] = revision_ok and result['passed_count'] == 3
    return result
