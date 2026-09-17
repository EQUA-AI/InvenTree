"""Export a signed thread deletion journal outside the database being restored."""

import hashlib
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aichat.services.retention_journal import JournalError, export_journal, read_journal


class Command(BaseCommand):
    """Write a new private file; the operator must first quiesce source writers."""

    help = (
        'Export retained thread deletions and outstanding cleanup to a new signed file.'
    )

    def add_arguments(self, parser):
        """Require the intended restore timestamp and a new output path."""
        parser.add_argument('--since', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        """Never print the signed artifact, raw identifiers or store errors."""
        try:
            token = export_journal(since=options['since'])
            payload = read_journal(token, since=options['since'])
        except JournalError as exc:
            raise CommandError(str(exc)) from None
        except Exception:
            raise CommandError(
                'Journal export failed while reading the source'
            ) from None
        target = Path(options['output'])
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise CommandError('Cannot create a new journal file') from None
        try:
            with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
                stream.write(token + '\n')
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                raise CommandError(
                    'Journal write failed; remove the incomplete destination'
                ) from None
            raise CommandError(
                'Journal write failed; incomplete file removed'
            ) from None
        self.stdout.write(
            json.dumps(
                {
                    'status': 'exported',
                    'threads': len(payload['threads']),
                    'outbox': len(payload['outbox']),
                    'journal_sha256': hashlib.sha256(token.encode()).hexdigest(),
                    'since': payload['since'],
                    'as_of': payload['as_of'],
                },
                sort_keys=True,
            )
        )
