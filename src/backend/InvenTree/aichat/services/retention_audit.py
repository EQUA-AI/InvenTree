"""Bounded, content-free residual evidence for deleted threads.

Probes never repair data. Recording the report is a separate opt-in write;
neither a clean sample nor an empty sample qualifies an entire deployment.
"""

import json

from django.utils import timezone

from aichat.models import (
    AIRetentionOutbox,
    ChatThread,
    ChatThreadGrant,
    ChatThreadTombstone,
)
from aichat.services import retention

LAST_AUDIT_SETTING = '_AIMMS_RETENTION_LAST_AUDIT'
MAX_AUDIT_SAMPLE = 1000


def audit_deleted_threads(*, sample: int = 20, record: bool = False) -> dict:
    """Probe recent tombstones without printing ids, content or exception text."""
    if type(sample) is not int or not 1 <= sample <= MAX_AUDIT_SAMPLE:
        raise ValueError(f'sample must be between 1 and {MAX_AUDIT_SAMPLE}')
    started = timezone.now().isoformat()
    references = list(
        ChatThreadTombstone.objects.order_by('-deleted_at', '-pk').values_list(
            'thread_id', flat=True
        )[:sample]
    )
    residual: dict[str, int | None] = {'thread_root': 0, 'protected_grants': 0}
    errors: dict[str, int] = {}
    probes = {
        'thread_root': lambda ref: ChatThread.objects.filter(pk=ref).count(),
        'protected_grants': lambda ref: ChatThreadGrant.objects.filter(
            thread_id=ref
        ).count(),
        **{name: kind.probe for name, kind in retention.OUTBOX_KINDS.items()},
    }
    for name, probe in probes.items():
        total = 0
        for reference in references:
            try:
                total += retention.checked_residual(probe, reference)
            except Exception:
                errors[name] = errors.get(name, 0) + 1
        residual[name] = None if name in errors else total
    missing = retention.THREAD_DERIVATIVES.uncovered_models()
    outstanding = AIRetentionOutbox.objects.exclude(state='done')
    outbox = {
        'sample_outstanding': outstanding.filter(reference__in=references).count(),
        'sample_pending': outstanding.filter(
            reference__in=references, state='pending'
        ).count(),
        'failed_permanent': outstanding.filter(state='failed_permanent').count(),
        'unknown_kind_rows': outstanding.exclude(
            kind__in=retention.OUTBOX_KINDS
        ).count(),
    }
    dirty = bool(
        missing
        or errors
        or any(value != 0 for value in residual.values())
        or any(outbox.values())
    )
    report = {
        'schema_version': 1,
        'scope': 'deleted_threads',
        'started_at': started,
        'finished_at': timezone.now().isoformat(),
        'sample_limit': sample,
        'sampled_threads': len(references),
        'status': 'incomplete'
        if dirty
        else 'clean_sample'
        if references
        else 'no_samples',
        'residual_by_kind': residual,
        'probe_errors_by_kind': errors,
        'unregistered_models': missing,
        'exemptions': dict(retention.THREAD_DERIVATIVES.exemptions),
        'outbox': outbox,
    }
    if record:
        from common.models import InvenTreeSetting

        # A failed evidence write is not silently reported as recorded.
        InvenTreeSetting.set_setting(LAST_AUDIT_SETTING, json.dumps(report), None)
    return report


def last_audit() -> dict | None:
    """Return historical evidence with its timestamp; never infer freshness."""
    try:
        from common.models import InvenTreeSetting

        raw = InvenTreeSetting.get_setting(LAST_AUDIT_SETTING, '')
        report = json.loads(raw) if raw else None
        return (
            report
            if isinstance(report, dict) and report.get('schema_version') == 1
            else None
        )
    except Exception:
        return None
