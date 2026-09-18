"""Create a private bounded account projection without exporting credentials."""

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from aichat.services import account_export


class Command(BaseCommand):
    """Explicit operator boundary, exclusive file and incomplete-artifact removal."""

    help = 'Export account metadata, authorized memories and scoped owned chat to a new private JSONL file.'

    def add_arguments(self, parser):
        """Retain an explicit server scope; never infer access from mailbox/client labels."""
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--scope-key', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        """A completed scoped export remains distinct from a complete account DSAR."""
        target = Path(options['output'])
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise CommandError('Cannot create a new private export file') from None
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                size = 0
                complete = False
                for count, row in enumerate(
                    account_export.records(
                        options['user_id'], scope_key=options['scope_key']
                    ),
                    start=1,
                ):
                    encoded = (
                        json.dumps(row, cls=DjangoJSONEncoder, ensure_ascii=False)
                        + '\n'
                    ).encode('utf-8')
                    size += len(encoded)
                    if (
                        size > account_export.MAX_BYTES
                        or count > account_export.MAX_RECORDS
                        or complete
                    ):
                        raise ValueError('Export exceeds bounds or record order')
                    stream.write(encoded)
                    complete = row.get('type') == 'complete'
                if not complete:
                    raise ValueError('Incomplete export')
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                raise CommandError(
                    'Export failed; remove the incomplete destination file'
                ) from None
            raise CommandError('Export failed; incomplete artifact removed') from None
        self.stdout.write(
            'Scoped account export complete; see manifest for excluded stores.'
        )
