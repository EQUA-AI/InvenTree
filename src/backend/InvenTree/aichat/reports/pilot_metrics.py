"""Shared §8.10 metric aggregation (S15) — read-only, content-free.

Every function reads persisted rows (``ChatTurn`` states and timestamps,
``ChatMessage.metadata`` blobs, ``RetrievalMiss``, ``AIRequestRejection``)
and returns counts, rates, codes, and latencies — never message content,
prompts, or excerpts. ``pilot_ops_report`` (daily/weekly review) and
``shadow_review_report`` (§16, run-scoped) compose these sections.

Known, named gaps (owner-ack items): per-stage latency is not persisted
(totals only — ``completed_at - created_at``); shadow-mode budget
``would_block`` exists only in logs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

#: Fallback mirror of ai.core.analysis.intent.ANALYSIS_INTENTS (lazy import
#: preferred; the report must not break if the ai package is unimportable).
_ANALYSIS_INTENTS_FALLBACK = (
    'source_inventory',
    'manual_fact',
    'record_retrieval',
    'fleet_aggregate',
    'trend_analysis',
    'manual_wo_comparison',
)

#: Boundary/refusal workflows subject to the ≤200-word gate (Q86).
SAFETY_LENGTH_WORKFLOWS = ('safety_refusal', 'advisory_intent', 'analysis_unavailable')

SAFETY_WORD_CAP = 200


def analysis_intents() -> tuple[str, ...]:
    """The ANALYSIS intent set, from the ai plane when importable."""
    try:
        from ai.core.analysis.intent import ANALYSIS_INTENTS

        return tuple(ANALYSIS_INTENTS)
    except Exception:
        return _ANALYSIS_INTENTS_FALLBACK


def percentiles(values: list[float]) -> dict[str, float | int | None]:
    """p50/p95/p99/max by sorted-list interpolation (stdlib only)."""
    if not values:
        return {'p50': None, 'p95': None, 'p99': None, 'max': None, 'n': 0}
    ordered = sorted(values)

    def _at(fraction: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = fraction * (len(ordered) - 1)
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        weight = position - low
        return ordered[low] * (1 - weight) + ordered[high] * weight

    return {
        'p50': round(_at(0.50), 3),
        'p95': round(_at(0.95), 3),
        'p99': round(_at(0.99), 3),
        'max': round(ordered[-1], 3),
        'n': len(ordered),
    }


def turn_and_latency_stats(since) -> dict[str, Any]:
    """Turn-state distribution and total-latency percentiles by modality."""
    from aichat.models import ChatTurn

    states: Counter[str] = Counter()
    latencies: dict[str, list[float]] = defaultdict(list)
    stuck = 0
    from django.utils import timezone

    hour_ago = timezone.now() - timezone.timedelta(hours=1)
    rows = ChatTurn.objects.filter(created_at__gte=since).values(
        'state', 'modality', 'created_at', 'completed_at'
    )
    total = 0
    for row in rows:
        total += 1
        states[str(row['state'])] += 1
        if row['state'] == 'running' and row['created_at'] < hour_ago:
            stuck += 1
        if row['completed_at'] is not None:
            latencies[str(row['modality'] or 'text')].append(
                (row['completed_at'] - row['created_at']).total_seconds()
            )
    incomplete = states.get('incomplete', 0) + states.get('failed', 0)
    return {
        'total': total,
        'states': dict(states),
        'stuck_running_over_1h': stuck,
        'incomplete_or_failed_rate': round(incomplete / total, 4) if total else None,
        'latency_s': {
            modality: percentiles(values) for modality, values in latencies.items()
        },
    }


def route_stats(since) -> dict[str, Any]:
    """Workflow distribution + shadow/enforce routing divergence.

    Divergence is DERIVED from the persisted route record: an ANALYSIS
    task intent, read-only effect, on a text turn, whose route mode is not
    'analysis' — the enforce flip would have routed it differently.
    """
    from aichat.models import ChatTurn

    intents = set(analysis_intents())
    workflows: Counter[str] = Counter()
    divergent = 0
    divergent_by_intent: Counter[str] = Counter()
    routed = 0
    rows = ChatTurn.objects.filter(created_at__gte=since).values(
        'modality', 'canonical_result'
    )
    for row in rows:
        canonical = row['canonical_result'] or {}
        if not isinstance(canonical, dict):
            continue
        workflows[str(canonical.get('workflow_used') or 'unknown')] += 1
        route = canonical.get('route')
        if not isinstance(route, dict):
            continue
        routed += 1
        task_intent = str(route.get('task_intent') or '')
        if (
            task_intent in intents
            and str(route.get('effect_intent') or 'read_only') == 'read_only'
            and str(row['modality'] or 'text') == 'text'
            and str(route.get('mode') or '') != 'analysis'
        ):
            divergent += 1
            divergent_by_intent[task_intent] += 1
    return {
        'workflow_distribution': dict(workflows),
        'turns_with_route': routed,
        'divergence': {
            'count': divergent,
            'rate': round(divergent / routed, 4) if routed else None,
            'by_intent': dict(divergent_by_intent),
        },
    }


def scope_stats(since) -> dict[str, Any]:
    """Scope-rejection rates from the RetrievalMiss telemetry."""
    from aichat.models import RetrievalMiss

    rows = RetrievalMiss.objects.filter(created_at__gte=since).values(
        'scope_mode', 'scope_enforced', 'out_of_scope_hits', 'corpus'
    )
    scoped = 0
    with_hits = 0
    shadow_hits = 0
    enforced_hits = 0
    by_corpus: Counter[str] = Counter()
    for row in rows:
        if not row['scope_mode']:
            continue
        scoped += 1
        if row['out_of_scope_hits']:
            with_hits += 1
            by_corpus[str(row['corpus'] or 'unknown')] += 1
            if row['scope_enforced']:
                enforced_hits += 1
            else:
                shadow_hits += 1
    return {
        'scoped_searches': scoped,
        'out_of_scope_rate': round(with_hits / scoped, 4) if scoped else None,
        'shadow_would_reject': shadow_hits,
        'enforced_filtered': enforced_hits,
        'by_corpus': dict(by_corpus),
    }


def coverage_stats(since) -> dict[str, Any]:
    """Complete-population coverage over persisted evidence attachments."""
    from aichat.models import ChatMessage

    rows = ChatMessage.objects.filter(
        metadata__has_key='evidence_analysis', created_at__gte=since
    ).values('metadata')
    total = 0
    complete = 0
    populations: list[float] = []
    for row in rows:
        attachment = (row['metadata'] or {}).get('evidence_analysis') or {}
        coverage = attachment.get('coverage')
        if not isinstance(coverage, dict):
            continue
        total += 1
        if coverage.get('complete_population'):
            complete += 1
        if isinstance(coverage.get('population_count'), int):
            populations.append(float(coverage['population_count']))
    return {
        'answers_with_coverage': total,
        'complete_population_rate': round(complete / total, 4) if total else None,
        'population_count': percentiles(populations),
    }


def validator_stats(since) -> dict[str, Any]:
    """Gate verdicts and would-fail codes from the evidence_gate blobs."""
    from aichat.models import ChatMessage

    rows = ChatMessage.objects.filter(
        metadata__has_key='evidence_gate', created_at__gte=since
    ).values('metadata')
    verdicts: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    by_intent: Counter[str] = Counter()
    prose_scans = 0
    rehearsals = 0
    for row in rows:
        blob = (row['metadata'] or {}).get('evidence_gate')
        if not isinstance(blob, dict):
            continue
        if blob.get('mode') == 'shadow_rehearsal':
            rehearsals += 1
            verdicts[str(blob.get('verdict') or 'unknown')] += 1
            for code in blob.get('codes') or []:
                codes[str(code)] += 1
            continue
        prose_scans += 1
        fail_codes = blob.get('would_fail') or []
        for code in fail_codes:
            codes[str(code)] += 1
        if fail_codes:
            by_intent[str(blob.get('intent') or 'unknown')] += 1
    return {
        'prose_scans': prose_scans,
        'rehearsals': rehearsals,
        'verdicts': dict(verdicts),
        'would_fail_codes': dict(codes),
        'would_fail_by_intent': dict(by_intent),
    }


def grounding_stats(since) -> dict[str, Any]:
    """Citation/entity mismatch signals from the grounding blobs."""
    from aichat.models import ChatMessage

    rows = ChatMessage.objects.filter(
        metadata__has_key='grounding', created_at__gte=since
    ).values('metadata')
    total = 0
    would_downgrade = 0
    verdicts: Counter[str] = Counter()
    for row in rows:
        blob = (row['metadata'] or {}).get('grounding')
        if not isinstance(blob, dict):
            continue
        total += 1
        verdict = str(blob.get('verdict') or blob.get('audit_verdict') or 'unknown')
        verdicts[verdict] += 1
        if blob.get('would_downgrade') or verdict in ('downgrade', 'would_downgrade'):
            would_downgrade += 1
    return {
        'turns_with_grounding': total,
        'verdicts': dict(verdicts),
        'mismatch_rate': round(would_downgrade / total, 4) if total else None,
    }


def safety_length_stats(since) -> dict[str, Any]:
    """The ≤200-word gate on refusal/boundary responses (counts only)."""
    from aichat.models import ChatTurn

    checked = 0
    violations = 0
    rows = ChatTurn.objects.filter(created_at__gte=since).select_related(
        'output_message'
    )
    for turn in rows:
        canonical = turn.canonical_result or {}
        if not isinstance(canonical, dict):
            continue
        if str(canonical.get('workflow_used') or '') not in SAFETY_LENGTH_WORKFLOWS:
            continue
        message = turn.output_message
        if message is None or not message.content:
            continue
        checked += 1
        if len(message.content.split()) > SAFETY_WORD_CAP:
            violations += 1
    return {'checked': checked, 'violations': violations}


def usage_stats(since) -> dict[str, Any]:
    """Token totals from the persisted usage blobs."""
    from aichat.models import ChatMessage

    totals: Counter[str] = Counter()
    turns = 0
    rows = ChatMessage.objects.filter(
        metadata__has_key='usage', created_at__gte=since
    ).values('metadata')
    for row in rows:
        usage = (row['metadata'] or {}).get('usage') or {}
        blob = usage.get('totals') or {}
        if not isinstance(blob, dict):
            continue
        turns += 1
        for key, value in blob.items():
            if isinstance(value, int):
                totals[str(key)] += value
    return {'turns_with_usage': turns, 'totals': dict(totals)}


def rejection_stats(since) -> dict[str, Any]:
    """Typed pre-turn rejections (the §8.10 denominator gap-filler)."""
    from aichat.models import AIRequestRejection

    codes: Counter[str] = Counter()
    for row in AIRequestRejection.objects.filter(created_at__gte=since).values('code'):
        codes[str(row['code'])] += 1
    return {'total': sum(codes.values()), 'by_code': dict(codes)}


def applicability_stats() -> dict[str, Any]:
    """Applicability posture: the provenance proxy plus the S8b relation.

    The blank-asset-binding proxy stays (ingest provenance still matters
    for the backfill queue); ``verified_rows_by_kind`` counts LIVE
    verified claims, and ``proposed_rows`` is the human queue.
    """
    from django.db.models import Count

    from aichat.models import ControlledDocument, ControlledDocumentApplicability

    unresolved: Counter[str] = Counter()
    total_current = 0
    for row in ControlledDocument.objects.filter(is_current=True).values(
        'document_class', 'asset_id'
    ):
        total_current += 1
        if not row['asset_id']:
            unresolved[str(row['document_class'] or 'unknown')] += 1
    verified_by_kind = {
        entry['kind']: entry['n']
        for entry in ControlledDocumentApplicability.objects
        .filter(state='verified')
        .values('kind')
        .annotate(n=Count('pk'))
        .order_by('kind')
    }
    proposed = ControlledDocumentApplicability.objects.filter(state='proposed').count()
    return {
        'current_documents': total_current,
        'blank_asset_binding': sum(unresolved.values()),
        'by_class': dict(unresolved),
        'verified_rows_by_kind': verified_by_kind,
        'proposed_rows': proposed,
        'note': 'blank bindings are ingest provenance; verified rows are the S8b relation',
    }


def latch_state() -> dict[str, Any]:
    """The pilot-stop latch header for every report."""
    try:
        from aichat.services.pilot_latch import current_state

        return current_state()
    except Exception:
        return {'latched': None, 'reason_code': 'unreadable'}


def retention_stats() -> dict[str, Any]:
    """S16 purge-job health: last-run age, backlog, outbox failures.

    The derivable half of release gate 11 (retention readiness): a human
    still attests that FEATURE_AI_RETENTION_JOBS is on and green in the
    target deployment; this section is the evidence they read.
    """
    from aichat.services.retention import retention_status

    status = retention_status()
    receipt = status.pop('last_run') or {}
    status['last_run_errors'] = sorted((receipt.get('errors') or {}).keys())
    return status


__all__ = [
    'SAFETY_LENGTH_WORKFLOWS',
    'SAFETY_WORD_CAP',
    'analysis_intents',
    'applicability_stats',
    'coverage_stats',
    'grounding_stats',
    'latch_state',
    'percentiles',
    'rejection_stats',
    'retention_stats',
    'route_stats',
    'safety_length_stats',
    'scope_stats',
    'turn_and_latency_stats',
    'usage_stats',
    'validator_stats',
]
