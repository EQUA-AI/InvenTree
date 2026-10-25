"""Versioned service-report review on the native Work audit/command ledger."""

import hashlib
import json
import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.module_loading import import_string

from tasks.closeout_models import (
    CloseoutCapture,
    CloseoutEffect,
    CloseoutFieldDecision,
    CloseoutPartUsage,
    CloseoutReading,
    new_effect_key,
)
from tasks.permissions import require_permission
from tasks.services.closeout import CloseoutError
from tasks.services.closeout_capture import CAPTURE_CLOSEOUT, REVIEW_CLOSEOUT
from tasks.services.work_orders import (
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
    _require_version,
)

SUBMITTED = 'SERVICE_REPORT_SUBMITTED'
APPROVED = 'SERVICE_REPORT_APPROVED'
FIELDS = (
    'cause',
    'action',
    'result',
    'verification_summary',
    'downtime_minutes',
    'follow_up_required',
    'follow_up',
)


def digest(value):
    """Canonical content identity, including database decimals and timestamps."""
    return hashlib.sha256(
        json.dumps(
            value, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':')
        ).encode()
    ).hexdigest()


def normalized_account(account):
    """Use the completion serializer rather than a second validation dialect."""
    from tasks.workorder_serializers import CompleteCommandSerializer

    serializer = CompleteCommandSerializer(
        data={**account, 'expected_version': 1, 'idempotency_key': str(uuid.uuid4())}
    )
    serializer.is_valid(raise_exception=True)
    return {
        name: serializer.validated_data.get(
            name,
            None
            if name == 'downtime_minutes'
            else False
            if name == 'follow_up_required'
            else '',
        )
        for name in FIELDS
    }


def source_evidence(work_order):
    """Retain the evidence reviewed with the account, not just today's fields."""
    return json.loads(
        json.dumps(
            {
                'captures': list(
                    CloseoutCapture.objects
                    .filter(work_order=work_order)
                    .order_by('pk')
                    .values()
                ),
                'decisions': list(
                    CloseoutFieldDecision.objects
                    .filter(proposal__capture_revision__capture__work_order=work_order)
                    .order_by('pk')
                    .values()
                ),
                'parts': list(
                    CloseoutPartUsage.objects
                    .filter(work_order=work_order)
                    .order_by('pk')
                    .values()
                ),
                'readings': list(
                    CloseoutReading.objects
                    .filter(work_order=work_order)
                    .order_by('pk')
                    .values()
                ),
                'alarms': list(
                    work_order.anomalies.order_by('pk').values(
                        'id', 'machine_id', 'fingerprint', 'detector', 'status'
                    )
                ),
            },
            cls=DjangoJSONEncoder,
        )
    )


def source_hash(work_order):
    """Any reviewed source change invalidates the exact report approval."""
    return digest(source_evidence(work_order))


def latest_report(work_order):
    """A resubmission supersedes, but never overwrites, previous reports."""
    return work_order.events.filter(event_type=SUBMITTED).order_by('-pk').first()


def metadata(event):
    """Return the native event result envelope."""
    return event.metadata.get('result_metadata', {}) if event else {}


def approval_for(work_order, submission):
    """Approval names an exact retained report, not merely a Work identity."""
    if not submission:
        return None
    return (
        work_order.events
        .filter(event_type=APPROVED, metadata__result_metadata__report_id=submission.pk)
        .order_by('-pk')
        .first()
    )


def test_work(work_order):
    """Only the explicit UI test detector participates in automatic recovery."""
    from machine_health.services.test_alarms import DETECTOR

    return work_order.anomalies.filter(detector=DETECTOR).exists()


def report_state(work_order):
    """Scope is enforced by the parent API before reading retained content."""
    submission = latest_report(work_order)
    approval = approval_for(work_order, submission)
    completed = work_order.lifecycle_status == 'completed'
    current = bool(submission) and (
        completed
        or (
            metadata(submission).get('source_hash') == source_hash(work_order)
            and work_order.lifecycle_version
            == (approval or submission).metadata['lifecycle_version']
        )
    )
    closeout = getattr(work_order, 'structured_closeout', None)
    from assets.health_models import (
        ACTIVE_ANOMALY_STATUSES,
        AnomalyStatus,
        MachineAnomaly,
    )
    from machine_health.services.test_alarms import DETECTOR

    tests = list(work_order.anomalies.filter(detector=DETECTOR).values('id', 'status'))
    other_alarms = (
        MachineAnomaly.objects
        .filter(machine_id=work_order.machine_id, status__in=ACTIVE_ANOMALY_STATUSES)
        .exclude(detector=DETECTOR)
        .count()
        if tests and work_order.machine_id
        else 0
    )
    effects = list(
        CloseoutEffect.objects
        .filter(closeout__work_order=work_order)
        .filter(effect_type__in=['sap_writeback', 'test_recovery'])
        .values('effect_type', 'status', 'result_reference', 'last_error')
    )
    return {
        'required': test_work(work_order)
        or bool(getattr(settings, 'AIMMS_SERVICE_REPORT_APPROVAL_REQUIRED', False)),
        'test_alarm': test_work(work_order),
        'test_recovery': {
            'total': len(tests),
            'active': sum(row['status'] in ACTIVE_ANOMALY_STATUSES for row in tests),
            'pending': sum(
                row['status'] in (*ACTIVE_ANOMALY_STATUSES, AnomalyStatus.SUPPRESSED)
                for row in tests
            ),
            'other_active_alarms': other_alarms,
        },
        'status': 'not_submitted'
        if not submission
        else 'stale'
        if not current
        else 'approved'
        if approval
        else 'submitted',
        'report_id': submission.pk if submission else None,
        'report': metadata(submission).get('document'),
        'content_hash': metadata(submission).get('content_hash'),
        'submitted_by': submission.actor_id if submission else None,
        'submitted_by_name': (
            submission.actor.get_full_name().strip() or submission.actor.get_username()
        )
        if submission and submission.actor
        else None,
        'approved_by': approval.actor_id if approval else None,
        'approved_by_name': (
            approval.actor.get_full_name().strip() or approval.actor.get_username()
        )
        if approval and approval.actor
        else None,
        'verification_enabled': getattr(settings, 'AIMMS_CLOSEOUT_VERIFY_POLICY', 'off')
        in ('optional', 'supervisor'),
        'verified': bool(closeout and closeout.verified_by_id),
        'sap_configured': bool(getattr(settings, 'AIMMS_SAP_WRITEBACK_ADAPTER', None)),
        'effects': effects,
        'version': work_order.lifecycle_version,
    }


def require_approved_report(work_order, account, capture_id):
    """Completion cannot bypass a required or voluntarily submitted report."""
    submission = latest_report(work_order)
    required = test_work(work_order) or getattr(
        settings, 'AIMMS_SERVICE_REPORT_APPROVAL_REQUIRED', False
    )
    if not submission and not required:
        return
    state = report_state(work_order)
    document = state['report'] or {}
    if (
        state['status'] != 'approved'
        or document.get('account') != normalized_account(account)
        or document.get('capture_id') != capture_id
    ):
        raise CloseoutError(
            'Submit and independently approve this exact service report first.'
        )


def queue_effect(closeout, effect_type):
    """Persist intent before any provider dispatch, using the existing worker."""
    from tasks.services.closeout_effects import execute_pending_effects

    CloseoutEffect.objects.get_or_create(
        effect_key=new_effect_key(closeout.pk, effect_type),
        defaults={
            'closeout': closeout,
            'effect_type': effect_type,
            'payload_hash': closeout.content_hash,
        },
    )
    if getattr(settings, 'AIMMS_CLOSEOUT_EFFECTS_ENABLED', False):
        transaction.on_commit(lambda: execute_pending_effects(closeout_id=closeout.pk))


def equipment_return_url(work_order):
    """Focus the native reference position only when a real component maps it."""
    from urllib.parse import urlencode

    from assets.equipment_positions import CATALOGUE_PREFIX, POSITION_FAMILIES
    from assets.registry_models import AssetComponent
    from machine_health.services.test_alarms import DETECTOR

    if not work_order.machine_id:
        return None
    url = f'/machines/machine/{work_order.machine_id}/equipment/'
    if work_order.machine.asset_type != 'pump':
        return url
    component_ids = {
        anomaly.metrics.get('component_id')
        for anomaly in work_order.anomalies.filter(detector=DETECTOR)
        if anomaly.metrics.get('component_id')
    }
    if len(component_ids) == 1:
        component = (
            AssetComponent.objects
            .select_related('part')
            .filter(pk__in=component_ids, machine_id=work_order.machine_id)
            .first()
        )
        if component:
            for position, family in POSITION_FAMILIES.items():
                if family and f'{CATALOGUE_PREFIX}{family}' == component.part.IPN:
                    return f'{url}?{urlencode({"view": "model", "position": position})}'
    return url


@transaction.atomic
def service_report_command(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    action,
    account=None,
    capture_id=None,
    report_id=None,
    reason='',
    confirmed=False,
):
    """Submit, approve, dispatch or verify using native role and replay gates."""
    from machine_health.services.test_alarms import recover_test_alarms
    from tasks.scope import enforce_machine_scope
    from tasks.services.closeout_amend import verify_closeout

    work_order = _locked_work_order(work_order_id)
    _require_scope(actor, work_order)
    permissions = {
        'submit': CAPTURE_CLOSEOUT,
        'approve': REVIEW_CLOSEOUT,
        'sap': 'tasks.verify_closeout',
        'return': 'tasks.verify_closeout',
        'end_test': 'tasks.verify_closeout',
    }
    if action not in permissions:
        raise CloseoutError('Unknown service-report command.')
    require_permission(actor, permissions[action])
    if action == 'end_test' and (
        not actor.is_superuser or not confirmed or len(reason.strip()) < 5
    ):
        raise CloseoutError(
            'Only an administrator can explicitly end a test exercise with a reason.'
        )
    request_hash = _canonical_hash(
        'service_report',
        actor,
        {
            'action': action,
            'account': account,
            'capture_id': capture_id,
            'report_id': report_id,
            'expected_version': expected_version,
            'reason': reason,
            **({'confirmed': confirmed} if action == 'end_test' else {}),
        },
    )
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay
    _require_version(work_order, expected_version)
    result = {}
    if action == 'end_test':
        if not test_work(work_order):
            raise CloseoutError('This Work has no synthetic test exercise.')
        if work_order.machine_id:
            enforce_machine_scope(actor, work_order.machine)
        resolved = recover_test_alarms(
            work_order, actor, kind='ended_test', reason=reason.strip()
        )
        if not resolved:
            raise CloseoutError('This Work has no active synthetic test exercise.')
        transaction.on_commit(
            lambda: notify_test_ended(work_order, resolved), robust=True
        )
        result = {
            'resolved_test_alarms': resolved,
            'equipment_url': equipment_return_url(work_order),
            'work_completed': False,
        }
    elif action == 'submit':
        if work_order.lifecycle_status not in ('in_progress', 'verifying'):
            raise CloseoutError(
                'Submit a service report during execution or verification.'
            )
        account = normalized_account(account or {})
        if capture_id is not None:
            from tasks.services.closeout import _validated_capture

            _validated_capture(work_order, capture_id, account)
        state = report_state(work_order)
        if (
            state['status'] == 'approved'
            and state['report']['account'] == account
            and state['report']['capture_id'] == capture_id
        ):
            raise CloseoutError(
                'This exact service report is already approved. Complete Work or edit the report before resubmitting.'
            )
        document = {
            'schema': 'service_report.v1',
            'work_order_id': work_order.pk,
            'reference': work_order.reference,
            'title': work_order.title,
            'machine_id': work_order.machine_id,
            'machine_name': work_order.machine.name if work_order.machine_id else None,
            'account': account,
            'capture_id': capture_id,
            'evidence': source_evidence(work_order),
        }
        result = {
            'document': document,
            'content_hash': digest(document),
            'source_hash': source_hash(work_order),
        }
    else:
        submission = latest_report(work_order)
        if not submission or submission.pk != report_id:
            raise CloseoutError('Select the latest submitted service report.')
        state = report_state(work_order)
        if action == 'approve':
            if work_order.lifecycle_status not in ('in_progress', 'verifying'):
                raise CloseoutError(
                    'Completed reports cannot be approved retrospectively.'
                )
            if state['status'] != 'submitted':
                raise CloseoutError(
                    'The report changed or was already approved; review the latest report.'
                )
            if submission.actor_id == actor.pk:
                raise CloseoutError(
                    'A different reviewer must approve the service report.'
                )
            result = {
                'report_id': submission.pk,
                'content_hash': metadata(submission)['content_hash'],
            }
        else:
            if (
                work_order.lifecycle_status != 'completed'
                or state['status'] != 'approved'
            ):
                raise CloseoutError('Complete the approved service report first.')
            closeout = work_order.structured_closeout
            if action == 'sap':
                if not getattr(settings, 'AIMMS_SAP_WRITEBACK_ADAPTER', None):
                    raise CloseoutError('SAP writeback is not configured.')
                queue_effect(closeout, 'sap_writeback')
                result = {'sap': 'queued'}
            else:
                retained_alarms = {
                    row['id']
                    for row in metadata(submission)['document']['evidence']['alarms']
                }
                if work_order.anomalies.exclude(pk__in=retained_alarms).exists():
                    raise CloseoutError(
                        'New alarm links require a separate reviewed work order.'
                    )
                if not closeout.verified_by_id:
                    verify_closeout(
                        work_order_id=work_order.pk,
                        actor=actor,
                        expected_version=expected_version,
                        idempotency_key=f'service-report-verify:{digest([work_order.pk, idempotency_key])}',
                    )
                if work_order.machine_id:
                    enforce_machine_scope(actor, work_order.machine)
                resolved = recover_test_alarms(work_order, actor)
                if resolved:
                    queue_effect(closeout, 'test_recovery')
                result = {
                    'resolved_test_alarms': resolved,
                    'equipment_url': equipment_return_url(work_order),
                }
    work_order.lifecycle_version += 1
    work_order.save(update_fields=['lifecycle_version'])
    return _append_result(
        work_order=work_order,
        actor=actor,
        command=f'service_report_{action}',
        event_type={
            'submit': SUBMITTED,
            'approve': APPROVED,
            'sap': 'SERVICE_REPORT_SAP_QUEUED',
            'return': 'SERVICE_REPORT_RETURNED',
            'end_test': 'TEST_EXERCISE_ENDED',
        }[action],
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason=reason,
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata=result,
    )


def notify_test_ended(work_order, resolved):
    """Announce an ended simulation, never a verified repair or service return."""
    from common.notifications import trigger_notification
    from InvenTree.helpers import pui_url
    from InvenTree.helpers_model import construct_absolute_url
    from machine_health.services.alarm_notifications import recipients_for

    trigger_notification(
        work_order,
        'tasks.test_exercise_ended',
        check_recent=False,
        targets=recipients_for(work_order.machine),
        context={
            'name': f'TEST exercise ended: {work_order.reference}',
            'message': 'The synthetic test was ended without recording a repair. Work status is unchanged; actual telemetry and unrelated alarms are unchanged.',
            'link': construct_absolute_url(pui_url(equipment_return_url(work_order))),
            'template': {
                'html': 'email/test_alarm_ended.html',
                'subject': f'TEST exercise ended: {work_order.reference}',
            },
            'work_order': work_order,
            'resolved': resolved,
        },
    )


def execute_sap_writeback(effect):
    """A trusted deployment adapter must return a real provider acknowledgement."""
    from tasks.services.accepted_reports import canonical_export
    from tasks.services.closeout_effects import (
        EffectOutcomeUnknown,
        EffectRetryableError,
    )

    adapter = getattr(settings, 'AIMMS_SAP_WRITEBACK_ADAPTER', None)
    if not adapter:
        raise EffectRetryableError('SAP writeback is not configured.')
    if isinstance(adapter, str):
        adapter = import_string(adapter)
    report = effect.closeout.reports.order_by('revision').first()
    if not report:
        raise EffectRetryableError('No frozen completion report is available.')
    try:
        acknowledgement = adapter(
            report=json.loads(canonical_export(report)),
            idempotency_key=effect.effect_key,
        )
    except EffectRetryableError:
        raise
    except Exception as exc:
        raise EffectOutcomeUnknown(
            'SAP dispatch outcome needs provider reconciliation.'
        ) from exc
    if (
        not isinstance(acknowledgement, dict)
        or acknowledgement.get('status') != 'acknowledged'
        or not acknowledgement.get('reference')
    ):
        raise EffectOutcomeUnknown('SAP did not return a verifiable acknowledgement.')
    return str(acknowledgement['reference'])[:255]


def execute_test_recovery_notification(effect):
    """Announce a verified test recovery without declaring plant readings normal."""
    from common.notifications import trigger_notification
    from InvenTree.helpers import pui_url
    from InvenTree.helpers_model import construct_absolute_url
    from machine_health.services.alarm_notifications import recipients_for

    work_order = effect.closeout.work_order
    if not effect.closeout.verified_by_id:
        raise CloseoutError('Recovery notification requires supervisor verification.')
    recipients = recipients_for(work_order.machine)
    if not recipients:
        raise CloseoutError(
            'No authorized recovery notification recipients are configured.'
        )
    name = f'TEST alarm cleared: {work_order.machine.name}'
    trigger_notification(
        work_order,
        'tasks.test_alarm_recovered',
        targets=recipients,
        context={
            'name': name,
            'message': 'Service report approved and closeout verified. The synthetic test alarm '
            'has returned to normal. Actual telemetry and unrelated alarms are unchanged.',
            'link': construct_absolute_url(
                pui_url(f'/machines/machine/{work_order.machine_id}/')
            ),
            'template': {'html': 'email/test_alarm_recovered.html', 'subject': name},
            'work_order': work_order,
            'report_link': construct_absolute_url(
                pui_url(
                    f'/maintenance/work-orders/{work_order.pk}/?work_panel=closeout'
                )
            ),
        },
    )
    return f'notification requested:{len(recipients)}'
