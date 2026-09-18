"""Fail-closed worker spend reservations in the existing usage ledger.

Rows stamped memory_extraction_reserved are conservative upper bounds until a
known provider response replaces them. Unknown outcomes/crashes keep the bound;
there is no silent budget refund. These rows are counted once, never alongside a
second usage record for the same call. Reports must distinguish estimates.
"""

from django.db import connection, transaction
from django.db.models import Sum

from aichat.models import AIWorkerUsageEvent
from aichat.services.worker_usage import utc_day_bounds

RESERVATION_TASK = 'memory_extraction_reserved'
COMPLETED_TASK = 'memory_extraction'
_BUDGET_LOCK = 731498204


@transaction.atomic
def reserve(*, tokens, deployment, thread_id, settings, purpose='extraction'):
    """Serialize global extraction admission across every producer/consumer."""
    if (
        purpose not in {'extraction', 'embedding'}
        or connection.vendor != 'postgresql'
        or type(tokens) is not int
        or tokens < 1
    ):
        raise ValueError('Memory budget unavailable')
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [_BUDGET_LOCK])
    start, end = utc_day_bounds()
    totals = AIWorkerUsageEvent.objects.filter(
        purpose=purpose, created_at__gte=start, created_at__lt=end
    ).aggregate(input=Sum('input_tokens'), output=Sum('output_tokens'))
    used = (totals['input'] or 0) + (totals['output'] or 0)
    cap = getattr(settings, f'aimms_worker_daily_token_cap_{purpose}')
    if cap > 0 and used + tokens > cap:
        return None
    return AIWorkerUsageEvent.objects.create(
        purpose=purpose,
        task=f'memory_{purpose}_reserved',
        deployment=deployment[:128],
        thread_id=thread_id,
        input_tokens=tokens,
        output_tokens=0,
        attempts=1,
    ).pk


def settle(reservation_id, result):
    """Only known usage replaces a reservation; a pre-call refusal releases it."""
    rows = AIWorkerUsageEvent.objects.filter(
        pk=reservation_id,
        task__in=['memory_extraction_reserved', 'memory_embedding_reserved'],
    )
    if not result.attempted:
        rows.delete()
    elif result.usage_known:
        row = rows.first()
        if row is None:
            return
        rows.update(
            task=f'memory_{row.purpose}',
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
