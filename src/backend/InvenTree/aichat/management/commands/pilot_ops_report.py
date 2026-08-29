"""The §8.10 operations report (S15) — daily/weekly review, content-free.

One read-only aggregation over persisted turn state: latch state,
turn/error/latency stats, route distribution + shadow/enforce divergence,
scope rejections, coverage completeness, validator outcomes, grounding
mismatches, safety-length violations, token usage, and typed pre-turn
rejections. ``--days 1`` is the daily engineering review; ``--days 7``
the weekly five-role review (§8.10/§16).
"""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aichat.reports import pilot_metrics


class Command(BaseCommand):
    """Aggregate the §8.10 metrics for one review window."""

    help = 'Content-free §8.10 operations report (read-only).'

    def add_arguments(self, parser):
        """Register the window and output options."""
        parser.add_argument(
            '--days', type=int, default=1, help='Lookback window in days'
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options):
        """Build and print the report."""
        window_days = max(1, options['days'])
        since = timezone.now() - timedelta(days=window_days)

        report = {
            'window_days': window_days,
            'generated_at': timezone.now().isoformat(),
            'latch': pilot_metrics.latch_state(),
            'turns': pilot_metrics.turn_and_latency_stats(since),
            'rejections': pilot_metrics.rejection_stats(since),
            'routes': pilot_metrics.route_stats(since),
            'scope': pilot_metrics.scope_stats(since),
            'coverage': pilot_metrics.coverage_stats(since),
            'validator': pilot_metrics.validator_stats(since),
            'grounding': pilot_metrics.grounding_stats(since),
            'safety_length': pilot_metrics.safety_length_stats(since),
            'usage': pilot_metrics.usage_stats(since),
            'retention': pilot_metrics.retention_stats(),
            'known_gaps': [
                'per-stage latency is not persisted (totals only)',
                'shadow-mode budget would_block exists only in logs',
            ],
        }

        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        latch = report['latch']
        self.stdout.write(
            f'Pilot latch: {"ENGAGED (" + latch["reason_code"] + ")" if latch.get("latched") else "clear"}'
        )
        turns = report['turns']
        self.stdout.write(
            f'Turns: {turns["total"]} states={turns["states"]} '
            f'incomplete_or_failed_rate={turns["incomplete_or_failed_rate"]}'
        )
        self.stdout.write(f'Rejections: {report["rejections"]}')
        self.stdout.write(
            f'Route divergence: {report["routes"]["divergence"]["count"]} '
            f'(rate {report["routes"]["divergence"]["rate"]})'
        )
        self.stdout.write(f'Scope: {report["scope"]}')
        self.stdout.write(f'Coverage: {report["coverage"]}')
        self.stdout.write(f'Validator: {report["validator"]}')
        self.stdout.write(f'Grounding: {report["grounding"]}')
        self.stdout.write(f'Safety length: {report["safety_length"]}')
        self.stdout.write(f'Usage: {report["usage"]["totals"]}')
        retention = report['retention']
        self.stdout.write(
            f'Retention: last_run_age_days={retention["last_run_age_days"]} '
            f'backlog={retention["backlog"]} outbox={retention["outbox"]}'
        )
        for modality, stats in turns['latency_s'].items():
            self.stdout.write(f'Latency [{modality}]: {stats}')
