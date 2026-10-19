"""Current-condition projection for one machine, or a page of machines.

The Health blade must never present stale telemetry as current. Every value the
summary reports carries its observation time, its quality and whether it has
aged past the source's freshness budget, and the overall state degrades when the
data behind it cannot be trusted:

* no mapped source at all -> ``unknown`` with an explicit empty state,
* every mapped signal stale -> ``offline``,
* some signals stale or of bad quality -> a degraded-data warning alongside
  whatever the readable signals say.

That way a connector outage reads as an outage rather than as a healthy machine.

``health_conditions`` judges many machines at once by exactly these rules, and
``health_summary`` is built on it, so a list and a Health tab cannot disagree.
"""

from __future__ import annotations

from collections import defaultdict

from django.db.models import Count
from django.utils import timezone

from assets.health_models import (
    ACTIVE_ANOMALY_STATUSES,
    AnomalySeverity,
    HealthState,
    MachineAnomaly,
    MachineSignalBinding,
    SignalQuality,
)
from assets.models import AssetMachine
from machine_health.services.display_time import display_shift, to_display

#: Anomaly severity mapped onto the machine's overall condition.
_SEVERITY_STATE = {
    AnomalySeverity.CRITICAL: HealthState.CRITICAL,
    AnomalySeverity.WARNING: HealthState.WARNING,
    AnomalySeverity.INFO: HealthState.NORMAL,
}

_STATE_RANK = {
    HealthState.UNKNOWN: 0,
    HealthState.NORMAL: 1,
    HealthState.WARNING: 2,
    HealthState.OFFLINE: 3,
    HealthState.CRITICAL: 4,
}


def signal_rows(machine, *, now=None):
    """Return each active binding with its current state and freshness.

    Observation times are presented in the clock the dashboard shows, and
    freshness is judged after that move. Judging it before would mark every row
    stale on the same reading the chart beside it draws happily - the signal
    table and the trend panel would be describing one value in two different
    times, which reads as a fault rather than as the design.
    """
    now = now or timezone.now()
    bindings = list(
        MachineSignalBinding.objects
        .select_related('source', 'state', 'dictionary_point__component')
        .filter(machine=machine, active=True)
        .order_by('display_name')
    )
    station = machine if machine.asset_type == 'pumphouse' else machine.parent
    return _binding_rows(bindings, station, now)


def _binding_rows(bindings, station, now):
    """Signal rows for one machine's active bindings, for every reader."""
    rows = []
    shift = (
        display_shift(station, bindings[0].source, now=now)
        if bindings
        else timezone.timedelta(0)
    )

    for binding in bindings:
        state = getattr(binding, 'state', None)
        threshold = binding.source.freshness_threshold_seconds
        observed_at = to_display(state.observed_at, shift) if state else None
        # Too old and dated-in-the-future are both "not a current reading". A
        # future observation means the stored value sits outside the span this
        # station is presenting - a leftover from an earlier load, say - and
        # showing it as live would put a reading on screen that has not
        # happened yet.
        age = (now - observed_at).total_seconds() if observed_at else None
        stale = age is None or age > threshold or age < 0
        value = (state.value or {}).get('value') if state else None
        quality = state.quality if state else SignalQuality.UNKNOWN
        # Unusable is as disqualifying as old. The mimic has always refused to
        # classify a reading whose quality is not good; this row did not, so a
        # pegged channel could drive a machine's condition on the Health blade
        # while the same row displayed "Unusable or unknown" beside it.
        usable = not stale and quality == SignalQuality.GOOD

        rows.append({
            'binding_id': binding.pk,
            'source_id': binding.source_id,
            'source_name': binding.source.name,
            'source_type': binding.source.source_type,
            # The mapped pointer, as the mimic already shows it. A page that
            # groups a pump's sixty signals into electrical, thermal and
            # mechanical needs the tag's own name; display names differ from
            # one station's dictionary to the next.
            'external_key': binding.external_key,
            # The component the reviewed tag dictionary assigns this point to,
            # with whether that assignment is a draft inference or verified.
            # Null when no dictionary point or component is recorded: the
            # signal is not attributed to any part, rather than to the machine.
            'component': _component(binding),
            'display_name': binding.display_name,
            'signal_kind': binding.signal_kind,
            'unit': binding.unit,
            'value': value,
            'observed_at': observed_at,
            # Not shifted, unlike observed_at. The shift exists to move a
            # *source* clock up to the present; received_at is already the
            # present - the wall-clock instant this deployment accepted the
            # value - so adding the offset would date the arrival of a reading
            # over a year after the reading itself.
            'received_at': state.received_at if state else None,
            'quality': quality,
            'stale': stale,
            'freshness_threshold_seconds': threshold,
            'state': binding.classify(value) if usable else HealthState.UNKNOWN,
            'limits': {
                'normal_min': binding.normal_min,
                'normal_max': binding.normal_max,
                'warn_min': binding.warn_min,
                'warn_max': binding.warn_max,
                'critical_min': binding.critical_min,
                'critical_max': binding.critical_max,
            },
        })

    return rows


def _component(binding) -> dict | None:
    """The component a binding's dictionary point names, or None."""
    point = binding.dictionary_point if binding.dictionary_point_id else None
    component = point.component if point and point.component_id else None
    if (
        component is None
        or point.machine_id != binding.machine_id
        or component.machine_id != binding.machine_id
    ):
        return None
    return {'code': component.code, 'name': component.name, 'status': component.status}


def health_summary(machine, *, now=None) -> dict:
    """Return the machine's current condition, freshness and anomaly counts."""
    now = now or timezone.now()
    return {
        **health_conditions([machine], now=now)[machine.pk],
        'sources': source_rows(machine, now=now),
    }


def health_conditions(machines, *, now=None) -> dict:
    """``health_summary`` without its source list, for many machines at once.

    Bindings, station clocks and active anomaly counts are each read once for
    the whole set, so a page of machines costs the same few queries however
    long it is. Returns ``{machine pk: condition}``. Authorization is the
    caller's: pass only machines the reader may see.
    """
    now = now or timezone.now()
    machines = list(machines)
    if not machines:
        return {}
    ids = [machine.pk for machine in machines]

    bindings = defaultdict(list)
    for binding in (
        MachineSignalBinding.objects
        .select_related('source', 'state', 'dictionary_point__component')
        .filter(machine_id__in=ids, active=True)
        .order_by('display_name')
    ):
        bindings[binding.machine_id].append(binding)

    # A pump presents on its station's clock (see display_time).
    parent_ids = {
        machine.parent_id
        for machine in machines
        if machine.pk in bindings
        and machine.asset_type != 'pumphouse'
        and machine.parent_id
    }
    parents = AssetMachine.objects.in_bulk(parent_ids) if parent_ids else {}

    active_values = [status.value for status in ACTIVE_ANOMALY_STATUSES]
    severities = defaultdict(list)
    for row in (
        MachineAnomaly.objects
        .filter(machine_id__in=ids, status__in=active_values)
        .values('machine_id', 'severity')
        .annotate(total=Count('pk'))
    ):
        severities[row['machine_id']].append((row['severity'], row['total']))

    conditions = {}
    for machine in machines:
        station = (
            machine
            if machine.asset_type == 'pumphouse'
            else parents.get(machine.parent_id)
        )
        conditions[machine.pk] = _condition(
            _binding_rows(bindings.get(machine.pk, []), station, now),
            severities.get(machine.pk, []),
        )
    return conditions


def _condition(rows, severity_counts) -> dict:
    """Judge one machine from its signal rows and active anomaly counts."""
    counts = {severity.value: 0 for severity in AnomalySeverity}
    worst_anomaly_state = HealthState.UNKNOWN
    for severity, count in severity_counts:
        counts[severity] = count
        if count:
            worst_anomaly_state = _worse(
                worst_anomaly_state, _SEVERITY_STATE.get(severity, HealthState.UNKNOWN)
            )

    if not rows:
        # No mapped source: say so rather than implying a healthy machine.
        # Active anomalies still speak; with none the state is unknown.
        return {
            'state': worst_anomaly_state,
            'configured': False,
            'signal_count': 0,
            'stale_signal_count': 0,
            'degraded_data': False,
            'last_observed_at': None,
            'anomaly_counts': counts,
            'active_anomaly_count': sum(counts.values()),
        }

    stale_rows = [row for row in rows if row['stale']]
    bad_quality = [
        row
        for row in rows
        if row['quality'] in {SignalQuality.BAD, SignalQuality.UNCERTAIN}
    ]

    observed = [row['observed_at'] for row in rows if row['observed_at']]
    last_observed_at = max(observed) if observed else None

    if len(stale_rows) == len(rows):
        state = HealthState.OFFLINE
    else:
        state = HealthState.NORMAL
        for row in rows:
            if not row['stale']:
                state = _worse(state, row['state'])

    state = _worse(state, worst_anomaly_state)

    return {
        'state': state,
        'configured': True,
        'signal_count': len(rows),
        'stale_signal_count': len(stale_rows),
        'degraded_data': bool(stale_rows or bad_quality),
        'last_observed_at': last_observed_at,
        'anomaly_counts': counts,
        'active_anomaly_count': sum(counts.values()),
    }


def _worse(left: str, right: str) -> str:
    """Return the more serious of two health states."""
    return left if _STATE_RANK.get(left, 0) >= _STATE_RANK.get(right, 0) else right


def source_rows(machine, *, now=None):
    """Return connection health for every source mapped to this machine."""
    now = now or timezone.now()
    seen = {}

    bindings = MachineSignalBinding.objects.select_related('source').filter(
        machine=machine
    )

    for binding in bindings:
        source = binding.source
        entry = seen.setdefault(
            source.pk,
            {
                'source_id': source.pk,
                'name': source.name,
                'source_type': source.source_type,
                'active': source.active,
                'healthy': source.connection_healthy,
                'last_success_at': source.last_success_at,
                'last_error_at': source.last_error_at,
                # Redacted classification only; never a provider message.
                'last_error_code': source.last_error_code,
                'freshness_threshold_seconds': source.freshness_threshold_seconds,
                'mapped_tag_count': 0,
            },
        )
        entry['mapped_tag_count'] += 1

    return sorted(seen.values(), key=lambda row: row['name'])
