"""S37: per-user/per-day token usage report from persisted turn metadata.

Read-only ops command (the grounding_soak_report idiom). Aggregates
``ChatMessage.metadata['usage']`` — written at turn terminal when
FEATURE_TURN_USAGE_PERSISTENCE is on — into per-user/per-day canonical
token totals and a cached-input hit rate by source. Python-side JSON
extraction is acceptable here; this never runs on the request path.
"""

import json
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aichat.models import ChatMessage

CANONICAL_KEYS = (
    'input_tokens',
    'output_tokens',
    'cached_input_tokens',
    'total_tokens',
)


class Command(BaseCommand):
    """Summarize persisted per-turn token usage (read-only)."""

    help = 'Summarize persisted per-turn token usage (read-only)'

    def add_arguments(self, parser):
        """CLI options."""
        parser.add_argument(
            '--days', type=int, default=14, help='Lookback window in days'
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options):
        """Aggregate usage metadata into per-user/day and per-source tables."""
        since = timezone.now() - timedelta(days=max(1, options['days']))
        rows = (
            ChatMessage.objects
            .filter(metadata__has_key='usage', created_at__gte=since)
            .order_by('created_at')
            .values('thread__owner__username', 'created_at', 'metadata')
        )

        per_user_day = defaultdict(lambda: dict.fromkeys(CANONICAL_KEYS, 0))
        per_source = defaultdict(lambda: dict.fromkeys(CANONICAL_KEYS, 0))
        turns = 0
        for row in rows:
            usage = (row['metadata'] or {}).get('usage') or {}
            totals = usage.get('totals') or {}
            if not isinstance(totals, dict):
                continue
            turns += 1
            user = row['thread__owner__username'] or '-'
            day = row['created_at'].strftime('%Y-%m-%d')
            bucket = per_user_day[user, day]
            for key in CANONICAL_KEYS:
                value = totals.get(key)
                if isinstance(value, int):
                    bucket[key] += value
            for event in usage.get('events') or []:
                if not isinstance(event, dict):
                    continue
                source = str(event.get('source') or '-')
                source_bucket = per_source[source]
                for key in CANONICAL_KEYS:
                    value = event.get(key)
                    if isinstance(value, int):
                        source_bucket[key] += value

        def hit_rate(bucket):
            base = bucket['input_tokens']
            return round(bucket['cached_input_tokens'] / base, 4) if base else None

        report = {
            'since': since.isoformat(),
            'turns_with_usage': turns,
            'per_user_day': [
                {
                    'user': user,
                    'day': day,
                    **bucket,
                    'cached_hit_rate': hit_rate(bucket),
                }
                for (user, day), bucket in sorted(per_user_day.items())
            ],
            'per_source': [
                {'source': source, **bucket, 'cached_hit_rate': hit_rate(bucket)}
                for source, bucket in sorted(per_source.items())
            ],
        }

        if options['json']:
            self.stdout.write(json.dumps(report, indent=2))
            return

        self.stdout.write(f'Usage since {report["since"]} — {turns} turns with usage')
        self.stdout.write('')
        self.stdout.write('Per user/day (canonical totals):')
        for entry in report['per_user_day']:
            self.stdout.write(
                f'  {entry["day"]} {entry["user"]}: in={entry["input_tokens"]} '
                f'out={entry["output_tokens"]} cached={entry["cached_input_tokens"]} '
                f'total={entry["total_tokens"]} hit_rate={entry["cached_hit_rate"]}'
            )
        self.stdout.write('')
        self.stdout.write('Per source:')
        for entry in report['per_source']:
            self.stdout.write(
                f'  {entry["source"]}: in={entry["input_tokens"]} '
                f'out={entry["output_tokens"]} cached={entry["cached_input_tokens"]} '
                f'total={entry["total_tokens"]} hit_rate={entry["cached_hit_rate"]}'
            )
