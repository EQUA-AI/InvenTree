"""Prepare large attachment source sets for atomic final deletion."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.services.attachment_memory import cleanup, residual


class Command(BaseCommand):
    """Each explicit pass cleans at most 200 facts; attachment deletion is separate."""

    help = 'Preview memory attachment lineage; --execute withdraws linked suggestions and severs confirmed source pointers.'

    def add_arguments(self, parser):
        """No file paths or storage keys are accepted or printed."""
        parser.add_argument('--attachment-id', type=int, required=True)
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        """Expose only aggregate cleanup progress, never fact text."""
        identity = options['attachment_id']
        if identity <= 0:
            raise CommandError('A positive attachment id is required')
        try:
            result = (
                cleanup(identity)
                if options['execute']
                else {'status': 'dry_run', 'remaining_claims': residual(identity)}
            )
        except Exception:
            raise CommandError(
                'Attachment memory cleanup failed; source deletion must remain blocked'
            ) from None
        self.stdout.write(json.dumps(result, indent=2))
        if result['status'] == 'purge_incomplete':
            raise CommandError(
                'Memory source cleanup is incomplete; repeat the bounded pass'
            )
