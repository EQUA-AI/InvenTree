"""M2 §8.3 soak read over the ChatCompactionEvent ledger (read-only).

Prints per-outcome counts, the six threshold metrics and a PASS/FAIL verdict
for the window; ``--json`` emits one object with the same fields. Output is
value-free: counts, rates, milliseconds and enum codes — thread ids never
appear (a stuck thread is a count, not a name). Exit codes: 0 PASS, 1 FAIL,
2 when the window holds no terminal event (``no_data``).

Threshold vocabulary (plan §8.3, GR-47): failure_rate < 1 %, content-filter
stuck threads = 0, cap_hit_rate < 5 %, race_rate < 2 %, started rows without
a terminal outcome older than 15 minutes = 0, p95 latency < 60 s.

``skipped`` rows (the §8.7 posture row: flags off on the executing worker,
``error_code=flags_off``) never ran the summarizer, so they sit outside
every rate's numerator AND denominator and are reported separately as
``skipped_total`` / ``flags_off``; a web/worker flag-parity gap must not
read as summarizer breakage.

``budget_deferred`` rows (§8.4: today's worker ledger reached the daily
cap, ``error_code=daily_cap``) are posture rows too, not runs: the task
returned before any model call, advanced no watermark and carries no
latency or tokens. Once the cap trips, every later turn on a thread with
backlog re-enqueues and lands another such row for the rest of the UTC
day, so they are kept out of every rate's denominator and out of the
content-filter stuck history, and are reported separately as
``budget_deferred``. A window of nothing but deferrals is ``no_data``.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aichat.models import ChatCompactionEvent, ChatCompactionOutcome
from aichat.tasks import COMPACTION_STARTED_STALE_MINUTES

#: (metric, threshold, comparator) — the §8.3 table.
THRESHOLDS: tuple[tuple[str, float, str], ...] = (
    ('failure_rate', 0.01, 'lt'),
    ('content_filter_stuck', 0, 'eq'),
    ('cap_hit_rate', 0.05, 'lt'),
    ('race_rate', 0.02, 'lt'),
    ('started_without_terminal', 0, 'eq'),
    ('latency_p95_ms', 60_000, 'lt'),
)

#: Outcomes that are neither ``started`` nor a summarizer run: excluded from
#: the rate denominators (§8.3 reads ``outcome=failed`` / all terminal, and a
#: posture skip or a daily-cap deferral is not a terminal summarize result).
NON_RUN_OUTCOMES = (
    ChatCompactionOutcome.STARTED,
    ChatCompactionOutcome.SKIPPED,
    ChatCompactionOutcome.BUDGET_DEFERRED,
)

TERMINAL_OUTCOMES = tuple(
    value for value in ChatCompactionOutcome.values if value not in NON_RUN_OUTCOMES
)

#: ``error_code`` stamped on a ``skipped`` row by the §8.7 in-body re-check.
FLAGS_OFF_ERROR_CODE = 'flags_off'


def percentile(values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile of ``values``; None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return int(ordered[rank - 1])


def _passes(value, threshold: float, comparator: str) -> bool:
    """Apply one threshold; a missing value (no ok rows) does not fail it."""
    if value is None:
        return True
    return value < threshold if comparator == 'lt' else value == threshold


def build_report(days: int, *, now=None) -> dict:
    """Aggregate the window into the report dict (pure over the queryset)."""
    now = now or timezone.now()
    since = now - timedelta(days=max(1, days))
    rows = list(
        ChatCompactionEvent.objects
        .filter(started_at__gte=since)
        .order_by('started_at')
        .values(
            'thread_id',
            'started_at',
            'outcome',
            'error_code',
            'cap_hit',
            'latency_ms',
            'input_tokens',
            'output_tokens',
            'directives_stripped',
            'directives_flagged',
            'entropy_flags',
        )
    )

    outcomes: Counter[str] = Counter(dict.fromkeys(ChatCompactionOutcome.values, 0))
    terminal = 0
    cap_hits = 0
    latencies: list[int] = []
    input_tokens = 0
    output_tokens = 0
    # M2 PR 4 content-control counts (§8.5.2 / §5.9): informational, no
    # threshold — the scrub and the entropy shadow are measured, not gated.
    # All three columns are per-run deltas, so the window totals are sums.
    directives_stripped = 0
    directives_flagged = 0
    entropy_flags = 0
    stale_started = 0
    skipped = 0
    flags_off = 0
    budget_deferred = 0
    stale_before = now - timedelta(minutes=COMPACTION_STARTED_STALE_MINUTES)
    per_thread: dict = defaultdict(list)

    for row in rows:
        outcome = str(row['outcome'])
        outcomes[outcome] += 1
        if outcome == ChatCompactionOutcome.STARTED:
            if row['started_at'] < stale_before:
                stale_started += 1
            continue
        if outcome == ChatCompactionOutcome.SKIPPED:
            skipped += 1
            if str(row['error_code'] or '') == FLAGS_OFF_ERROR_CODE:
                flags_off += 1
            continue
        if outcome == ChatCompactionOutcome.BUDGET_DEFERRED:
            budget_deferred += 1
            continue
        terminal += 1
        input_tokens += int(row['input_tokens'] or 0)
        output_tokens += int(row['output_tokens'] or 0)
        directives_stripped += int(row['directives_stripped'] or 0)
        directives_flagged += int(row['directives_flagged'] or 0)
        entropy_flags += int(row['entropy_flags'] or 0)
        if row['cap_hit']:
            cap_hits += 1
        if outcome == ChatCompactionOutcome.OK:
            latencies.append(int(row['latency_ms'] or 0))
        per_thread[row['thread_id']].append(outcome)

    stuck = sum(
        1
        for history in per_thread.values()
        if len(history) >= 2
        and all(item == ChatCompactionOutcome.CONTENT_FILTER for item in history[-2:])
    )

    def rate(count: int) -> float | None:
        return round(count / terminal, 4) if terminal else None

    metrics = {
        'failure_rate': rate(outcomes[ChatCompactionOutcome.FAILED]),
        'content_filter_stuck': stuck,
        'cap_hit_rate': rate(cap_hits),
        'race_rate': rate(outcomes[ChatCompactionOutcome.RACE_LOST]),
        'started_without_terminal': stale_started,
        'latency_p95_ms': percentile(latencies, 95),
    }
    thresholds = {
        name: {
            'value': metrics[name],
            'threshold': threshold,
            'comparator': comparator,
            'pass': _passes(metrics[name], threshold, comparator),
        }
        for name, threshold, comparator in THRESHOLDS
    }
    if terminal == 0:
        verdict = 'no_data'
    elif all(item['pass'] for item in thresholds.values()):
        verdict = 'PASS'
    else:
        verdict = 'FAIL'

    return {
        'window_days': days,
        'generated_at': now.isoformat(),
        'since': since.isoformat(),
        'events_total': len(rows),
        'terminal_total': terminal,
        'skipped_total': skipped,
        'flags_off': flags_off,
        'budget_deferred': budget_deferred,
        'outcomes': dict(outcomes),
        'cap_hits': cap_hits,
        'latency_p50_ms': percentile(latencies, 50),
        'latency_p95_ms': metrics['latency_p95_ms'],
        'latency_samples': len(latencies),
        'input_tokens_total': input_tokens,
        'output_tokens_total': output_tokens,
        'directives_stripped_total': directives_stripped,
        'directives_flagged_total': directives_flagged,
        'entropy_flags_total': entropy_flags,
        **{name: metrics[name] for name in metrics if name != 'latency_p95_ms'},
        'thresholds': thresholds,
        'verdict': verdict,
    }


class Command(BaseCommand):
    """Print the compaction soak verdict; never mutate."""

    help = (
        'Summarize ChatCompactionEvent rows over --days (default 7) against the '
        'plan §8.3 thresholds. Counts and rates only; exit 0 PASS, 1 FAIL, '
        '2 no terminal events.'
    )

    def add_arguments(self, parser) -> None:
        """Register the reporting window and output options."""
        parser.add_argument(
            '--days', type=int, default=7, help='Lookback window in days'
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options) -> None:
        """Build the report, print it, exit per the verdict."""
        report = build_report(int(options['days']))
        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        else:
            self._print_text(report)
        verdict = report['verdict']
        if verdict == 'no_data':
            sys.exit(2)
        if verdict == 'FAIL':
            sys.exit(1)

    def _print_text(self, report: dict) -> None:
        """The ``key = value`` idiom shared with compaction_model_probe."""
        write = self.stdout.write
        write(f'window_days              = {report["window_days"]}')
        write(f'events_total             = {report["events_total"]}')
        write(f'terminal_total           = {report["terminal_total"]}')
        write(f'skipped_total            = {report["skipped_total"]}')
        write(f'flags_off                = {report["flags_off"]}')
        write(f'budget_deferred          = {report["budget_deferred"]}')
        for outcome, count in sorted(report['outcomes'].items()):
            write(f'outcome_{outcome:<17}= {count}')
        write(f'cap_hits                 = {report["cap_hits"]}')
        write(f'latency_p50_ms           = {_fmt(report["latency_p50_ms"])}')
        write(f'latency_p95_ms           = {_fmt(report["latency_p95_ms"])}')
        write(f'latency_samples          = {report["latency_samples"]}')
        write(f'input_tokens_total       = {report["input_tokens_total"]}')
        write(f'output_tokens_total      = {report["output_tokens_total"]}')
        write(f'directives_stripped_total= {report["directives_stripped_total"]}')
        write(f'directives_flagged_total = {report["directives_flagged_total"]}')
        write(f'entropy_flags_total      = {report["entropy_flags_total"]}')
        for name, item in report['thresholds'].items():
            symbol = '<' if item['comparator'] == 'lt' else '=='
            write(
                f'{name:<25}= {_fmt(item["value"])} '
                f'(threshold {symbol} {item["threshold"]}) '
                f'{"pass" if item["pass"] else "FAIL"}'
            )
        verdict = report['verdict']
        if verdict == 'PASS':
            write(self.style.SUCCESS('PASS'))
        elif verdict == 'FAIL':
            write(self.style.ERROR('FAIL'))
        else:
            write(self.style.WARNING('no_data'))


def _fmt(value) -> str:
    """Render a metric value; None reads as n/a."""
    return 'n/a' if value is None else str(value)
