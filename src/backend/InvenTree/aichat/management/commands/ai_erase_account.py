"""Operator-only account disabling/anonymization and current chat/voice cleanup."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.account_erasure import AccountErasureError, erase_account


class Command(BaseCommand):
    """Preview by default; execution preserves the user id and disables login."""

    help = 'Preview local account erasure. --execute disables login, scrubs local identity and purges current chat/voice stores.'

    def add_arguments(self, parser):
        """Require an explicit target and an explicit execution switch."""
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--batch-size', type=int, default=500)

    def handle(self, *args, **options):
        """Print a count-only receipt; failed cleanup never re-enables login."""
        try:
            report = erase_account(
                options['user_id'],
                dry_run=not options['execute'],
                batch_size=options['batch_size'],
            )
        except AccountErasureError as exc:
            raise CommandError(str(exc)) from None
        except Exception:
            raise CommandError(
                'Account cleanup could not finish; access may already be disabled. Retry after resolving the store failure.'
            ) from None
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if report['status'] == 'purge_incomplete':
            raise CommandError(
                'Local account cleanup remains incomplete; review residual counts'
            )
