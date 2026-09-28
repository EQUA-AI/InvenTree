"""Stop protocol for the EQUA demo metrics session (work package F).

Revokes future replay authority through the persisted session state under the
session lock, so no replay batch can commit after the stop boundary.
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
    """Stop one session's replay feed."""

    help = 'Revoke future replay authority for one EQUA demo metrics session.'

    def add_arguments(self, parser):
        """Declare validated arguments."""
        parser.add_argument('--session', required=True, help='Session key')
        parser.add_argument(
            '--actor', required=True, help='Username of the trusted operator'
        )

    def handle(self, *args, **options):
        """Persist the stop under the session lock."""
        actor = resolve_actor(options['actor'])
        authorize_operator(actor)
        session = resolve_session(options['session'])
        try:
            result = replay.stop_session(session, actor)
        except replay.ReplayError as exc:
            raise CliError(exc.code, str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'stop={result}'))
