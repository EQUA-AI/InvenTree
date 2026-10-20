"""P21: saved work package, without promoting plans to physical authority.

Applied procedure content comes only from the immutable application snapshot.
The current procedure catalogue is deliberately not substituted for it. Scope
history is retained, but has no affected-dependency or evidence-validity graph.
"""

from .common import packet_of, person, procedures_enabled


def _scopes(packet):
    if packet is None:
        return []
    return [
        {
            'id': scope.pk,
            'version': scope.version,
            'current': scope.superseded_at is None,
            'verified_cause': scope.verified_cause,
            'lines': scope.scope_lines,
            'crew_size': scope.crew_size,
            'planned_elapsed_minutes': scope.planned_elapsed_minutes,
            'approved_by': person(scope.approved_by),
            'approved_at': scope.approved_at.isoformat(),
            'note': scope.approval_note,
            'failure_codes': scope.failure_codes,
            'superseded_at': scope.superseded_at.isoformat()
            if scope.superseded_at
            else None,
        }
        for scope in packet.approved_scopes.select_related('approved_by').order_by(
            '-version', '-pk'
        )
    ]


def _drift_status(application):
    """Compare the immutable applied identity with the catalogue at this read."""
    from tasks.models import ProcedureRevisionStatus

    revision = application.revision
    procedure = revision.procedure
    snapshot = application.snapshot
    identities = (
        (snapshot.get('procedure_id'), procedure.pk),
        (snapshot.get('revision_id'), revision.pk),
        (snapshot.get('revision', {}).get('revision'), revision.revision),
    )
    if any(
        type(recorded) is not int or recorded != actual
        for recorded, actual in identities
    ):
        return 'not_evaluated'
    if procedure.current_revision_id is None:
        return 'not_evaluated'
    if procedure.current_revision_id != revision.pk:
        return 'different_revision'
    if not procedure.active:
        return 'procedure_inactive'
    if revision.status != ProcedureRevisionStatus.PUBLISHED:
        return 'revision_not_published'
    return 'current'


def _application(application, part_names):
    snapshot = application.snapshot
    return {
        'id': application.pk,
        'primary': application.primary,
        'procedure_id': snapshot.get('procedure_id'),
        'revision_id': snapshot.get('revision_id'),
        'revision': snapshot.get('revision', {}).get('revision'),
        'change_summary': snapshot.get('revision', {}).get('change_summary'),
        'snapshot_hash': application.snapshot_hash,
        # The model's legacy default is not a recorded comparison.
        'drift_status': _drift_status(application),
        'drift_basis': 'catalogue_at_read',
        'current_catalogue_code': application.revision.procedure.code or None,
        'current_catalogue_name': application.revision.procedure.name or None,
        'applied_by': person(application.applied_by),
        'applied_at': application.applied_at.isoformat(),
        'steps': snapshot.get('steps', []),
        # Catalogue labels aid navigation; they do not replace saved requirements.
        'resources': [
            {**item, 'part_name': part_names.get(item.get('part_id'))}
            for item in snapshot.get('resources', [])
        ],
    }


def project(work_order, actor=None) -> dict:
    """Read the scoped parent's saved package and native deviations only."""
    from .scope_revision import project as revision_review

    enabled = procedures_enabled()
    scopes = _scopes(packet_of(work_order))
    applications = (
        list(
            work_order.procedure_applications.select_related(
                'applied_by', 'revision__procedure'
            ).order_by('sequence', 'pk')
        )
        if enabled
        else []
    )
    part_names = {}
    if actor and actor.is_authenticated and actor.has_perm('part.view_part'):
        from part.models import Part

        ids = {
            item.get('part_id')
            for application in applications
            for item in application.snapshot.get('resources', [])
            if type(item.get('part_id')) is int
        }
        part_names = dict(Part.objects.filter(pk__in=ids).values_list('pk', 'name'))
    return {
        'rule': 'prepared_package.v1',
        'scopes': scopes,
        'revision_review': revision_review(work_order, scopes),
        'procedures_state': 'read' if enabled else 'not_enabled',
        'applications': [
            _application(application, part_names) for application in applications
        ]
        if enabled
        else [],
        'deviations': [
            {
                'id': item.pk,
                'category': item.category,
                'application_key': item.application_key,
                'step_key': item.step_key,
                'resource_key': item.resource_key,
                'expected': item.expected,
                'actual': item.actual,
                'reason': item.reason,
                'recorded_by': person(item.actor),
                'created_at': item.created_at.isoformat(),
                'resolution': item.resolution,
                'resolved_at': item.resolved_at.isoformat()
                if item.resolved_at
                else None,
            }
            for item in work_order.deviations.select_related('actor').order_by(
                '-created_at', '-pk'
            )
        ]
        if enabled
        else [],
        # Mirrors the native deviation endpoint, which is gated by Procedures
        # (not the separate canonical work-order API flag).
        'can_record_deviation': bool(
            enabled
            and actor
            and actor.is_authenticated
            and actor.has_perm('tasks.execute_workorder')
        ),
        'physical_authority': {'available': False, 'reason': 'no_provider'},
        'affected_dependencies': {'available': False, 'reason': 'no_provider'},
    }
