"""Write one owner's scoped chat transcript export to a private JSON file."""

import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from aichat.services import ThreadRepository


class Command(BaseCommand):
    """Operator-only scoped export; this is not a complete account DSAR."""

    help = 'Export owned chat transcripts in one server scope to a new mode-0600 JSON file.'

    def add_arguments(self, parser):
        """Require an explicit owner, server scope and destination."""
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--scope-key', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        """Never send transcript contents to command output or overwrite a file."""
        if not get_user_model().objects.filter(pk=options['user_id']).exists():
            raise CommandError('Unknown export owner')
        repository = ThreadRepository(options['user_id'], options['scope_key'])
        target = Path(options['output'])
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise CommandError('Cannot create a new export file') from None
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write('{"schema_version":1,"records":[')
                separator = ''
                for record in repository.export_transcript_records():
                    stream.write(separator)
                    json.dump(record, stream, ensure_ascii=False)
                    separator = ',\n'
                stream.write(']}\n')
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # A failed or interrupted export must not look like a finished file.
            try:
                target.unlink(missing_ok=True)
            except OSError:
                raise CommandError(
                    'Export failed; remove the incomplete destination file'
                ) from None
            raise CommandError(
                'Export failed; the incomplete file was removed'
            ) from None
        self.stdout.write('Scoped transcript export complete.')
