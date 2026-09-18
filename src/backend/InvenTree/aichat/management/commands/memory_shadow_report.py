"""Read-only count-based custom memory report; no provider or automatic pause action."""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from aichat.services.memory_shadow_report import report_window


def _time(value):
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError('Timezone-aware ISO timestamp required')
    return parsed


class Command(BaseCommand):
    """Unknown/absent evidence never satisfies a promotion threshold."""

    help = 'Report retained memory runs and estimated/known worker spend with explicit unmeasured gates.'

    def add_arguments(self, parser):
        """Bound both main and optional comparison windows."""
        parser.add_argument('--since')
        parser.add_argument('--until')
        parser.add_argument('--days', type=int, default=1)
        parser.add_argument('--baseline-since')
        parser.add_argument('--baseline-until')
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        """No transcript, owner identifiers, query text or provider errors are output."""
        try:
            if not 1 <= options['days'] <= 90:
                raise ValueError('Invalid days')
            until = _time(options['until']) if options['until'] else timezone.now()
            since = (
                _time(options['since'])
                if options['since']
                else until - timedelta(days=options['days'])
            )
            report = report_window(since=since, until=until)
            if bool(options['baseline_since']) != bool(options['baseline_until']):
                raise ValueError('Both baseline bounds required')
            if options['baseline_since']:
                report['baseline'] = report_window(
                    since=_time(options['baseline_since']),
                    until=_time(options['baseline_until']),
                )
            self.stdout.write(json.dumps(report, sort_keys=True, indent=2))
        except (TypeError, ValueError):
            raise CommandError(
                'Invalid report window; provide aware ordered bounds within 90 days'
            ) from None
