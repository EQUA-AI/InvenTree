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
from collections import Counter
from datetime import timedelta

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
    SignalQuality,
)

THRESHOLD_DETECTOR = 'threshold'
THRESHOLD_DETECTOR_VERSION = '1'
SOURCE_ALARM_DETECTOR = 'source_alarm'

#: Why a threshold anomaly was closed. The two are kept apart deliberately.
#: "The reading came back inside its limits" is an observation; "we stopped being
#: able to tell" is an admission. Printing the first when the second happened is
#: how a real condition gets forgotten, and it is the failure this detector is
#: most likely to produce, because losing a rule and losing a breach look
#: identical from here - both simply stop matching.
RESOLUTION_IN_LIMITS = 'Signal returned inside its configured limits'
RESOLUTION_UNASSESSABLE = (
    'Closed without a clearing reading: this binding no longer carries a '
    'threshold, or it stopped reporting. The condition was not observed to end.'
)

#: How long a signal must read inside its limits before the condition closes,
#: measured on the PLANT's clock rather than the server's.
#:
#: Raising and clearing are not symmetric. A breach that turns out to be
#: transient costs somebody a look; a clear that turns out to be transient
#: closes a real condition and nobody looks again. So the clear side gets the
#: hysteresis.
#:
#: The plant clock is the whole of the fix. ``MachineSignalState`` holds one row
#: per binding, so a poll that applies up to ``MAX_DOCUMENTS_PER_STATION`` = 200
#: snapshots five seconds apart - about seventeen minutes of plant time -
#: evaluates exactly once, against the last of them. A winding above its critical
#: limit for 199 of those 200 samples that dips below on the last one would close
#: a standing critical, while the poller still held the 199 contrary samples in
#: memory. Measured on the server clock a delay would not help: one evaluation is
#: one evaluation however long it took. Measured on ``observed_at``, the clearing
#: reading is five seconds newer than the last breach, the gate holds, and the
#: condition survives its own catch-up.
#:
#: Five minutes because that is already this estate's validity window - the same
#: figure ``assets.activation._initial_cursor`` enters a live source at.
RESOLVE_AFTER = timedelta(minutes=5)

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

    A condition closes only after reading inside its limits for
    :data:`RESOLVE_AFTER` of plant time. See that constant for why the plant's
    clock rather than the server's is what makes the rule work.

    A critical condition is confirmed by corroborating detectors where the
    reviewed limits file asked for it. Two standards require this - IEEE Std
    3004.8-2016 cl. 8.5.2.2 and API Std 670 cl. 5.4.6.4 - and with eleven or
    twelve winding detectors on one band per machine, a single failed input
    would otherwise open a critical alarm about a machine that is fine.

    A lone breach is **de-escalated, never suppressed**. That distinction is the
    whole of the design. A stator hot spot in one slot is real and is exactly
    what a detector array exists to catch, so silencing a single detector to
    avoid nuisance trips would discard the signal the standards are protecting.
    One detector past critical raises a warning saying it is unconfirmed; two
    raise the critical. Escalation is left to ``record_anomaly``, which never
    silently de-escalates an already-open condition - so a condition that was
    confirmed stays confirmed even if a corroborating detector later fails.
    """
    now = now or timezone.now()
    raised: list[MachineAnomaly] = []

    states = list(
        MachineSignalState.objects.select_related('binding', 'binding__source').filter(
            binding__machine=machine, binding__active=True
        )
    )

    # The plant's "now": the newest usable reading this machine has. A channel
    # whose last reading is older than that by more than its source's freshness
    # window has stopped reporting, and the number it left behind is not a
    # measurement of the machine as it is. Judged on the plant clock, like the
    # clearing gate, so a recorded window presented 442 days late is not stale
    # from end to end.
    newest = max(
        (state.observed_at for state in states if state.quality == SignalQuality.GOOD),
        default=None,
    )

    # Three outcomes, not two. ``holding`` is never resolved; ``cleared`` earned
    # the recovery note; anything open and in neither gets the honest one.
    holding = set()
    cleared = set()
    breaching = []

    # Read up front so a clearing reading can be measured against when the
    # condition was last seen. The same rows are re-read by the resolver below;
    # one extra query per machine buys the only evidence that distinguishes a
    # recovery from a dip.
    last_seen = dict(
        MachineAnomaly.objects.filter(
            machine=machine, detector=THRESHOLD_DETECTOR, status=AnomalyStatus.OPEN
        ).values_list('fingerprint', 'last_observed_at')
    )

    for state in states:
        binding = state.binding
        value = (state.value or {}).get('value')

        fingerprint = fingerprint_for(
            THRESHOLD_DETECTOR, binding.pk, binding.external_key
        )

        if state.quality != SignalQuality.GOOD:
            # An unusable reading is not evidence either way, and the second
            # half of that matters as much as the first. It must not raise an
            # anomaly, because the value is not a measurement. It must not
            # resolve one either: dropping the fingerprint here would let
            # _auto_resolve_threshold_anomalies close an open condition with
            # "Signal returned inside its configured limits", which is a
            # different and untrue claim from "the sensor stopped reporting".
            # Holding the fingerprint leaves an open condition open.
            holding.add(fingerprint)
            continue

        window = timedelta(seconds=binding.source.freshness_threshold_seconds)
        if newest is not None and newest - state.observed_at > window:
            # Stale is handled as unusable is, and for the same reason: a
            # reading nobody has refreshed can neither open a condition nor
            # close one. Holding keeps an open condition open and silent.
            holding.add(fingerprint)
            continue

        classification = binding.classify(value)

        if classification not in _STATE_SEVERITY:
            # NORMAL is an affirmative verdict and earns the recovery note:
            # classify() only reaches it when a bound is actually configured.
            # UNKNOWN is not. It means this reading cannot be judged - the value
            # is not a number, or activation wiped the bounds when the point's
            # meaning changed (assets.activation._refresh_binding). Treating the
            # two alike is what let "Signal returned inside its configured
            # limits" be written about a rule that had been deleted.
            if classification == HealthState.NORMAL:
                breached_at = last_seen.get(fingerprint)
                if (
                    breached_at is not None
                    and state.observed_at - breached_at < RESOLVE_AFTER
                ):
                    # Inside its limits, but not for long enough to call it over.
                    # Held rather than cleared, so the condition stays open and
                    # keeps its own note if it later closes for a different
                    # reason.
                    holding.add(fingerprint)
                else:
                    cleared.add(fingerprint)
            continue

        holding.add(fingerprint)
        breaching.append((state, binding, value, fingerprint, classification))

    # Counted across the whole pass, before any severity is written: the vote is
    # a property of the group, so it cannot be decided one detector at a time.
    confirmed = Counter(
        binding.vote_group
        for _state, binding, _value, _fp, classification in breaching
        if binding.vote_group and classification == HealthState.CRITICAL
    )

    for state, binding, value, fingerprint, classification in breaching:
        severity = _STATE_SEVERITY[classification]
        unconfirmed = (
            classification == HealthState.CRITICAL
            and binding.vote_minimum
            and binding.vote_group
            and confirmed[binding.vote_group] < binding.vote_minimum
        )
        if unconfirmed:
            severity = AnomalySeverity.WARNING

        summary = f'{binding.display_name} read {value} {binding.unit}'.strip()
        # A reading refreshed on time but holding the same number past the
        # freshness window is a different doubt from a stale one: the channel
        # reports, the acquisition behind it may not. Said on the alarm rather
        # than used to suppress it - a stopped bay's status is rightly constant,
        # and only the reader can tell a frozen winding from a cool one.
        window_seconds = binding.source.freshness_threshold_seconds
        unchanged = (
            (state.observed_at - state.value_changed_at).total_seconds()
            if state.value_changed_at
            else None
        )
        if unchanged is not None and unchanged > window_seconds:
            summary += (
                f' - and has read exactly that for {_span(unchanged)}, which may'
                ' be a frozen acquisition rather than a steady machine.'
            )
        if unconfirmed:
            summary += (
                f' - past its critical limit, but alone: {confirmed[binding.vote_group]}'
                f' of the {binding.vote_minimum} detectors this group needs to'
                f' confirm a machine condition. Treat as a possible sensor fault'
                f' until a second detector agrees.'
            )

        anomaly, _created = record_anomaly(
            machine=machine,
            fingerprint=fingerprint,
            title=f'{binding.display_name} outside configured limits',
            severity=severity,
            observed_at=state.observed_at,
            source=binding.source,
            bindings=[binding],
            detector=THRESHOLD_DETECTOR,
            detector_version=THRESHOLD_DETECTOR_VERSION,
            evidence_summary=summary,
            metrics={
                'value': value,
                'unit': binding.unit,
                'warn_min': binding.warn_min,
                'warn_max': binding.warn_max,
                'critical_min': binding.critical_min,
                'critical_max': binding.critical_max,
                'vote_group': binding.vote_group or None,
                'vote_minimum': binding.vote_minimum,
                'vote_confirmed': confirmed[binding.vote_group]
                if binding.vote_group
                else None,
                'unchanged_for_seconds': unchanged,
            },
        )
        raised.append(anomaly)

    _auto_resolve_threshold_anomalies(machine, holding, cleared, now=now)

    return raised


def _span(seconds: float) -> str:
    """A duration in the largest unit that still reads honestly."""
    for size, name in ((86400, 'day'), (3600, 'hour'), (60, 'minute')):
        if seconds >= size:
            count = int(seconds // size)
            return f'{count} {name}{"s" if count != 1 else ""}'
    return f'{int(seconds)} seconds'


def _auto_resolve_threshold_anomalies(machine, holding, cleared, *, now):
    """Close threshold anomalies that are no longer held open, saying why.

    ``holding`` is every condition that must stay open: still breaching, or
    reading badly enough that there is no evidence either way. ``cleared`` is
    every one that produced an affirmative in-limits reading.

    Only anomalies this detector raised are auto-resolved. A source-declared
    alarm is the source's to clear, and an operator-acknowledged condition is
    never closed on their behalf.

    Everything else is closed, including the cases no clearing reading was seen
    for. That is deliberate: this function is the only thing in the backend that
    writes ``RESOLVED``, so an anomaly it declines to close can never be closed
    at all, and it would hold its machine's unique open slot for ever. The
    protection is the note, not the refusal - the operator is told whether the
    condition was observed to end.
    """
    stale = MachineAnomaly.objects.filter(
        machine=machine, detector=THRESHOLD_DETECTOR, status=AnomalyStatus.OPEN
    ).exclude(fingerprint__in=holding)

    for anomaly in stale:
        anomaly.status = AnomalyStatus.RESOLVED
        anomaly.resolved_at = now
        anomaly.resolution_note = (
            RESOLUTION_IN_LIMITS
            if anomaly.fingerprint in cleared
            else RESOLUTION_UNASSESSABLE
        )
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
