"""Read the memory queue's aggregate pressure and scoped heartbeat."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.memory_worker import worker_status


class Command(BaseCommand):
    """No broker payloads, thread identifiers or exception contents are printed."""

    help = 'Inspect memory-worker liveness and queue pressure without changing state.'

    def add_arguments(self, parser):
        """Allow operators to require a fresh heartbeat and admission readiness."""
        parser.add_argument('--fail-on-unready', action='store_true')

    def handle(self, *args, **options):
        """Emit aggregate-only JSON, refusing unqualified readiness."""
        try:
            result = worker_status()
        except Exception:
            raise CommandError('Memory worker status unavailable') from None
        self.stdout.write(json.dumps(result, sort_keys=True))
        if options['fail_on_unready'] and (
            not result['heartbeat_fresh']
            or result['backpressured']
            or result['serving_hold']
        ):
            raise CommandError('Memory worker is not ready')
