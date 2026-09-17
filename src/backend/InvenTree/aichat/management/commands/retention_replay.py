"""Validate or replay a signed deletion journal on an isolated restore clone."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aichat.services.retention_journal import MAX_BYTES, JournalError, replay_journal


class Command(BaseCommand):
    """Preview by default; execution requires the deployment serving hold."""

    help = 'Replay a signed external thread journal under restore hold. Preview unless --execute is set.'

    def add_arguments(self, parser):
        """Require an external journal; restored tombstones alone are insufficient."""
        parser.add_argument('--since', required=True)
        parser.add_argument('--journal', required=True)
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        """Emit counts/digest only; do not clear the serving hold."""
        try:
            with Path(options['journal']).open('rb') as stream:
                content = stream.read(MAX_BYTES + 2)
            if len(content) > MAX_BYTES + 1:
                raise JournalError('Journal exceeds the byte limit')
            token = content.decode('ascii').strip()
        except (OSError, UnicodeError):
            raise CommandError('Cannot read journal file') from None
        except JournalError as exc:
            raise CommandError(str(exc)) from None
        try:
            report = replay_journal(
                token, since=options['since'], execute=options['execute']
            )
        except JournalError as exc:
            raise CommandError(str(exc)) from None
        except Exception:
            raise CommandError(
                'Replay could not finish; retain the serving hold and retry'
            ) from None
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if report['status'] == 'replay_incomplete':
            raise CommandError('Replay is incomplete; retain the serving hold')
