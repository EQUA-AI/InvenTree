"""Inspect native writer/copy call-site leads without querying operational rows."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integration_state.writer_audit import (
    build_inventory,
    check_baseline,
    encode_inventory,
)


class Command(BaseCommand):
    """Source-only candidate inventory; success never certifies actual writers."""

    help = 'Print bounded native writer/copy source call-site leads, never authority'
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        """A baseline requires explicit reconciliation on any source/scope drift."""
        parser.add_argument('--check-baseline', type=Path)

    def handle(self, **options):
        """Read only source metadata and emit no operational payloads."""
        try:
            current = build_inventory()
            if options['check_baseline'] is not None:
                check_baseline(
                    current, json.loads(options['check_baseline'].read_text())
                )
        except (OSError, ValueError, TypeError, KeyError):
            raise CommandError(
                'Native writer source audit is unavailable or differs'
            ) from None
        self.stdout.write(encode_inventory(current))
