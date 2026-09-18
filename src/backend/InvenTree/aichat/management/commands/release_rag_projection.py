"""Explicit operator recovery after projection repair and review."""

import json

from django.core.management.base import BaseCommand, CommandError

from aichat.models import RagProjectionGate
from aichat.services.projection_audit import release_gate


class Command(BaseCommand):
    """Preview the current stop; release requires its exact revision timestamp."""

    help = 'Inspect a projection stop. --execute releases only the named stop after repair and fresh clean evidence.'

    def add_arguments(self, parser):
        """A stop receipt prevents releasing a newer concurrent incident."""
        parser.add_argument('--corpus', choices=['attachment', 'media'], required=True)
        parser.add_argument('--expected-blocked-at')
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        """Never change a gate merely because an audit sample passed."""
        if not options['execute']:
            gate = RagProjectionGate.objects.filter(
                corpus=options['corpus'], blocked=True
            ).first()
            result = {
                'corpus': options['corpus'],
                'blocked': bool(gate),
                'blocked_at': gate.updated_at.isoformat() if gate else None,
            }
        else:
            try:
                result = release_gate(
                    corpus=options['corpus'],
                    expected_blocked_at=options['expected_blocked_at'],
                )
            except ValueError:
                raise CommandError(
                    'Projection release refused; review stop receipt, repairs and latest evidence'
                ) from None
        self.stdout.write(json.dumps(result, sort_keys=True))
