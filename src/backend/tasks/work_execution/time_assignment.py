"""Assignment wall time and recorded person-work remain separate facts."""

from django.contrib.auth import get_user_model
from django.utils import timezone

from .common import person


def project(work_order, actor=None) -> dict:
    """Read native assignment provenance, retaining unknown time coverage."""
    observed_at = timezone.now()
    events = list(
        work_order.events
        .filter(event_type='ASSIGNED')
        .select_related('actor')
        .order_by('-created_at', '-pk')
    )
    target_ids = {
        event.metadata.get(key)
        for event in events
        for key in ('assigned_to_id', 'previous_assigned_to_id')
        if type(event.metadata.get(key)) is int and event.metadata[key] > 0
    }
    names = {
        user.pk: person(user)
        for user in get_user_model().objects.filter(pk__in=target_ids)
    }

    def name_for(target):
        return names.get(target) if type(target) is int else None

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
    from tasks.services.work_time import downtime_projection, time_projection

    records = time_projection(work_order, observed_at)
    return {
        'time_records': records,
        'downtime_records': downtime_projection(work_order, actor),
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
                'assigned_to_name': name_for(event.metadata.get('assigned_to_id')),
                'previous_assigned_to_name': name_for(
                    event.metadata.get('previous_assigned_to_id')
                ),
            }
            for event in events
        ],
        'attributed_intervals': {'available': True},
        'coverage': records['coverage'],
        'correction': {'available': True},
        'assignment_factors': {'available': False, 'reason': 'no_provider'},
        'accepted_handover': {'available': False, 'reason': 'read_work_continuity'},
    }
