"""P27: assignment events and elapsed wall time are never attributed labor.

No authoritative person-work interval, coverage or correction provider exists.
An assignment event may anchor elapsed wall time only when its recorded target
still matches the typed assignee. Work timestamps or a held lifecycle cannot
classify a person's time.
"""

from django.utils import timezone

from .common import person


def project(work_order) -> dict:
    """Read native assignment provenance, retaining unknown time coverage."""
    observed_at = timezone.now()
    events = list(
        work_order.events
        .filter(event_type='ASSIGNED')
        .select_related('actor')
        .order_by('-created_at', '-pk')
    )
    latest = events[0] if events else None
    target = latest.metadata.get('assigned_to_id') if latest else None
    anchor = (
        latest
        if (
            latest is not None
            and type(target) is int
            and target == work_order.assigned_to_id
            and latest.created_at <= observed_at
        )
        else None
    )
    return {
        'rule': 'time_assignment.v1',
        'observed_at': observed_at.isoformat(),
        'assigned_to': person(work_order.assigned_to),
        'legacy_assignee': work_order.assignee or None,
        'assignment_event_at': anchor.created_at.isoformat() if anchor else None,
        'elapsed_assignment_seconds': (observed_at - anchor.created_at).total_seconds()
        if anchor
        else None,
        'assignment_reason': anchor.reason if anchor and anchor.reason else None,
        'assignment_recorded_by': person(anchor.actor) if anchor else None,
        'events': [
            {
                'id': event.pk,
                'recorded_by': person(event.actor),
                'recorded_at': event.created_at.isoformat(),
                'reason': event.reason or None,
                'assigned_to_id': event.metadata.get('assigned_to_id'),
                'previous_assigned_to_id': event.metadata.get(
                    'previous_assigned_to_id'
                ),
            }
            for event in events
        ],
        'attributed_intervals': {'available': False, 'reason': 'no_provider'},
        'coverage': 'unknown',
        'correction': {'available': False, 'reason': 'no_provider'},
        'assignment_factors': {'available': False, 'reason': 'no_provider'},
        'accepted_handover': {'available': False, 'reason': 'no_provider'},
    }
