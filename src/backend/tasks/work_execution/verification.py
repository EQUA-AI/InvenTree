"""P23: physical result records are separate from operating/service authority.

The procedure writer overwrites current values on retest. Its append-only
events retain outcomes, actors and times, but not the previous values. Keep
those events without filling the old attempt with today's reading.
"""

from .common import packet_of, person, procedures_enabled

TEST_TYPES = ('measurement', 'inspection', 'verification')
STEP_EVENTS = ('STEP_COMPLETED', 'STEP_FAILED', 'STEP_REOPENED', 'STEP_NOT_APPLICABLE')


def project(work_order) -> dict:
    """Project saved procedure results, lockout records and source limitations."""
    from repair.models import LockoutPoint
    from tasks.models import WorkOrderStepExecution

    enabled = procedures_enabled()
    executions = (
        list(
            WorkOrderStepExecution.objects
            .filter(
                application__work_order=work_order,
                step_snapshot__step_type__in=TEST_TYPES,
            )
            .select_related('application', 'completed_by')
            .order_by('application__sequence', 'sequence', 'pk')
        )
        if enabled
        else []
    )
    events = (
        list(
            work_order.events
            .filter(event_type__in=STEP_EVENTS)
            .select_related('actor')
            .order_by('created_at', 'pk')
        )
        if executions
        else []
    )
    packet = packet_of(work_order)
    return {
        'rule': 'verification_return.v1',
        'procedures_state': 'read' if enabled else 'not_enabled',
        'tests': [
            {
                'id': execution.pk,
                'application_id': execution.application_id,
                'procedure_revision': execution.application.snapshot.get(
                    'revision', {}
                ).get('revision'),
                'step': execution.step_snapshot,
                'version': execution.version,
                'status': execution.status,
                'value': execution.value,
                'passed': execution.passed,
                'note': execution.note,
                'disposition_reason': execution.disposition_reason,
                'recorded_by': person(execution.completed_by),
                'recorded_at': execution.completed_at.isoformat()
                if execution.completed_at
                else None,
                'events': [
                    {
                        'id': event.pk,
                        'event_type': event.event_type,
                        'version': event.metadata.get('version'),
                        'recorded_by': person(event.actor),
                        'recorded_at': event.created_at.isoformat(),
                        'reason': event.reason,
                        'evidence_ids': event.metadata.get('evidence_ids', []),
                        # Original values were never stored in these events.
                        'historical_value_available': False,
                    }
                    for event in events
                    if event.metadata.get('step_execution_id') == execution.pk
                ],
            }
            for execution in executions
        ],
        'attempt_history': 'outcomes_only_previous_values_not_retained',
        'lockouts': [
            {
                'id': point.pk,
                'device': point.isolation_device,
                'energy_source': point.energy_source,
                'status': point.status,
                'applied_by': person(point.applied_by),
                'verified_by': person(point.verified_by),
                'verified_at': point.verified_at.isoformat()
                if point.verified_at
                else None,
                'restored_at': point.restored_at.isoformat()
                if point.restored_at
                else None,
                'note': point.note,
            }
            for point in LockoutPoint.objects
            .filter(gate__packet=packet)
            .select_related('applied_by', 'verified_by')
            .order_by('created_at', 'pk')
        ]
        if packet
        else [],
        'technical_acceptance': {'available': False, 'reason': 'no_provider'},
        'controlled_test_authority': {'available': False, 'reason': 'no_provider'},
        'operating_permission': {'available': False, 'reason': 'no_provider'},
        'observed_return': {'available': False, 'reason': 'no_provider'},
        'service_state': {'available': False, 'reason': 'no_provider'},
    }
