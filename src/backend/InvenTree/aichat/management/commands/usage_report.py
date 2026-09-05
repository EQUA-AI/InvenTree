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
    # M1 PR F: cache writes, when the provider reports them.
    'cache_write_tokens',
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
        # M1 PR F (§9.8): the builder's context_builder event, folded into
        # a content-free sections block, plus the estimator's error against
        # the provider count on single-call turns (provider_calls == 1).
        sections = {
            'turns': 0,
            'recent_turns': 0,
            'summary_present': 0,
            'dropped_turns': 0,
            'section_tokens': 0,
            'wall_ms_total': 0,
            'degraded': 0,
        }
        estimator_samples: list[float] = []
        for row in rows:
            usage = (row['metadata'] or {}).get('usage') or {}
            totals = usage.get('totals') or {}
            if not isinstance(totals, dict):
                continue
            turns += 1
            events = [e for e in (usage.get('events') or []) if isinstance(e, dict)]
            builder = next(
                (e for e in events if str(e.get('source') or '') == 'context_builder'),
                None,
            )
            provider_calls = [
                e
                for e in events
                if str(e.get('source') or '')
                not in ('context_builder', 'history_replay', 'quota_reservation')
                and isinstance(e.get('input_tokens'), int)
            ]
            if builder is not None:
                sections['turns'] += 1
                for key in (
                    'recent_turns',
                    'summary_present',
                    'dropped_turns',
                    'section_tokens',
                ):
                    value = builder.get(key)
                    if isinstance(value, int):
                        sections[key] += value
                if isinstance(builder.get('wall_ms'), int):
                    sections['wall_ms_total'] += builder['wall_ms']
                if isinstance(builder.get('degraded'), int):
                    sections['degraded'] += builder['degraded']
                estimate = builder.get('history_token_estimate')
                if len(provider_calls) == 1 and isinstance(estimate, int):
                    actual = provider_calls[0]['input_tokens']
                    if actual > 0:
                        estimator_samples.append(abs(estimate - actual) / actual)
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
                if source in ('context_builder', 'history_replay'):
                    # Telemetry events, not provider calls: folded into the
                    # sections block above, never into the token tables.
                    continue
                source_bucket = per_source[source]
                for key in CANONICAL_KEYS:
                    value = event.get(key)
                    if isinstance(value, int):
                        source_bucket[key] += value

        def hit_rate(bucket):
            base = bucket['input_tokens']
            return round(bucket['cached_input_tokens'] / base, 4) if base else None

        def write_ratio(bucket):
            base = bucket['input_tokens']
            write = bucket.get('cache_write_tokens')
            return (
                round(write / base, 4)
                if base and isinstance(write, int) and write
                else None
            )

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
                {
                    'source': source,
                    **bucket,
                    'cached_hit_rate': hit_rate(bucket),
                    'cache_write_ratio': write_ratio(bucket),
                }
                for source, bucket in sorted(per_source.items())
            ],
            'sections': {
                **sections,
                'wall_ms_mean': (
                    round(sections['wall_ms_total'] / sections['turns'], 1)
                    if sections['turns']
                    else None
                ),
            },
            'estimator_error': {
                'samples': len(estimator_samples),
                'mean_abs_relative': (
                    round(sum(estimator_samples) / len(estimator_samples), 4)
                    if estimator_samples
                    else None
                ),
            },
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
