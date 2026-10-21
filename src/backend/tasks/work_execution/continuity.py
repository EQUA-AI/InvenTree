"""P44: hold provenance and explicit incoming-owner handover acceptance.

Assignment proposes a handover; only the incoming owner's versioned command
changes responsibility. Physical/service and cross-crew authority stay separate.
"""

from django.conf import settings

from .common import person


def handover_summary(work_order, actor=None):
    """One responsibility source for continuity and assignment readers."""
    enabled = bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False))
    permitted = bool(
        actor and actor.is_authenticated and actor.has_perm('tasks.execute_workorder')
    )
    handover = (
        work_order.handovers
        .select_related(
            'previous_owner', 'incoming_owner', 'accepted_by', 'proposed_by'
        )
        .order_by('-pk')
        .first()
    )
    acceptance = (
        {
            'available': True,
            'state': 'accepted' if handover.accepted_at else 'pending',
            'id': handover.pk,
            'previous_owner': person(handover.previous_owner)
            or handover.previous_owner_name
            or None,
            'incoming_owner': person(handover.incoming_owner)
            or handover.incoming_owner_name
            or None,
            'responsible_owner': person(
                handover.incoming_owner
                if handover.accepted_at
                else handover.previous_owner
            ),
            'proposed_version': handover.proposed_version,
            'proposed_by': person(handover.proposed_by)
            or handover.proposed_by_name
            or None,
            'proposed_at': handover.proposed_at.isoformat(),
            'accepted_by': person(handover.accepted_by)
            or handover.accepted_by_name
            or None,
            'accepted_at': handover.accepted_at.isoformat()
            if handover.accepted_at
            else None,
            'accepted_version': handover.accepted_version,
            'can_accept': bool(
                enabled
                and permitted
                and actor.is_active
                and actor.pk == handover.incoming_owner_id
                and work_order.assigned_to_id == actor.pk
                and handover.accepted_at is None
                and handover.withdrawn_at is None
            ),
        }
        if handover
        else {'available': False, 'reason': 'no_recorded_handover'}
    )

    latest = work_order.events.filter(event_type='ASSIGNED').order_by('-pk').first()
    facts = (latest.metadata.get('result_metadata') or {}) if latest else {}
    disposition = facts.get('responsibility_disposition')
    if disposition in ('released', 'restored') and (
        handover is None or latest.created_at >= handover.proposed_at
    ):
        from django.contrib.auth import get_user_model

        current_responsible = (
            get_user_model()
            .objects.filter(pk=facts.get('responsible_owner_id'))
            .first()
        )
        return {
            'available': True,
            'state': disposition,
            'id': None,
            'previous_owner': facts.get('responsible_owner_name'),
            'responsible_owner': person(current_responsible),
            'incoming_owner': person(work_order.assigned_to),
            'proposed_version': latest.metadata.get('lifecycle_version'),
            'proposed_by': person(latest.actor),
            'proposed_at': latest.created_at.isoformat(),
            'accepted_by': None,
            'accepted_at': None,
            'accepted_version': None,
            'can_accept': False,
        }
    if handover and handover.withdrawn_at:
        acceptance.update(state='withdrawn', can_accept=False)
    return acceptance


def project(work_order, actor=None):
    """Read attributed history without accepting, holding or resuming work."""
    events = list(
        work_order.events
        .filter(event_type__in=('HOLD', 'RESUME'))
        .select_related('actor')
        .order_by('-created_at', '-pk')
    )
    # Withheld packet details do not transfer lifecycle ownership to Work.
    packet_owned = hasattr(work_order, 'repair_packet')
    enabled = bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False))
    permitted = bool(
        actor and actor.is_authenticated and actor.has_perm('tasks.execute_workorder')
    )
    eligible = enabled and permitted and not packet_owned
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
            if packet_owned
            else None,
        },
        'assignment_acceptance': {
            'available': False,
            'reason': 'no_site_acceptance_provider',
        },
        'accepted_handover': handover_summary(work_order, actor),
        'physical_service_state': {'available': False, 'reason': 'no_provider'},
        'cross_crew_restrictions': {'available': False, 'reason': 'no_provider'},
    }
