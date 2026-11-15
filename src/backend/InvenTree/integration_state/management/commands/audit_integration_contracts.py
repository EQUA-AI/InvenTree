"""Inspect field/edge metadata without reading operational source rows."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integration_state.contract_audit import (
    build_inventory,
    check_baseline,
    encode_inventory,
)


class Command(BaseCommand):
    """Record explicit policy gaps; a successful audit never grants disclosure."""

    help = 'Print native integration field metadata and explicit unreviewed gaps'
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        """The optional file is a drift baseline, never an authority registry."""
        parser.add_argument('--check-baseline', type=Path)

    def handle(self, **options):
        """Read model/DTO declarations only and fail on baseline drift."""
        value = build_inventory()
        if options['check_baseline'] is not None:
            try:
                check_baseline(value, json.loads(options['check_baseline'].read_text()))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CommandError(
                    'Native contract baseline is unavailable or differs'
                ) from exc
        self.stdout.write(encode_inventory(value))
