"""P44: current hold provenance and existing standalone record commands.

There is no site-required assignment acceptance, accepted handover, physical
state, service or cross-crew authority provider here. A lifecycle/assignment
event cannot stand in for any of those facts.
"""

from django.conf import settings

from .common import packet_of, person


def project(work_order, actor=None):
    """Read attributed history without accepting, holding or resuming work."""
    events = list(
        work_order.events
        .filter(event_type__in=('HOLD', 'RESUME'))
        .select_related('actor')
        .order_by('-created_at', '-pk')
    )
    packet = packet_of(work_order)
    enabled = bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False))
    permitted = bool(
        actor and actor.is_authenticated and actor.has_perm('tasks.execute_workorder')
    )
    eligible = enabled and permitted and packet is None
    latest = events[0] if events else None
    # A current hold reason may exist without a matching native audit event.
    # Do not borrow a recorder/time when reason or transition provenance differs.
    matching_hold = (
        latest
        if (
            latest
            and latest.event_type == 'HOLD'
            and latest.to_status == 'on_hold'
            and isinstance(latest.metadata, dict)
            and type(latest.metadata.get('lifecycle_version')) is int
            and 0 < latest.metadata['lifecycle_version'] <= work_order.lifecycle_version
            and latest.reason == work_order.hold_reason
        )
        else None
    )
    return {
        'rule': 'work_continuity.v1',
        'lifecycle_version': work_order.lifecycle_version,
        'current_hold': {
            'reason': work_order.hold_reason or None,
            'recorded_by': person(matching_hold.actor) if matching_hold else None,
            'recorded_at': matching_hold.created_at.isoformat()
            if matching_hold
            else None,
            'event_id': matching_hold.pk if matching_hold else None,
        }
        if work_order.lifecycle_status == 'on_hold'
        else None,
        'history': [
            {
                'event_id': event.pk,
                'action': event.event_type.lower(),
                'from_status': event.from_status,
                'to_status': event.to_status,
                'reason': event.reason or None,
                'recorded_by': person(event.actor),
                'recorded_at': event.created_at.isoformat(),
                'lifecycle_version': event.metadata['lifecycle_version']
                if isinstance(event.metadata, dict)
                and type(event.metadata.get('lifecycle_version')) is int
                and event.metadata['lifecycle_version'] > 0
                else None,
            }
            for event in events
        ],
        'commands': {
            'hold': bool(eligible and work_order.lifecycle_status == 'in_progress'),
            'resume': bool(eligible and work_order.lifecycle_status == 'on_hold'),
            'unavailable_reason': 'api_disabled'
            if not enabled
            else 'permission_required'
            if not permitted
            else 'packet_owns_lifecycle'
            if packet
            else None,
        },
        'assignment_acceptance': {
            'available': False,
            'reason': 'no_site_acceptance_provider',
        },
        'accepted_handover': {'available': False, 'reason': 'no_provider'},
        'physical_service_state': {'available': False, 'reason': 'no_provider'},
        'cross_crew_restrictions': {'available': False, 'reason': 'no_provider'},
    }
