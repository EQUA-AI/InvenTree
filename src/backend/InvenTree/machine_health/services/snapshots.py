"""Immutable evidence capture.

A snapshot is the citation behind a preliminary result or an evidence-backed
repair. Capturing one freezes what was observed, when, from where and how good
the data was, so that a decision made today stays reconstructable after the live
signal has moved on. Snapshots are never edited; a correction is a new capture.
"""

from __future__ import annotations

import hashlib
import json

from django.utils import timezone

from assets.health_models import (
    HealthEvidenceSnapshot,
    MachineSignalBinding,
    SignalQuality,
    SnapshotReason,
)

#: A snapshot is evidence for one decision, not a data export.
MAX_SNAPSHOT_SAMPLES = 500


class SnapshotError(Exception):
    """The requested snapshot cannot be captured."""

    code = 'SNAPSHOT_INVALID'


def _content_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode()
    ).hexdigest()


def capture_current_signal(
    binding: MachineSignalBinding,
    *,
    reason: str,
    anomaly=None,
    actor=None,
    system_actor: str = '',
    now=None,
) -> HealthEvidenceSnapshot:
    """Capture the binding's current reading as immutable evidence.

    A missing or stale reading is captured as such rather than skipped: "we had
    no fresh data at the moment of the decision" is itself evidence, and hiding
    it would let a repair look better supported than it was.
    """
    if reason not in SnapshotReason.values:
        raise SnapshotError(f'Unknown snapshot reason {reason!r}.')

    now = now or timezone.now()
    state = getattr(binding, 'state', None)
    threshold = binding.source.freshness_threshold_seconds

    if state is None:
        window_start = window_end = now
        samples = []
        statistics = {'available': False}
        quality = SignalQuality.UNKNOWN
        stale = True
    else:
        window_start = window_end = state.observed_at
        value = (state.value or {}).get('value')
        samples = [{'observed_at': state.observed_at.isoformat(), 'value': value}]
        statistics = {'available': True, 'latest': value}
        quality = state.quality
        stale = state.is_stale(threshold, now=now)

    payload = {
        'binding': binding.pk,
        'external_key': binding.external_key,
        'samples': samples,
        'statistics': statistics,
        'quality': quality,
        'stale': stale,
    }

    return HealthEvidenceSnapshot.objects.create(
        machine=binding.machine,
        anomaly=anomaly,
        source=binding.source,
        binding=binding,
        signal_label=binding.display_name,
        unit=binding.unit,
        window_start=window_start,
        window_end=window_end,
        captured_at=now,
        samples=samples,
        statistics=statistics,
        quality=quality,
        stale=stale,
        reason=reason,
        source_references={
            'source': binding.source.name,
            'source_type': binding.source.source_type,
            'external_key': binding.external_key,
        },
        content_hash=_content_hash(payload),
        created_by=actor if getattr(actor, 'pk', None) else None,
        system_actor=system_actor[:64],
    )


def capture_anomaly_evidence(
    anomaly, *, reason: str = SnapshotReason.ANOMALY_REPAIR, actor=None, now=None
) -> list[HealthEvidenceSnapshot]:
    """Capture one snapshot per signal implicated in an anomaly.

    Returns the captured snapshots in binding order. An anomaly with no mapped
    signals yields an empty list; the caller must present that as "no telemetry
    evidence available", never as a clean bill of health.
    """
    bindings = list(
        anomaly.bindings.select_related('source', 'machine', 'state').order_by(
            'display_name'
        )
    )

    return [
        capture_current_signal(
            binding, reason=reason, anomaly=anomaly, actor=actor, now=now
        )
        for binding in bindings
    ]


def snapshots_for_machine(machine, *, limit: int = 50):
    """Return the machine's most recent evidence snapshots."""
    return (
        HealthEvidenceSnapshot.objects
        .filter(machine=machine)
        .select_related('binding', 'source', 'anomaly')
        .order_by('-captured_at')[: min(limit, MAX_SNAPSHOT_SAMPLES)]
    )
