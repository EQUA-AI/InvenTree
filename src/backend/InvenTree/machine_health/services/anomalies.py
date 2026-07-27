"""Deterministic anomaly detection and lifecycle.

Detection is rule-based on purpose. Two things can raise an anomaly:

1. an alarm the source system itself declared, or
2. a configured threshold on a :class:`MachineSignalBinding`.

AI is not one of them. A model may summarize an anomaly that already exists, but
it may not independently declare a machine critical - every critical alarm traces
back to a policy record or to the control system that owns the asset.

Repeated ingestion of the same condition is idempotent: an anomaly's identity is
its ``(machine, fingerprint)`` pair while it is still active, so a source that
resends an alarm every minute updates one row rather than flooding the blade.
"""

from __future__ import annotations

import hashlib

from django.db import IntegrityError, transaction
from django.utils import timezone

from assets.health_models import (
    ACTIVE_ANOMALY_STATUSES,
    AnomalySeverity,
    AnomalyStatus,
    HealthState,
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
)

THRESHOLD_DETECTOR = 'threshold'
THRESHOLD_DETECTOR_VERSION = '1'
SOURCE_ALARM_DETECTOR = 'source_alarm'

#: Board severity for each threshold classification.
_STATE_SEVERITY = {
    HealthState.WARNING: AnomalySeverity.WARNING,
    HealthState.CRITICAL: AnomalySeverity.CRITICAL,
}


class AnomalyError(Exception):
    """The anomaly request is invalid."""

    code = 'ANOMALY_INVALID'


def fingerprint_for(*parts) -> str:
    """Return a stable identity for 'the same problem'."""
    joined = '|'.join(str(part or '') for part in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:64]


@transaction.atomic
def record_anomaly(
    *,
    machine,
    fingerprint: str,
    title: str,
    severity: str,
    observed_at=None,
    source=None,
    bindings=(),
    detector: str = '',
    detector_version: str = '',
    external_id: str = '',
    alarm_code: str = '',
    evidence_summary: str = '',
    metrics: dict | None = None,
) -> tuple[MachineAnomaly, bool]:
    """Open or refresh the active anomaly for ``(machine, fingerprint)``.

    Returns ``(anomaly, created)``. An already-active anomaly is refreshed in
    place - severity may escalate, never silently de-escalate, so an alarm that
    briefly reads as a warning cannot downgrade a standing critical condition.
    """
    if severity not in AnomalySeverity.values:
        raise AnomalyError(f'Unknown anomaly severity {severity!r}.')

    observed_at = observed_at or timezone.now()
    active_values = [status.value for status in ACTIVE_ANOMALY_STATUSES]

    existing = (
        MachineAnomaly.objects
        .select_for_update()
        .filter(machine=machine, fingerprint=fingerprint, status__in=active_values)
        .first()
    )

    if existing is not None:
        updates = {'last_observed_at': observed_at}
        if _severity_rank(severity) > _severity_rank(existing.severity):
            updates['severity'] = severity
        if evidence_summary:
            updates['evidence_summary'] = evidence_summary
        if metrics:
            updates['metrics'] = metrics
        for name, value in updates.items():
            setattr(existing, name, value)
        existing.save(update_fields=[*updates, 'updated_at'])
        if bindings:
            existing.bindings.add(*bindings)
        return existing, False

    try:
        anomaly = MachineAnomaly.objects.create(
            machine=machine,
            source=source,
            fingerprint=fingerprint,
            title=title[:255],
            severity=severity,
            status=AnomalyStatus.OPEN,
            evidence_summary=evidence_summary,
            metrics=metrics or {},
            detector=detector,
            detector_version=detector_version,
            external_id=external_id[:128],
            alarm_code=alarm_code[:64],
            first_observed_at=observed_at,
            last_observed_at=observed_at,
        )
    except IntegrityError as exc:
        # Lost a race against a concurrent ingest for the same condition; the
        # partial unique index is the authority, so adopt the winner.
        raise AnomalyError(
            'An active anomaly already exists for this machine and fingerprint.'
        ) from exc

    if bindings:
        anomaly.bindings.add(*bindings)

    return anomaly, True


def _severity_rank(severity: str) -> int:
    order = {
        AnomalySeverity.INFO: 0,
        AnomalySeverity.WARNING: 1,
        AnomalySeverity.CRITICAL: 2,
    }
    return order.get(severity, 0)


def evaluate_thresholds(machine, *, now=None) -> list[MachineAnomaly]:
    """Raise or clear threshold anomalies for one machine's current signals.

    Only bindings with configured bounds participate. A signal with no bounds has
    no opinion about health and must not manufacture one.
    """
    now = now or timezone.now()
    raised: list[MachineAnomaly] = []

    states = MachineSignalState.objects.select_related('binding').filter(
        binding__machine=machine, binding__active=True
    )

    seen_fingerprints = set()

    for state in states:
        binding = state.binding
        value = (state.value or {}).get('value')
        classification = binding.classify(value)

        fingerprint = fingerprint_for(
            THRESHOLD_DETECTOR, binding.pk, binding.external_key
        )

        if classification not in _STATE_SEVERITY:
            continue

        seen_fingerprints.add(fingerprint)
        anomaly, _created = record_anomaly(
            machine=machine,
            fingerprint=fingerprint,
            title=f'{binding.display_name} outside configured limits',
            severity=_STATE_SEVERITY[classification],
            observed_at=state.observed_at,
            source=binding.source,
            bindings=[binding],
            detector=THRESHOLD_DETECTOR,
            detector_version=THRESHOLD_DETECTOR_VERSION,
            evidence_summary=(
                f'{binding.display_name} read {value} {binding.unit}'.strip()
            ),
            metrics={
                'value': value,
                'unit': binding.unit,
                'warn_min': binding.warn_min,
                'warn_max': binding.warn_max,
                'critical_min': binding.critical_min,
                'critical_max': binding.critical_max,
            },
        )
        raised.append(anomaly)

    _auto_resolve_threshold_anomalies(machine, seen_fingerprints, now=now)

    return raised


def _auto_resolve_threshold_anomalies(machine, still_breaching, *, now):
    """Resolve threshold anomalies whose signal has returned inside its limits.

    Only anomalies this detector raised are auto-resolved. A source-declared
    alarm is the source's to clear, and an operator-acknowledged condition is
    never closed on their behalf.
    """
    stale = MachineAnomaly.objects.filter(
        machine=machine, detector=THRESHOLD_DETECTOR, status=AnomalyStatus.OPEN
    ).exclude(fingerprint__in=still_breaching)

    for anomaly in stale:
        anomaly.status = AnomalyStatus.RESOLVED
        anomaly.resolved_at = now
        anomaly.resolution_note = 'Signal returned inside its configured limits'
        anomaly.save(
            update_fields=['status', 'resolved_at', 'resolution_note', 'updated_at']
        )


@transaction.atomic
def acknowledge_anomaly(anomaly_id: int, *, actor, note: str = '') -> MachineAnomaly:
    """Acknowledge an open anomaly.

    Acknowledging records that a human has seen the condition. It does not
    resolve it, and it never satisfies a safety gate or marks a repair ready.
    """
    anomaly = MachineAnomaly.objects.select_for_update().get(pk=anomaly_id)

    if anomaly.status == AnomalyStatus.ACKNOWLEDGED:
        return anomaly

    if anomaly.status != AnomalyStatus.OPEN:
        raise AnomalyError(
            f'Only an open anomaly can be acknowledged; this one is '
            f'{anomaly.get_status_display().lower()}.'
        )

    anomaly.status = AnomalyStatus.ACKNOWLEDGED
    anomaly.acknowledged_at = timezone.now()
    anomaly.acknowledged_by = actor if getattr(actor, 'pk', None) else None
    anomaly.acknowledgement_note = note[:2000]
    anomaly.save(
        update_fields=[
            'status',
            'acknowledged_at',
            'acknowledged_by',
            'acknowledgement_note',
            'updated_at',
        ]
    )
    return anomaly


def ingest_source_alarm(
    *,
    machine,
    source,
    alarm_code: str,
    title: str,
    severity: str,
    observed_at=None,
    external_id: str = '',
    external_key: str = '',
    evidence_summary: str = '',
    metrics: dict | None = None,
) -> tuple[MachineAnomaly, bool]:
    """Record an alarm the source system itself declared."""
    bindings = []
    if external_key:
        binding = MachineSignalBinding.objects.filter(
            machine=machine, source=source, external_key=external_key
        ).first()
        if binding is not None:
            bindings.append(binding)

    return record_anomaly(
        machine=machine,
        fingerprint=fingerprint_for(
            SOURCE_ALARM_DETECTOR, source.pk if source else '', alarm_code, external_key
        ),
        title=title,
        severity=severity,
        observed_at=observed_at,
        source=source,
        bindings=bindings,
        detector=SOURCE_ALARM_DETECTOR,
        detector_version=str(source.pk) if source else '',
        external_id=external_id,
        alarm_code=alarm_code,
        evidence_summary=evidence_summary,
        metrics=metrics or {},
    )
