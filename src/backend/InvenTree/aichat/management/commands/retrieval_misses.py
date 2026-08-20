"""Roll up the top unanswered manual questions from the A7 ledger (S16)."""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from aichat.models import RetrievalMiss


class Command(BaseCommand):
    """Report the most frequent zero-hit controlled-corpus queries."""

    help = 'Report the top unanswered manual questions from the retrieval ledger'

    def add_arguments(self, parser) -> None:
        """Register the reporting window and output options."""
        parser.add_argument(
            '--days', type=int, default=30, help='Lookback window in days'
        )
        parser.add_argument('--top', type=int, default=20, help='Rows to report')
        parser.add_argument(
            '--weak',
            type=float,
            default=None,
            metavar='SCORE',
            help=(
                'Also report weak-but-nonzero searches: hits whose top score '
                'is below SCORE (or unrecorded). These are the over-caution '
                'suspects a zero-hit report never shows (P8-W0b).'
            ),
        )
        parser.add_argument(
            '--corpus',
            choices=['governed', 'attachment'],
            default=None,
            help=(
                'Restrict the report to one retrieval surface: governed '
                '(controlled manuals) or attachment (uploaded documents, R2). '
                'Default reports across both.'
            ),
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options) -> None:
        """Aggregate zero-hit queries by frequency within the window."""
        since = timezone.now() - timedelta(days=max(1, options['days']))
        base = RetrievalMiss.objects.filter(created_at__gte=since)
        if options['corpus']:
            base = base.filter(corpus=options['corpus'])
        misses = (
            base
            .filter(hit_count=0)
            .values('query')
            .annotate(asked=Count('id'), last_asked=Max('created_at'))
            .order_by('-asked', '-last_asked')[: max(1, options['top'])]
        )
        total_searches = base.count()
        total_misses = base.filter(hit_count=0).count()
        rows = [
            {
                'query': row['query'],
                'asked': row['asked'],
                'last_asked': row['last_asked'].isoformat(),
            }
            for row in misses
        ]

        weak_rows: list[dict] = []
        weak_total = 0
        weak_threshold = options.get('weak')
        if weak_threshold is not None:
            weak_filter = Q(top_score__isnull=True) | Q(top_score__lt=weak_threshold)
            weak_qs = base.filter(weak_filter, hit_count__gt=0)
            weak_total = weak_qs.count()
            weak = (
                weak_qs
                .values('query')
                .annotate(
                    asked=Count('id'),
                    last_asked=Max('created_at'),
                    best_score=Max('top_score'),
                    worst_score=Min('top_score'),
                )
                .order_by('-asked', '-last_asked')[: max(1, options['top'])]
            )
            weak_rows = [
                {
                    'query': row['query'],
                    'asked': row['asked'],
                    'last_asked': row['last_asked'].isoformat(),
                    'best_score': row['best_score'],
                    'worst_score': row['worst_score'],
                }
                for row in weak
            ]

        if options['json']:
            report = {
                'window_days': options['days'],
                'total_searches': total_searches,
                'total_misses': total_misses,
                'top_unanswered': rows,
            }
            if options['corpus']:
                report['corpus'] = options['corpus']
            if weak_threshold is not None:
                report['weak_threshold'] = weak_threshold
                report['total_weak'] = weak_total
                report['top_weak'] = weak_rows
            self.stdout.write(json.dumps(report))
            return
        self.stdout.write(
            f'{total_misses} zero-hit searches of {total_searches} total '
            f'in the last {options["days"]} days'
        )
        for row in rows:
            self.stdout.write(
                f'{row["asked"]:>4}x  {row["query"]}  (last {row["last_asked"]})'
            )
        if weak_threshold is not None:
            self.stdout.write(
                f'{weak_total} weak searches (hits with top score < '
                f'{weak_threshold} or unrecorded):'
            )
            for row in weak_rows:
                self.stdout.write(
                    f'{row["asked"]:>4}x  {row["query"]}  '
                    f'(scores {row["worst_score"]}..{row["best_score"]}, '
                    f'last {row["last_asked"]})'
                )
