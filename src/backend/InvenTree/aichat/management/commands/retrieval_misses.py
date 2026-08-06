"""Roll up the top unanswered manual questions from the A7 ledger (S16)."""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Max
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
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options) -> None:
        """Aggregate zero-hit queries by frequency within the window."""
        since = timezone.now() - timedelta(days=max(1, options['days']))
        misses = (
            RetrievalMiss.objects
            .filter(hit_count=0, created_at__gte=since)
            .values('query')
            .annotate(asked=Count('id'), last_asked=Max('created_at'))
            .order_by('-asked', '-last_asked')[: max(1, options['top'])]
        )
        total_searches = RetrievalMiss.objects.filter(created_at__gte=since).count()
        total_misses = RetrievalMiss.objects.filter(
            hit_count=0, created_at__gte=since
        ).count()
        rows = [
            {
                'query': row['query'],
                'asked': row['asked'],
                'last_asked': row['last_asked'].isoformat(),
            }
            for row in misses
        ]
        if options['json']:
            self.stdout.write(
                json.dumps({
                    'window_days': options['days'],
                    'total_searches': total_searches,
                    'total_misses': total_misses,
                    'top_unanswered': rows,
                })
            )
            return
        self.stdout.write(
            f'{total_misses} zero-hit searches of {total_searches} total '
            f'in the last {options["days"]} days'
        )
        for row in rows:
            self.stdout.write(
                f'{row["asked"]:>4}x  {row["query"]}  (last {row["last_asked"]})'
            )
