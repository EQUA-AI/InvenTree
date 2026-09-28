"""Bounded replay feed for the EQUA demo metrics session (work package F).

One manual execution, no perpetual schedule: bounded interval and duration,
one replica, no automatic retries. Each batch re-checks session authority and
configuration, emits genuine new timestamps and commits its own receipt.
"""

from django.core.management.base import BaseCommand

from assets.demo_metrics import replay
from assets.demo_metrics.cli import (
    CliError,
    authorize_operator,
    resolve_actor,
    resolve_session,
)


class Command(BaseCommand):
    """Run the bounded live feed once."""

    help = 'Run a bounded replay feed for one active EQUA demo metrics session.'

    def add_arguments(self, parser):
        """Declare validated arguments."""
        parser.add_argument('--session', required=True, help='Session key')
        parser.add_argument(
            '--actor', required=True, help='Username of the trusted operator'
        )
        parser.add_argument(
            '--interval-seconds', type=float, default=30, help='Seconds between batches'
        )
        parser.add_argument(
            '--max-duration-seconds',
            type=float,
            default=1800,
            help='Hard bound on the feed duration',
        )

    def handle(self, *args, **options):
        """Start the feed (idempotent claim) and run the bounded loop."""
        actor = resolve_actor(options['actor'])
        authorize_operator(actor)
        session = resolve_session(options['session'])
        interval = options['interval_seconds']
        duration = options['max_duration_seconds']
        if interval < 0 or duration <= 0 or duration > 3600:
            raise CliError(
                'BAD_ARGUMENT', 'Interval/duration outside the bounded range'
            )
        try:
            result = replay.run_replay(
                session, actor, interval_seconds=interval, max_duration_seconds=duration
            )
        except replay.ReplayError as exc:
            raise CliError(exc.code, str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'replay batches={result["batches"]}'))
