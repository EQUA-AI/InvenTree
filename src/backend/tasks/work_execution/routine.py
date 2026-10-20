"""P51: native routine tasks and readings, without invented calibration facts."""

from django.db.models import Prefetch

from .common import closeout_wizard_enabled, person, procedures_enabled

ROUTINE_TYPES = ('preventive', 'inspection', 'calibration')


def project(work_order):
    """Read only this Work's records; work type does not create a second workflow."""
    if work_order.work_order_type not in ROUTINE_TYPES:
        return None

    from tasks.closeout_serializers import CloseoutReadingSerializer
    from tasks.models import WorkOrderStepExecution

    procedures = procedures_enabled()
    readings = closeout_wizard_enabled()
    applications = (
        work_order.procedure_applications
        .select_related('applied_by')
        .prefetch_related(
            Prefetch(
                'step_executions',
                queryset=WorkOrderStepExecution.objects.select_related(
                    'completed_by'
                ).order_by('sequence', 'pk'),
            )
        )
        .order_by('sequence', 'pk')
        if procedures
        else []
    )
    return {
        'rule': 'routine_work.v1',
        'work_order_type': work_order.work_order_type,
        'procedures_state': 'read' if procedures else 'not_enabled',
        'readings_state': 'read' if readings else 'not_enabled',
        'applications': [
            {
                'id': application.pk,
                'revision': application.snapshot.get('revision', {}).get('revision'),
                'snapshot_hash': application.snapshot_hash,
                'applied_by': person(application.applied_by),
                'applied_at': application.applied_at.isoformat(),
                'tasks': [
                    {
                        'id': execution.pk,
                        'step': execution.step_snapshot,
                        'status': execution.status,
                        'version': execution.version,
                        # The N/A writer retains old values but changes their recorder.
                        'value': None
                        if execution.status == 'not_applicable'
                        else execution.value,
                        'note': ''
                        if execution.status == 'not_applicable'
                        else execution.note,
                        'disposition_reason': execution.disposition_reason,
                        'recorded_by': person(execution.completed_by),
                        'recorded_at': execution.completed_at.isoformat()
                        if execution.completed_at
                        else None,
                    }
                    for execution in application.step_executions.all()
                ],
            }
            for application in applications
        ],
        'readings': CloseoutReadingSerializer(
            work_order.closeout_readings.select_related('recorded_by').order_by('pk'),
            many=True,
        ).data
        if readings
        else [],
    }
