"""The §16 shadow-review report (S15) — run-scoped, six inspection items.

For at least one representative evaluation run, the owners inspect:
(1) old route vs new task intent, (2) out-of-scope items the filter would
reject, (3) validator would-downgrade reasons, (4) manual applicability
unresolved cases, (5) aggregate coverage completeness, (6) latency and
token changes vs a baseline window. Content-free by construction; the
frozen battery baseline (median 9.3 s / p95 68.2 s) prints as the fixed
"before" reference when no baseline window is given.
"""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aichat.reports import pilot_metrics

#: The frozen pre-program battery baseline (§8.9 / decision record Q47).
FROZEN_BASELINE = {
    'median_s': 9.3,
    'p95_s': 68.2,
    'source': 'frozen 2026-08-23 battery',
}


def _parse(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'{value!r} is not an ISO datetime') from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


class Command(BaseCommand):
    """Aggregate the six §16 shadow-review items for one run window."""

    help = 'Content-free §16 shadow-review report for one evaluation window.'

    def add_arguments(self, parser):
        """Register the run window, optional baseline window, and output."""
        parser.add_argument('--since', help='ISO start of the run window')
        parser.add_argument('--until', help='ISO end of the run window')
        parser.add_argument(
            '--days', type=int, default=1, help='Fallback window (days)'
        )
        parser.add_argument('--baseline-since', help='ISO start of a comparison window')
        parser.add_argument('--baseline-until', help='ISO end of a comparison window')
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        """Build and print the six-item report."""
        until = _parse(options['until']) if options['until'] else timezone.now()
        since = (
            _parse(options['since'])
            if options['since']
            else until - timedelta(days=max(1, options['days']))
        )

        # The aggregators take an open-ended window; scope by since and
        # note until (rows after it are absent only for closed windows).
        report = {
            'window': {'since': since.isoformat(), 'until': until.isoformat()},
            'generated_at': timezone.now().isoformat(),
            'latch': pilot_metrics.latch_state(),
            # (1) old route vs new task intent
            'route_vs_intent': pilot_metrics.route_stats(since),
            # (2) out-of-scope items the filter would reject
            'out_of_scope': pilot_metrics.scope_stats(since),
            # (3) validator would-downgrade reasons
            'validator_reasons': pilot_metrics.validator_stats(since),
            # (4) manual applicability unresolved (pre-S8b proxy)
            'applicability_unresolved': pilot_metrics.applicability_stats(),
            # (5) aggregate coverage completeness
            'coverage': pilot_metrics.coverage_stats(since),
            # (6) latency and token changes
            'latency_tokens': {
                'window': {
                    'turns': pilot_metrics.turn_and_latency_stats(since),
                    'usage': pilot_metrics.usage_stats(since),
                },
                'frozen_baseline': FROZEN_BASELINE,
            },
        }
        if options['baseline_since']:
            baseline_since = _parse(options['baseline_since'])
            report['latency_tokens']['baseline_window'] = {
                'since': baseline_since.isoformat(),
                'turns': pilot_metrics.turn_and_latency_stats(baseline_since),
                'usage': pilot_metrics.usage_stats(baseline_since),
            }

        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        for key in (
            'route_vs_intent',
            'out_of_scope',
            'validator_reasons',
            'applicability_unresolved',
            'coverage',
        ):
            self.stdout.write(f'{key}: {report[key]}')
        self.stdout.write(f'latency_tokens: {report["latency_tokens"]}')
