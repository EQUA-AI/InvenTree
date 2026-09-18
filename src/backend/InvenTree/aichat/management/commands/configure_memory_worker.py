"""Preview or explicitly install the memory worker's heartbeat schedule."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.memory_worker import configure_heartbeat


class Command(BaseCommand):
    """Schedule installation is separate from code deployment and routing enablement."""

    help = 'Preview the ai-memory heartbeat schedule; --execute installs it.'

    def add_arguments(self, parser):
        """Default to a read-only configuration preview."""
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        """Do not expose store errors or alter producer flags."""
        try:
            result = configure_heartbeat(execute=options['execute'])
        except Exception:
            raise CommandError('Memory heartbeat configuration unavailable') from None
        self.stdout.write(json.dumps(result, sort_keys=True))
