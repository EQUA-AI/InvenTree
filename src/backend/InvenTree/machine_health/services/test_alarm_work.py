"""Atomic synthetic alarm and native planned work creation, never physical release."""

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from tasks.scope import scoped_work_orders
from tasks.services import scheduling

from repair.intake_scenarios import prepared_target_scenario
from repair.work_intake import assignment_candidate
from repair.work_packages import (
    WorkPackageError,
    create_repair_work_package,
    find_duplicate_repairs,
)

from .test_alarms import targets, trigger_test_alarm

PLAN_SALT = 'machine-health.test-alarm-work-plan.v1'


def work_plan(*, actor, machine, binding_id, active_alarm=None):
    """Preview the exact server-prepared scope and visible duplicate work."""
    if not getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False):
        raise PermissionDenied('Work orders are disabled in this deployment.')
    machine = scheduling._authorize_creation(actor, machine.pk)
    target = next(
        (row for row in targets(machine) if row['binding_id'] == binding_id), None
    )
    owned_alarm = bool(
        active_alarm is not None
        and active_alarm.machine_id == machine.pk
        and active_alarm.metrics.get('binding_id') == binding_id
        and active_alarm.bindings.filter(pk=binding_id, machine=machine).exists()
    )
    if target is None or not (
        target['eligible']
        or (owned_alarm and target['ineligible_reason'] == 'existing_test')
    ):
        raise WorkPackageError(
            'Select an eligible mapped signal before preparing work.'
        )
    scenario = prepared_target_scenario(machine, target)
    if scenario is None:
        scenario = {
            'key': 'mapped_signal_investigation_v1',
            'label': 'Mapped-parameter investigation',
            'provenance': 'prepared_investigation_not_ai_output',
            'fields': {
                'title': f'{machine.name} | {target["name"]} test alarm'[:200],
                'work_scope': (
                    f'Investigate the synthetic high indication for {target["name"]} '
                    f'on {machine.name}, component {target["component_name"] or "pump-level"}. '
                    'Validate signal mapping, timestamp, measurement quality and applicable limits. '
                    'Review operating context and peer channels before proposing corrective work. '
                    'Record findings and verification evidence under approved procedures. '
                    'A synthetic indication does not establish a physical fault or operating permission.'
                ),
                'symptom': f'Synthetic high indication: {target["name"]}.'[:255],
                'production_impact': 'Exercise only; actual production impact not established.',
            },
            'suggested_owner_username': None,
            'priority': 'high',
            'criticality': 'critical',
            'work_order_type': 'corrective',
            'parts': [],
            'limitations': 'Prepared investigation, not AI output. No diagnosis, parts, physical work or service release is established.',
        }
    duplicates = find_duplicate_repairs(machine)
    visible = set(
        scoped_work_orders(actor)
        .filter(pk__in=[row['work_order_id'] for row in duplicates])
        .values_list('pk', flat=True)
    )
    if any(row['work_order_id'] not in visible for row in duplicates):
        raise WorkPackageError('Existing work needs review by an authorized planner.')
    return {
        'scenario': scenario,
        'duplicates': duplicates,
        'plan_token': signing.dumps(
            {
                'actor_id': actor.pk,
                'machine_id': machine.pk,
                'binding_id': binding_id,
                'component_id': target['component_id'],
                'scenario': scenario,
            },
            salt=PLAN_SALT,
            compress=True,
        ),
    }


@transaction.atomic
def trigger_with_work(
    *,
    actor,
    machine,
    plan_token=None,
    assigned_to_id=None,
    event_owned=False,
    duplicate_override=False,
    duplicate_override_reason='',
    **alarm,
):
    """Commit alarm, ownership, packet and primary card together, or none of them."""
    if not getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False):
        raise PermissionDenied('Work orders are disabled in this deployment.')
    if event_owned and not getattr(
        settings, 'AIMMS_TEST_ALARM_EVENT_WORK_ENABLED', False
    ):
        raise PermissionDenied('Event-owned test work is disabled in this deployment.')
    if event_owned and (plan_token or assigned_to_id or 'create_work_order' in alarm):
        raise WorkPackageError(
            'Event-owned work does not accept manual planning overrides.'
        )
    if event_owned and duplicate_override and not duplicate_override_reason.strip():
        raise WorkPackageError('Explain why a separate work order is needed.')
    if event_owned and not duplicate_override and duplicate_override_reason.strip():
        raise WorkPackageError(
            'A separate work order reason requires duplicate review.'
        )
    machine = scheduling._authorize_creation(actor, machine.pk, lock=True)
    if event_owned:
        from assets.health_models import MachineSignalBinding
        from assets.registry_models import AssetComponent, DictionaryPoint

        binding = (
            MachineSignalBinding.objects
            .select_for_update()
            .filter(pk=alarm['binding_id'], machine=machine)
            .first()
        )
        if binding and binding.dictionary_point_id:
            point = (
                DictionaryPoint.objects
                .select_for_update()
                .filter(pk=binding.dictionary_point_id, machine=machine)
                .first()
            )
            if point and point.component_id:
                list(
                    AssetComponent.objects
                    .select_for_update()
                    .filter(pk=point.component_id, machine=machine)
                    .values_list('pk', flat=True)
                )
    context = (
        {
            'create_work_order': True,
            'event_owned': True,
            'duplicate_override': duplicate_override,
            'duplicate_override_reason': duplicate_override_reason,
        }
        if event_owned
        else {
            'create_work_order': True,
            'plan_token': plan_token,
            'assigned_to_id': assigned_to_id,
            'duplicate_override': duplicate_override,
            'duplicate_override_reason': duplicate_override_reason,
        }
    )
    try:
        anomaly = trigger_test_alarm(
            machine=machine, actor=actor, command_context=context, **alarm
        )
    except ValidationError as exc:
        if not (
            event_owned
            and not duplicate_override
            and not duplicate_override_reason
            and exc.messages == ['This request key belongs to another selection.']
        ):
            raise
        anomaly = trigger_test_alarm(
            machine=machine,
            actor=actor,
            command_context={'create_work_order': True, 'event_owned': True},
            **alarm,
        )
    receipt = anomaly.metrics.get('automatic_work_order')
    if receipt is not None:
        if not scoped_work_orders(actor).filter(pk=receipt['work_order_id']).exists():
            raise WorkPackageError('The created work is no longer accessible.')
        return anomaly, receipt
    if event_owned:
        plan_token = work_plan(
            actor=actor,
            machine=machine,
            binding_id=alarm['binding_id'],
            active_alarm=anomaly,
        )['plan_token']
    try:
        plan = signing.loads(plan_token, salt=PLAN_SALT, max_age=900)
    except signing.BadSignature as exc:
        raise WorkPackageError(
            'The work preview expired or changed. Reload it before triggering.'
        ) from exc
    if any(
        plan.get(key) != expected
        for key, expected in {
            'actor_id': actor.pk,
            'machine_id': machine.pk,
            'binding_id': alarm['binding_id'],
            'component_id': alarm['component_id'],
        }.items()
    ):
        raise WorkPackageError('The work preview does not match this actor and target.')
    if not event_owned:
        assignment_candidate(actor, machine, assigned_to_id)
    scenario = plan['scenario']
    fields = scenario['fields']
    result = create_repair_work_package(
        actor=actor,
        idempotency_key=f'test-alarm-work:{actor.pk}:{alarm["idempotency_key"]}',
        draft={
            'machine_id': machine.pk,
            'origin': 'anomaly',
            'title': fields['title'],
            'description': fields['work_scope'],
            'priority': scenario['priority'],
            'work_order_type': scenario['work_order_type'],
            'create_repair_packet': True,
            'fault': {
                'summary': fields['work_scope'],
                'symptom': fields['symptom'],
                'production_impact': fields['production_impact'],
                'criticality': scenario['criticality'],
            },
            'planning': (
                {
                    'auto_assign': True,
                    'required_trades': [],
                    'trade_requirements_confirmed': False,
                }
                if event_owned
                else {'assigned_to_id': assigned_to_id}
            ),
            'source': {'anomaly_id': anomaly.pk},
            'parts': [],
            'duplicate_override': duplicate_override,
            'duplicate_override_reason': duplicate_override_reason,
        },
    )
    receipt = {**result.as_dict(), 'provenance': scenario['provenance']}
    if event_owned:
        from tasks.models import WorkOrderEvent

        event = WorkOrderEvent.objects.get(
            work_order_id=result.work_order_id, event_type='WORK_PACKAGE_CREATED'
        )
        assignment = event.metadata['result_metadata']['assignment']
        if assignment.get('code') == 'ASSIGNMENT_REQUIREMENTS_UNKNOWN':
            assignment = {
                **assignment,
                'supervisor_escalation': (
                    'Supervisor review required: trade requirements are not confirmed; '
                    'individual availability, workload and relevant experience are not verified.'
                ),
            }
            WorkOrderEvent.objects.create(
                work_order_id=result.work_order_id,
                event_type='TEST_ALARM_ASSIGNMENT_ESCALATED',
                from_status=event.to_status,
                to_status=event.to_status,
                actor=actor,
                reason=assignment['supervisor_escalation'],
                correlation_id=event.correlation_id,
                idempotency_key=f'test-alarm-escalation:{actor.pk}:{alarm["idempotency_key"]}',
                metadata={'source_anomaly_id': anomaly.pk, 'assignment': assignment},
            )
        receipt['assignment'] = assignment
        receipt['triage'] = {
            'priority': scenario['priority'],
            'criticality': scenario['criticality'],
            'explanation': 'Critical synthetic test event; prepared investigation priority, not a physical diagnosis.',
        }
    anomaly.metrics = {**anomaly.metrics, 'automatic_work_order': receipt}
    anomaly.save(update_fields=['metrics', 'updated_at'])
    return anomaly, receipt
