"""Preliminary results from health evidence.

What this produces is explicitly *preliminary*: an evidence-backed reading of
what the telemetry currently shows, not a diagnosis. It becomes a diagnosis only
when a technician verifies it.

Three rules shape everything here:

* **Every claim cites a snapshot.** The analysis runs over immutable
  :class:`HealthEvidenceSnapshot` rows, and each observation it emits carries the
  snapshot id it came from plus whether that observation supports, contradicts or
  is merely related to the stated cause.
* **Missing and stale data are answers.** ``unavailable``, ``stale`` and
  ``insufficient`` are first-class statuses. The service never fills a gap with a
  guess, and it degrades its own confidence when the data behind it is old or of
  poor quality - a confident-looking cause built on hours-old telemetry is the
  failure mode this exists to prevent.
* **The hub that set the boundaries is authoritative about them.** When the
  source system declares its own alarm, whether the machine is out of limits is
  that system's determination, made against limits configured there. This report
  says so and stops hedging about it. When we inferred the condition from a
  threshold configured here, it says that instead. The distinction is confined
  to this report: an authoritative alarm still cannot satisfy a safety gate,
  mark a repair ready or count as a verified diagnosis.

Detection stays deterministic elsewhere; this only summarizes what has already
been observed. It cannot raise an alarm, satisfy a safety gate or mark a repair
ready.
"""

from __future__ import annotations

from django.utils import timezone

from assets.health_models import AnomalySeverity, SignalQuality, SnapshotReason
from repair.schema import (
    AUTHORITY_DERIVED,
    AUTHORITY_SOURCE_DECLARED,
    RELATION_SUPPORTS,
    RELATION_UNKNOWN,
    STATUS_AVAILABLE,
    STATUS_INSUFFICIENT,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
    coerce_diagnosis,
)

from .anomalies import SOURCE_ALARM_DETECTOR
from .snapshots import capture_anomaly_evidence

PROVIDER = 'machine_health.preliminary'
RULE_VERSION = '2'

#: Confidence ceilings by data condition. These are deliberately modest: this
#: analysis restates measurements, it does not reason about failure physics.
_CONFIDENCE_CLEAN = 0.6
_CONFIDENCE_DEGRADED = 0.3

#: Ceilings for an alarm the source system declared itself. Higher, because the
#: report is restating a determination the hub made against its own configured
#: boundaries rather than inferring one from a raw measurement. Still short of
#: the 'high' band: what caused the alarm remains unverified.
_CONFIDENCE_DECLARED = 0.75
_CONFIDENCE_DECLARED_DEGRADED = 0.45

#: A declared alarm with no usable telemetry of our own. The declaration is
#: still a fact - deliberately above ``_CONFIDENCE_DEGRADED``, because the hub
#: saying so outweighs an inference of ours drawn from bad data.
_CONFIDENCE_DECLARED_ONLY = 0.4


def _observation_text(snapshot) -> str:
    """Render one snapshot as a short, checkable sentence."""
    stats = snapshot.statistics or {}
    if not stats.get('available'):
        return f'{snapshot.signal_label}: no reading was available.'

    value = stats.get('latest')
    unit = f' {snapshot.unit}' if snapshot.unit else ''
    when = snapshot.window_end.isoformat() if snapshot.window_end else 'unknown time'
    suffix = ' (stale at capture)' if snapshot.stale else ''
    return f'{snapshot.signal_label} read {value}{unit} at {when}{suffix}.'


def _relation_for(snapshot) -> str:
    """Whether a snapshot supports the anomaly it was captured for.

    A snapshot with no usable reading supports nothing. Saying so keeps an empty
    measurement out of the "evidence for" column, where it would otherwise pad a
    thin case.
    """
    stats = snapshot.statistics or {}
    if not stats.get('available'):
        return RELATION_UNKNOWN
    if snapshot.stale or snapshot.quality in {SignalQuality.BAD, SignalQuality.UNKNOWN}:
        return RELATION_UNKNOWN
    return RELATION_SUPPORTS


def _authority_for(anomaly) -> tuple[str, str | None]:
    """Who decided this machine is outside its limits, and where they set them.

    A source-declared alarm carries the source system's own determination: the
    boundaries live in the hub the data comes from, and that hub owns the asset.
    Anything else is our threshold rule reading a number, which is an inference.

    An alarm whose source record is gone degrades to ``derived``. Without the
    hub we cannot name whose boundaries were applied, and an unattributable
    claim of authority is worth less than an honest inference.
    """
    if anomaly.detector != SOURCE_ALARM_DETECTOR or anomaly.source is None:
        return AUTHORITY_DERIVED, None

    return AUTHORITY_SOURCE_DECLARED, anomaly.source.name


def _confirm_tests(anomaly, snapshots, *, authority_source: str | None) -> list[str]:
    """Suggest checks a technician can run to confirm or rule out the cause.

    These are proposals for a human, never actions: nothing here is performed,
    scheduled or treated as satisfied.
    """
    tests = []

    missing = [s for s in snapshots if not (s.statistics or {}).get('available')]
    stale = [s for s in snapshots if s.stale and s not in missing]

    for snapshot in missing:
        tests.append(
            f'Take a manual reading of {snapshot.signal_label}; the source '
            'provided no value.'
        )
    for snapshot in stale:
        tests.append(
            f'Confirm {snapshot.signal_label} against a live local reading; the '
            'stored value was already stale when captured.'
        )

    if not snapshots:
        if authority_source:
            # The source is known - it declared the alarm. What is missing is a
            # local signal binding, so telling the technician to map a source
            # would point them at the wrong gap.
            alarm = anomaly.alarm_code or anomaly.title
            tests.append(
                f'Read {alarm} directly in {authority_source}; no signal here is '
                'bound to it, so this system holds no measurement of its own.'
            )
        else:
            tests.append(
                'Map this machine to a health source, or record a manual '
                'measurement, before relying on telemetry for this fault.'
            )

    for binding in anomaly.bindings.all():
        if binding.signal_kind:
            tests.append(
                f'Trend {binding.display_name} across a full duty cycle to '
                'separate a transient from a developing fault.'
            )

    return tests[:10]


def analyze_anomaly(anomaly, *, actor=None, capture=True, now=None) -> dict:
    """Build a preliminary, evidence-cited result for one anomaly.

    When ``capture`` is set, fresh snapshots are taken first so the analysis and
    its citations describe the same instant. Pass ``capture=False`` to re-read an
    existing evidence set without disturbing it.

    Returns a diagnosis-schema-v2 blob. It is always unverified: only a person
    can turn this into a diagnosis.
    """
    now = now or timezone.now()

    if capture:
        snapshots = capture_anomaly_evidence(
            anomaly, reason=SnapshotReason.AI_DIAGNOSIS, actor=actor, now=now
        )
    else:
        snapshots = list(anomaly.snapshots.order_by('captured_at'))

    evidence = [
        {
            'snapshot_id': str(snapshot.id),
            'observation': _observation_text(snapshot),
            'relation': _relation_for(snapshot),
            'signal_label': snapshot.signal_label,
            'unit': snapshot.unit,
            'observed_at': (
                snapshot.window_end.isoformat() if snapshot.window_end else None
            ),
            'quality': snapshot.quality,
            'stale': snapshot.stale,
        }
        for snapshot in snapshots
    ]

    # The four statuses are distinguished by *why* the data cannot be used, so
    # the reader learns whether to wait, re-measure or investigate manually.
    with_readings = [
        snapshot
        for snapshot in snapshots
        if (snapshot.statistics or {}).get('available')
    ]
    fresh = [snapshot for snapshot in with_readings if not snapshot.stale]
    usable = [snapshot for snapshot in fresh if snapshot.quality != SignalQuality.BAD]

    stale_count = sum(1 for snapshot in snapshots if snapshot.stale)
    bad_count = sum(
        1
        for snapshot in snapshots
        if snapshot.quality in {SignalQuality.BAD, SignalQuality.UNCERTAIN}
    )

    if not with_readings:
        # Either nothing is mapped, or every mapped signal has never reported.
        status = STATUS_UNAVAILABLE
    elif not fresh:
        status = STATUS_STALE
    elif not usable:
        status = STATUS_INSUFFICIENT
    else:
        status = STATUS_AVAILABLE

    authority, authority_source = _authority_for(anomaly)
    declared = authority == AUTHORITY_SOURCE_DECLARED

    degraded = bool(stale_count or bad_count)
    if status == STATUS_AVAILABLE:
        if declared:
            confidence = (
                _CONFIDENCE_DECLARED_DEGRADED if degraded else _CONFIDENCE_DECLARED
            )
        else:
            confidence = _CONFIDENCE_DEGRADED if degraded else _CONFIDENCE_CLEAN
    elif declared:
        # Our own telemetry is missing, stale or unusable - but the hub declared
        # the alarm against its own boundaries, and that has not stopped being
        # true. Reporting 0.0 here would read as "nothing is known".
        confidence = _CONFIDENCE_DECLARED_ONLY
    else:
        confidence = 0.0

    likely_cause = _likely_cause(
        anomaly, status, usable, declared=declared, authority_source=authority_source
    )

    window_start = min((s.window_start for s in snapshots), default=None)
    window_end = max((s.window_end for s in snapshots), default=None)

    return coerce_diagnosis({
        'likely_cause': likely_cause,
        'failure_mode': anomaly.alarm_code or None,
        'confidence': confidence,
        'alternatives': _alternatives(anomaly, status, declared=declared),
        'evidence': evidence,
        'confirm_tests': _confirm_tests(
            anomaly, snapshots, authority_source=authority_source
        ),
        'status': status,
        'authority': authority,
        'authority_source': authority_source,
        'data_window': {
            'start': window_start.isoformat() if window_start else None,
            'end': window_end.isoformat() if window_end else None,
            'snapshot_count': len(snapshots),
        },
        'freshness': {'stale': bool(stale_count), 'stale_signal_count': stale_count},
        'quality': {
            'summary': 'degraded' if degraded else 'good',
            'bad_signal_count': bad_count,
        },
        'provider': PROVIDER,
        'model_or_rule_version': RULE_VERSION,
        'generated_at': now.isoformat(),
        # Always false. Verification is a human act, recorded separately.
        'verified_by_user': False,
    })


def _declared_cause(anomaly, status: str, usable, authority_source: str) -> str:
    """State the source system's own alarm, and what we can add to it.

    The declaration is reported as fact, because it is one. Only our corrobora-
    ting telemetry is hedged - and its absence never downgrades the alarm to a
    maybe, because the boundaries it was raised against are configured in the
    hub, not here.
    """
    alarm = anomaly.alarm_code or anomaly.title
    lead = f'{authority_source} declared {alarm} on this machine.'

    if status == STATUS_AVAILABLE:
        labels = ', '.join(snapshot.signal_label for snapshot in usable)
        corroboration = f'The readings held here ({labels}) are consistent with it.'
    elif status == STATUS_STALE:
        corroboration = (
            'The readings held here are stale, so the alarm stands on the source '
            "system's determination alone."
        )
    elif status == STATUS_INSUFFICIENT:
        corroboration = (
            'The readings held here are not good enough to corroborate it, so the '
            "alarm stands on the source system's determination alone."
        )
    else:
        corroboration = (
            'No reading for it has reached this system, so the alarm stands on the '
            "source system's determination alone."
        )

    return (
        f'{lead} {corroboration} Whether the machine is outside its limits is that '
        "system's call - the boundaries are configured there. What caused the "
        'condition is not established here.'
    )


def _likely_cause(
    anomaly, status: str, usable, *, declared: bool, authority_source: str | None
) -> str:
    """State what the data shows, and say plainly when it shows nothing."""
    if declared and authority_source:
        return _declared_cause(anomaly, status, usable, authority_source)

    if status == STATUS_UNAVAILABLE:
        return (
            f'No telemetry evidence is available for "{anomaly.title}". '
            'This condition needs a manual assessment.'
        )
    if status == STATUS_STALE:
        return (
            f'The signals behind "{anomaly.title}" are stale, so the current '
            'condition cannot be established from telemetry alone.'
        )
    if status == STATUS_INSUFFICIENT:
        return (
            f'The available readings for "{anomaly.title}" are not sufficient to '
            'indicate a cause.'
        )

    labels = ', '.join(snapshot.signal_label for snapshot in usable)
    severity = (
        'a critical' if anomaly.severity == AnomalySeverity.CRITICAL else 'an abnormal'
    )
    return (
        f'{labels} show {severity} condition consistent with "{anomaly.title}". '
        'This is a restatement of the measurements, not a verified cause.'
    )


def _alternatives(anomaly, status: str, *, declared: bool) -> list[str]:
    """List explanations a technician should rule out before committing.

    A declared alarm gets these even with no local telemetry: there is a stated
    condition to explain either way. A derived one does not - with no usable
    data we have not established that anything is wrong, and listing reasons it
    might be would invent a finding.
    """
    if not declared and status != STATUS_AVAILABLE:
        return []

    alternatives = [
        'Instrumentation fault: the sensor or its wiring, rather than the machine.',
        'Process condition: an upstream change moved the machine outside its '
        'configured limits without a mechanical defect.',
    ]

    if declared:
        alternatives.append(
            'Threshold configuration in the source system: the limit it alarms on '
            'may no longer match how this machine is operated. Changing it is a '
            'change there, not here.'
        )
    else:
        alternatives.append(
            'Threshold configuration: the limit may no longer match how this '
            'machine is operated.'
        )

    return alternatives
