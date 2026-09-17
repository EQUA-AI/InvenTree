"""Preview or execute current chat/voice erasure for an operator-selected user."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.retention import purge_user


class Command(BaseCommand):
    """Default to a count-only preview; account deactivation is separate."""

    help = 'Preview current chat/voice erasure. --execute deletes content; preserves user and operational records.'

    def add_arguments(self, parser):
        """Expose an explicit execution switch and bounded database page size."""
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--batch-size', type=int, default=500)

    def handle(self, *args, **options):
        """Emit only aggregate counts, statuses and a hashed subject reference."""
        try:
            report = purge_user(
                options['user_id'],
                dry_run=not options['execute'],
                batch_size=options['batch_size'],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        except Exception:
            raise CommandError(
                'Erasure could not complete; retry after resolving the store failure'
            ) from None
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if report['status'] == 'purge_incomplete':
            raise CommandError(
                'Current-store erasure is incomplete; review residual counts and retry'
            )
