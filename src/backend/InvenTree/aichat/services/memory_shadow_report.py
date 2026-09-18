"""Content-free extraction/worker observations with explicit unmeasured gates."""

import math

from django.db.models import Count, Sum
from django.utils import timezone

from aichat.models import AIWorkerUsageEvent, MemoryExtractionRun

MAX_RUNS = 10000
COUNTERS = (
    'n_input_messages',
    'n_proposals',
    'n_rejected',
    'n_rejected_vocabulary',
    'n_topics_dropped',
    'n_shield_flagged',
    'n_shield_unavailable',
)


def report_window(*, since, until):
    """Bound a closed interval to retained detail; never turn absence into a pass."""
    if (
        timezone.is_naive(since)
        or timezone.is_naive(until)
        or since >= until
        or (until - since).total_seconds() > 90 * 86400
        or until > timezone.now()
    ):
        raise ValueError('Report requires an aware ordered window of at most 90 days')
    rows = MemoryExtractionRun.objects.filter(
        created_at__gte=since, created_at__lt=until
    )
    total = rows.count()
    outcomes = dict(rows.values_list('outcome').annotate(total=Count('pk')))
    counts = rows.aggregate(**{name: Sum(name) for name in COUNTERS})
    # Completed runs currently record wall latency; zero is unmeasured, not fast.
    latency = list(
        rows
        .filter(latency_ms__gt=0)
        .order_by('latency_ms')
        .values_list('latency_ms', flat=True)[: MAX_RUNS + 1]
    )
    bounded = len(latency) <= MAX_RUNS
    p95 = (
        latency[max(0, math.ceil(len(latency) * 0.95) - 1)]
        if latency and bounded
        else None
    )
    ledger = AIWorkerUsageEvent.objects.filter(
        created_at__gte=since,
        created_at__lt=until,
        task__in=[
            'memory_extraction',
            'memory_extraction_reserved',
            'memory_embedding',
            'memory_embedding_reserved',
        ],
    )
    usage = []
    for row in (
        ledger
        .values('task', 'purpose')
        .annotate(
            events=Count('pk'),
            input_tokens=Sum('input_tokens'),
            output_tokens=Sum('output_tokens'),
            attempts=Sum('attempts'),
        )
        .order_by('task', 'purpose')
    ):
        usage.append({
            **row,
            'estimated_upper_bound': row['task'].endswith('_reserved'),
        })
    return {
        'schema_version': 1,
        'window': {'since': since.isoformat(), 'until': until.isoformat()},
        'scope': 'custom_extractor_and_memory_worker',
        'runs': total,
        'outcomes': outcomes,
        'counts': {key: counts[key] or 0 for key in COUNTERS},
        'observed_complete_fraction': outcomes.get('complete', 0) / total
        if total
        else None,
        'latency': {
            'p95_ms': p95,
            'measured_runs': len(latency) if bounded else None,
            'truncated': not bounded,
            'includes_unmeasured_zero_runs': False,
        },
        'worker_usage': usage,
        'memory_gate_results': {
            name: {'status': 'not_measured', 'value': None}
            for name in (
                'reviewer_precision',
                'reviewer_recall',
                'cross_client_errors',
                'injection_proposals',
                'extraction_error_rate',
                'queue_p95_lag',
                'rag_head_of_line_events',
                'tokens_per_confirmed_fact',
                'mem0_admission',
            )
        },
        'evidence_limits': [
            'detail_can_be_removed_by_retention_or_user_erasure',
            'deferred_outcomes_include_budget_and_provider_conditions',
            'run_latency_is_not_historical_queue_lag',
            'worker_ledger_window_is_not_an_exact_join_to_finished_runs',
            'unknown_provider_usage_keeps_reserved_upper_bounds',
            'human_quality_and_hard_zero_gates_need_separate_reviewed_evidence',
        ],
        'decision': 'not_qualified',
    }
