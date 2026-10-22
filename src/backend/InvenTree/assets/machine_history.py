"""R6 P12 (UI06/UI08): a machine's work views and its fault occurrences.

Two read projections over records that already exist; neither stores anything.

**Work views** (``machine_work_views.v1``). The machine Maintenance tab shows
*Open work* and *Completed history* apart. Open work is the open, active Work
in the reader's canonical Work scope - the same status set, active flag and
scope predicate the Machines list counts with (``assets.location_api``), so
the two numbers cannot disagree. Completed history is the native maintenance
record index for the machine, counted from the same queryset the native table
pages through. The endpoint returns 404 when machine/actor scope is unresolved.
A later resolver failure is defensively represented as ``None``, never zero.

**Fault occurrences** (``fault_occurrences.v1``). An occurrence is an existing
``MachineAnomaly``: its own id, the source's alarm id and code, when it was
observed, what it was found on, the evidence snapshots retained for it and
the work that owns the response. Nothing here mints a new fault record or
identifier. Retained snapshots are immutable and are returned as captured; the
machine's current readings are not part of an occurrence and never replace
its retained window. Work is re-authorized per row against the reader's
canonical Work scope (resolved once for this projection); work outside it is withheld entirely rather than shown
as a dead reference.

O27 presents occurrence observations on the dashboard clock. Snapshot windows,
samples and capture times retain their immutable recorded evidence clock.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from tasks.dashboard_metrics import OPEN
from tasks.scope import scoped_work_orders

from assets.health_models import (
    ACTIVE_ANOMALY_STATUSES,
    AnomalyStatus,
    HealthEvidenceSnapshot,
    MachineAnomaly,
    MachineSignalBinding,
)
from assets.models import AssetMaintenanceRecord
from machine_health.services.display_time import (
    display_shift,
    presentation_metadata,
    recorded_telemetry_as_live,
    to_display,
)

MACHINE_WORK_VIEWS_RULE = 'machine_work_views.v1'
FAULT_OCCURRENCES_RULE = 'fault_occurrences.v1'

#: Open lifecycle states in the order the native lifecycle runs.
OPEN_ORDER = ('planned', 'ready', 'in_progress', 'on_hold', 'verifying')

#: Occurrence pages are bounded; this is a history view, not an export.
DEFAULT_OCCURRENCE_PAGE = 25
MAX_OCCURRENCE_PAGE = 100

#: Snapshot references listed per occurrence row; the detail carries them all
#: up to ``MAX_DETAIL_SNAPSHOTS``, with their samples.
MAX_ROW_SNAPSHOTS = 20
MAX_DETAIL_SNAPSHOTS = 50

OCCURRENCE_STATUSES = ('all', 'active', 'resolved')


class OccurrenceQueryError(ValueError):
    """A list parameter was outside the supported values."""


def machine_work_summary(actor, machine) -> dict:
    """Counts for the machine's Open work and Completed history views."""
    from assets.location_api import _open_work_by_machine

    open_work = _open_work_by_machine(actor, [machine.pk])
    return {
        'rule': MACHINE_WORK_VIEWS_RULE,
        'machine': machine.pk,
        # The native order where it is known; any state OPEN adds is kept.
        'open_statuses': [state for state in OPEN_ORDER if state in OPEN]
        + sorted(set(OPEN) - set(OPEN_ORDER)),
        # Defensive failure after the endpoint has authorized the machine.
        'open': None
        if open_work is None
        else open_work.get(machine.pk, {'count': 0, 'highest_priority': None}),
        'completed': {
            'count': AssetMaintenanceRecord.objects.filter(machine=machine).count(),
            'source': 'maintenance records',
        },
    }


def _reader_scopes(actor, anomalies):
    """Authorize only this bounded page's Work through the canonical predicate."""
    ids = {row.work_order_id for row in anomalies if row.work_order_id}
    ids.update(
        row.repair_packet.work_order_id
        for row in anomalies
        if row.repair_packet_id and row.repair_packet.work_order_id
    )
    return set(
        scoped_work_orders(actor).filter(pk__in=ids).values_list('pk', flat=True)
    )


def _visible(scopes, work_order):
    """Use canonical Work visibility without per-row resolver queries."""
    if work_order is None:
        return None
    return work_order if work_order.pk in scopes else None


def _work_row(work_order, relation: str) -> dict:
    return {
        'id': work_order.pk,
        'reference': work_order.reference,
        'title': work_order.title,
        'lifecycle_status': work_order.lifecycle_status,
        'priority': work_order.priority,
        'work_order_type': work_order.work_order_type,
        'affected_component': work_order.affected_component,
        'actual_completed_at': work_order.actual_completed_at.isoformat()
        if work_order.actual_completed_at
        else None,
        'relation': relation,
    }


def _work_for(scopes, anomaly, machine) -> tuple[list[dict], dict | None]:
    """Work that owns or relates to the occurrence, as the reader may see it.

    The anomaly's own ``work_order`` owns the response. A repair packet raised
    for it can carry a different work order; that one is related work. Both
    are re-authorized here, so an out-of-scope job is simply absent.
    """
    rows = []
    owning = _visible(scopes, anomaly.work_order)
    if owning is not None:
        rows.append(_work_row(owning, 'owning'))
    packet = anomaly.repair_packet
    packet_ref = None
    if (
        packet is not None
        and packet.machine_id == machine.pk
        and (packet.work_order_id is None or packet.work_order.machine_id == machine.pk)
    ):
        packet_work = packet.work_order
        visible_packet_work = _visible(scopes, packet_work)
        if packet_work is None or visible_packet_work is not None:
            packet_ref = {
                'id': packet.pk,
                'reference': packet.reference,
                'status': packet.status,
            }
        if visible_packet_work is not None and (
            owning is None or visible_packet_work.pk != owning.pk
        ):
            rows.append(_work_row(visible_packet_work, 'repair_packet'))
    return rows, packet_ref


def _components(anomaly) -> list[dict]:
    """Component slots of the occurrence's signals, through their source points."""
    found = {}
    for binding in anomaly.bindings.all():
        point = binding.dictionary_point
        component = point.component if point is not None else None
        if component is not None and component.pk not in found:
            found[component.pk] = {
                'code': component.code,
                'name': component.name,
                'status': component.status,
            }
    return sorted(found.values(), key=lambda row: row['code'])


def _signals(anomaly) -> list[dict]:
    return [
        {
            'binding_id': binding.pk,
            'display_name': binding.display_name,
            'unit': binding.unit,
            'signal_kind': binding.signal_kind,
        }
        for binding in sorted(anomaly.bindings.all(), key=lambda b: b.display_name)
    ]


def _snapshot(snapshot, *, with_samples: bool) -> dict:
    row = {
        'id': str(snapshot.id),
        'signal_label': snapshot.signal_label,
        'unit': snapshot.unit,
        'binding': snapshot.binding_id,
        'source_name': snapshot.source.name if snapshot.source else None,
        'window_start': snapshot.window_start.isoformat()
        if snapshot.window_start
        else None,
        'window_end': snapshot.window_end.isoformat() if snapshot.window_end else None,
        'captured_at': snapshot.captured_at.isoformat(),
        'captured_by_name': (
            snapshot.created_by.get_full_name().strip()
            or snapshot.created_by.get_username()
        )
        if snapshot.created_by
        else None,
        'system_actor': snapshot.system_actor,
        'quality': snapshot.quality,
        'stale': snapshot.stale,
        'reason': snapshot.reason,
        'content_hash': snapshot.content_hash,
        'display_shifted': False,
        'display_shift_seconds': 0,
        'recorded_telemetry_as_live': recorded_telemetry_as_live(),
    }
    if with_samples:
        row['samples'] = snapshot.samples
        row['statistics'] = snapshot.statistics
        row['source_references'] = snapshot.source_references
    else:
        row['sample_count'] = len(snapshot.samples or [])
    return row


def _occurrence(scopes, anomaly, machine, snapshots) -> dict:
    work, packet = _work_for(scopes, anomaly, machine)
    acknowledged_by = anomaly.acknowledged_by
    station = machine if machine.asset_type == 'pumphouse' else machine.parent
    shift = (
        display_shift(station, anomaly.source)
        if recorded_telemetry_as_live()
        else timezone.timedelta(0)
    )
    return {
        'id': anomaly.pk,
        'external_id': anomaly.external_id,
        'alarm_code': anomaly.alarm_code,
        'severity': anomaly.severity,
        'status': anomaly.status,
        'active': anomaly.status in {s.value for s in ACTIVE_ANOMALY_STATUSES},
        'title': anomaly.title,
        'evidence_summary': anomaly.evidence_summary,
        'metrics': anomaly.metrics,
        'detector': anomaly.detector,
        'detector_version': anomaly.detector_version,
        'source': {
            'id': anomaly.source.pk,
            'name': anomaly.source.name,
            'type': anomaly.source.source_type,
        }
        if anomaly.source
        else None,
        'signals': _signals(anomaly),
        'components': _components(anomaly),
        'first_observed_at': to_display(anomaly.first_observed_at, shift).isoformat(),
        'last_observed_at': to_display(anomaly.last_observed_at, shift).isoformat(),
        **presentation_metadata(shift),
        'acknowledged_at': anomaly.acknowledged_at.isoformat()
        if anomaly.acknowledged_at
        else None,
        'acknowledged_by_name': (
            acknowledged_by.get_full_name() or acknowledged_by.get_username()
        )
        if acknowledged_by
        else None,
        'resolved_at': anomaly.resolved_at.isoformat() if anomaly.resolved_at else None,
        'resolution_note': anomaly.resolution_note,
        'work': work,
        'repair_packet': packet,
        'snapshot_count': len(snapshots),
        'snapshots': snapshots,
    }


def _anomalies(machine):
    return (
        MachineAnomaly.objects
        .filter(machine=machine)
        .select_related(
            'source',
            'acknowledged_by',
            'work_order__machine',
            'repair_packet__work_order__machine',
        )
        .prefetch_related(
            Prefetch(
                'bindings',
                queryset=MachineSignalBinding.objects.select_related(
                    'dictionary_point__component'
                ),
            )
        )
    )


def fault_occurrences(actor, machine, *, status='all', limit=None, offset=None):
    """A page of the machine's occurrences, newest first, with evidence refs."""
    if status not in OCCURRENCE_STATUSES:
        raise OccurrenceQueryError('status must be all, active or resolved.')
    try:
        limit = DEFAULT_OCCURRENCE_PAGE if limit in (None, '') else int(limit)
        offset = 0 if offset in (None, '') else int(offset)
    except (TypeError, ValueError) as exc:
        raise OccurrenceQueryError('limit and offset must be whole numbers.') from exc
    if limit < 1 or offset < 0:
        raise OccurrenceQueryError('limit must be positive and offset not negative.')
    limit = min(limit, MAX_OCCURRENCE_PAGE)

    queryset = _anomalies(machine)
    if status == 'active':
        queryset = queryset.filter(
            status__in=[value.value for value in ACTIVE_ANOMALY_STATUSES]
        )
    elif status == 'resolved':
        queryset = queryset.filter(status=AnomalyStatus.RESOLVED)
    count = queryset.count()
    page = list(queryset.order_by('-first_observed_at', '-pk')[offset : offset + limit])

    by_anomaly: dict[int, list] = {anomaly.pk: [] for anomaly in page}
    for snapshot in (
        HealthEvidenceSnapshot.objects
        .filter(anomaly__in=page)
        .select_related('source', 'created_by')
        .order_by('captured_at', 'id')
    ):
        by_anomaly[snapshot.anomaly_id].append(snapshot)

    scopes = _reader_scopes(actor, page)
    results = []
    for anomaly in page:
        retained = by_anomaly[anomaly.pk]
        row = _occurrence(
            scopes,
            anomaly,
            machine,
            [
                _snapshot(snapshot, with_samples=False)
                for snapshot in retained[-MAX_ROW_SNAPSHOTS:]
            ],
        )
        row['snapshot_count'] = len(retained)
        results.append(row)

    return {
        'rule': FAULT_OCCURRENCES_RULE,
        'machine': machine.pk,
        'status': status,
        'plant_timezone': settings.AIMMS_PLANT_TIMEZONE or None,
        'recorded_telemetry_as_live': recorded_telemetry_as_live(),
        'count': count,
        'limit': limit,
        'offset': offset,
        'results': results,
    }


def fault_occurrence(actor, machine, occurrence_id: int) -> dict | None:
    """One occurrence with every retained snapshot and its samples."""
    anomaly = _anomalies(machine).filter(pk=occurrence_id).first()
    if anomaly is None:
        return None
    retained = list(
        HealthEvidenceSnapshot.objects
        .filter(anomaly=anomaly)
        .select_related('source', 'created_by')
        .order_by('captured_at', 'id')
    )
    scopes = _reader_scopes(actor, [anomaly])
    row = _occurrence(
        scopes,
        anomaly,
        machine,
        [
            _snapshot(snapshot, with_samples=True)
            for snapshot in retained[-MAX_DETAIL_SNAPSHOTS:]
        ],
    )
    row['snapshot_count'] = len(retained)
    return {'rule': FAULT_OCCURRENCES_RULE, 'machine': machine.pk, **row}
