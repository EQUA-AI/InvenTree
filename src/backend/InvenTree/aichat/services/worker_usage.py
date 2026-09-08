"""M2 §8.4: the worker-side usage ledger and its daily token caps (GR-29).

Ledger = spend record (``AIWorkerUsageEvent``, one row per run);
``ChatCompactionEvent`` = per-event diagnostic. Both read the same
``response.usage`` from one ``_summarize`` return, so nothing is counted
twice. Every helper here is fail-soft: a ledger write that fails is logged
(exception class only) and never raised, and a cap read that fails reads
as "no cap" — spend accounting must never break compaction.

Value-free by construction: rows and log lines carry a purpose code, a
deployment name, a task name, ids, counts and timestamps — never content.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.db.models import Count, Sum
from django.utils import timezone

from aichat.models import AIWorkerUsageEvent, AIWorkerUsagePurpose

logger = logging.getLogger('inventree')

#: The ``task`` stamp for the S38 compaction job.
TASK_COMPACT_THREAD_SUMMARY = 'compact_thread_summary'

#: ``ChatCompactionEvent.error_code`` for a ``budget_deferred`` row.
ERROR_CODE_DAILY_CAP = 'daily_cap'

#: Purpose -> the ``ai.core.config.Settings`` field holding its daily cap.
_CAP_FIELDS = {
    AIWorkerUsagePurpose.SUMMARIZATION: 'aimms_worker_daily_token_cap_summarization',
    AIWorkerUsagePurpose.EXTRACTION: 'aimms_worker_daily_token_cap_extraction',
}


def _int_or_zero(value) -> int:
    """Coerce a counter to a non-negative int; anything unusable counts as 0."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def record_worker_usage(
    purpose: str,
    deployment: str,
    *,
    task: str = '',
    thread_id=None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    attempts: int = 1,
) -> AIWorkerUsageEvent | None:
    """Write one ledger row for a worker run; never raises.

    ``attempts`` is the number of model calls the run made (a §8.5.3 bisect
    makes up to three); a run that made no call (``attempts <= 0``) writes
    nothing, while a failed call with zero tokens still lands a row so the
    ledger shows the call happened. Returns the row, or ``None`` when
    nothing was written (skipped or the write failed).
    """
    calls = _int_or_zero(attempts)
    if calls <= 0:
        return None
    try:
        return AIWorkerUsageEvent.objects.create(
            purpose=str(purpose)[:24],
            task=str(task or '')[:64],
            thread_id=thread_id,
            deployment=str(deployment or '')[:128],
            input_tokens=_int_or_zero(input_tokens),
            output_tokens=_int_or_zero(output_tokens),
            attempts=min(calls, 32767),
        )
    except Exception as exc:
        logger.warning(
            'Worker usage ledger write failed purpose=%s thread=%s error=%s',
            purpose,
            thread_id,
            type(exc).__name__,
        )
        return None


def utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The half-open ``[start, end)`` of the current UTC day."""
    current = (now or timezone.now()).astimezone(dt_timezone.utc)
    start = datetime.combine(current.date(), time.min, tzinfo=dt_timezone.utc)
    return start, start + timedelta(days=1)


def usage_today(purpose: str, *, now: datetime | None = None) -> dict:
    """Token and row totals for ``purpose`` over the current UTC day.

    Returns ``{'input_tokens', 'output_tokens', 'rows'}``. A pure read:
    database errors propagate (``daily_cap_status`` is the fail-soft
    wrapper the task uses).
    """
    start, end = utc_day_bounds(now)
    totals = AIWorkerUsageEvent.objects.filter(
        purpose=purpose, created_at__gte=start, created_at__lt=end
    ).aggregate(
        input_tokens=Sum('input_tokens'),
        output_tokens=Sum('output_tokens'),
        rows=Count('pk'),
    )
    return {
        'input_tokens': _int_or_zero(totals.get('input_tokens')),
        'output_tokens': _int_or_zero(totals.get('output_tokens')),
        'rows': _int_or_zero(totals.get('rows')),
    }


def cap_for(purpose: str, settings) -> int:
    """The daily token cap for ``purpose`` from the AI settings; 0 = unlimited."""
    field = _CAP_FIELDS.get(purpose)
    if field is None:
        return 0
    return _int_or_zero(getattr(settings, field, 0))


def daily_cap_status(purpose: str, settings=None) -> dict:
    """Whether ``purpose`` has reached its daily cap; never raises.

    Returns ``{'cap', 'used', 'reached'}``. ``cap == 0`` means unlimited
    and short-circuits without a database read. A failed read is logged
    (exception class only) and reads as ``used=0, reached=False`` — the
    cap is advisory backpressure, not a correctness gate.
    """
    if settings is None:
        from ai.core.config import get_settings

        settings = get_settings()
    cap = cap_for(purpose, settings)
    if cap <= 0:
        return {'cap': 0, 'used': 0, 'reached': False}
    try:
        today = usage_today(purpose)
    except Exception as exc:
        logger.warning(
            'Worker usage cap read failed purpose=%s error=%s; treating as no cap',
            purpose,
            type(exc).__name__,
        )
        return {'cap': cap, 'used': 0, 'reached': False}
    used = today['input_tokens'] + today['output_tokens']
    return {'cap': cap, 'used': used, 'reached': used >= cap}


__all__ = [
    'ERROR_CODE_DAILY_CAP',
    'TASK_COMPACT_THREAD_SUMMARY',
    'cap_for',
    'daily_cap_status',
    'record_worker_usage',
    'usage_today',
    'utc_day_bounds',
]
