"""Preview or execute client-labelled memory erasure without cross-client deletion."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.client_memory_erasure import purge_client


class Command(BaseCommand):
    """Native records and unpartitioned transcripts remain explicit blockers."""

    help = 'Preview client memory offboarding; --execute persists a stop bit and erases up to 200 facts.'

    def add_arguments(self, parser):
        """Operator CLI only; no request-supplied client scope."""
        parser.add_argument('--client-id', type=int, required=True)
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        """Output a content-free receipt and never imply full client erasure."""
        try:
            result = purge_client(options['client_id'], dry_run=not options['execute'])
        except Exception:
            raise CommandError(
                'Client memory erasure unavailable; check identity and stores'
            ) from None
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        if options['execute']:
            raise CommandError(
                'Full client offboarding remains incomplete; review the receipt exclusions'
            )
