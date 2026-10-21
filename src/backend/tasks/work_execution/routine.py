"""P51: native routine tasks and readings, without invented calibration facts."""

from django.db.models import Prefetch

from .common import closeout_wizard_enabled, person, procedures_enabled

ROUTINE_TYPES = ('preventive', 'inspection', 'calibration')


def project(work_order, actor=None):
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
    from tasks.services.calibration import project as project_traceability

    traceability = (
        project_traceability(work_order)
        if readings and work_order.work_order_type == 'calibration'
        else {}
    )
    reading_rows = (
        list(
            CloseoutReadingSerializer(
                work_order.closeout_readings.select_related('recorded_by').order_by(
                    'pk'
                ),
                many=True,
            ).data
        )
        if readings
        else []
    )
    if work_order.work_order_type == 'calibration':
        for reading in reading_rows:
            reading['calibration_traceability'] = traceability.get(reading['id'], [])
    from tasks.services.routine_findings import creation_availability, linked

    return {
        'linked_corrective': linked(work_order, actor),
        **creation_availability(work_order, actor),
        'calibration_traceability_can_record': bool(
            readings
            and work_order.work_order_type == 'calibration'
            and actor
            and actor.is_active
            and actor.has_perm('tasks.capture_closeout')
        ),
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
        'readings': reading_rows,
    }
