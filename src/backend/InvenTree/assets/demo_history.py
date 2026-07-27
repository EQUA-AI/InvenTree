"""Shared completed-history contract for the machine demo loaders.

Both demo manifests (``demo_machine_data.json`` and
``water_workflow_demo_data.json``) seed machine maintenance history, and every
seeded row must resolve to a completed work order that renders the same way in
the Maintenance workspace. Keeping that contract in one module stops the two
loaders from drifting on lifecycle, board stage, completion time or provenance.

Nothing here claims a generated value was observed by a real technician, stock
transaction, approval or industrial control system: the provenance event records
the demo source and profile version so an imported row is always distinguishable
from governed closeout output.
"""

import datetime
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings

from tasks.models import KanbanColumn, WorkOrder, WorkOrderEvent, WorkOrderLifecycle

# Bump when the imported-history field set changes so a loader rerun can detect
# and refresh cards stamped by an older profile.
COMPLETED_HISTORY_PROFILE_VERSION = 1

IMPORTED_HISTORY_EVENT = 'IMPORTED_HISTORY'

# Demo history rows carry a date but no clock time. A fixed site-local
# late-afternoon completion keeps reruns byte-for-byte deterministic while
# staying plausible for a day-shift job.
COMPLETION_LOCAL_TIME = datetime.time(16, 0)

# Stable namespace so a replayed import reuses one correlation id per card.
_CORRELATION_NAMESPACE = uuid.UUID('6f2a1c34-9a1e-5b7d-8f43-3d1c0b7e5a90')


def completion_instant(record_date, *, timezone_name=None):
    """Return the deterministic completion datetime for a history row.

    The value is expressed in ``timezone_name`` (the site the job was performed
    at), then adapted to the project's ``USE_TZ`` setting so the same manifest
    loads under both the aware runtime configuration and the naive test one.
    """
    zone = ZoneInfo(timezone_name or settings.TIME_ZONE)
    local = datetime.datetime.combine(record_date, COMPLETION_LOCAL_TIME, tzinfo=zone)

    if settings.USE_TZ:
        return local
    return local.replace(tzinfo=None)


def normalize_completed_history_card(
    work_order, *, record_date, dataset, timezone_name=None, extra_metadata=None
):
    """Apply the minimum completed-history invariant to a demo work order.

    Sets the terminal board stage, completed lifecycle, inactive flag, work-order
    card kind and an actual completion time, then appends exactly one
    ``IMPORTED_HISTORY`` provenance event. Safe to replay: the event is looked up
    by (work order, event type) and refreshed rather than duplicated.

    Returns the completion datetime that was stored.
    """
    completed_at = completion_instant(record_date, timezone_name=timezone_name)
    terminal_status = KanbanColumn.terminal_key() or WorkOrder.STATUS_DONE

    fields = {
        'status': terminal_status,
        'lifecycle_status': WorkOrderLifecycle.COMPLETED,
        'is_active': False,
        'card_kind': WorkOrder.KIND_WORK_ORDER,
        'actual_completed_at': completed_at,
    }
    for field, value in fields.items():
        setattr(work_order, field, value)
    work_order.save(update_fields=[*fields, 'updated_at'])

    metadata = {
        'source': dataset,
        'profile_version': COMPLETED_HISTORY_PROFILE_VERSION,
        'synthetic': True,
        **(extra_metadata or {}),
    }
    correlation_id = uuid.uuid5(
        _CORRELATION_NAMESPACE, f'{dataset}:{work_order.reference or work_order.pk}'
    )

    event, created = WorkOrderEvent.objects.get_or_create(
        work_order=work_order,
        event_type=IMPORTED_HISTORY_EVENT,
        defaults={
            'from_status': '',
            'to_status': WorkOrderLifecycle.COMPLETED,
            'reason': 'Imported demo maintenance history',
            'correlation_id': correlation_id,
            'idempotency_key': f'{dataset}:history:{work_order.reference or work_order.pk}',
            'metadata': metadata,
        },
    )
    if not created and event.metadata != metadata:
        event.metadata = metadata
        event.save(update_fields=['metadata'])

    return completed_at
