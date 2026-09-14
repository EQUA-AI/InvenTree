"""Onboard a station estate without copying connection credentials into manifests."""

import json
from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.management.base import BaseCommand, CommandError

from assets.health_models import HealthSource
from assets.pumphouse_estate import onboard_estate, read_manifest


class Command(BaseCommand):
    """Use explicit identities and reviewed mappings; never enable the global flag."""

    def add_arguments(self, parser):
        """Take one source, a manifest, and optional preview/activation switches."""
        parser.add_argument('manifest', type=Path)
        parser.add_argument('--source', type=int, required=True)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--activate', action='store_true')

    def handle(self, *args, **options):
        """Emit crosswalk JSON for the engineer to retain alongside the manifest."""
        try:
            source = HealthSource.objects.get(pk=options['source'])
            report = onboard_estate(
                read_manifest(options['manifest']),
                source,
                directory=options['manifest'].parent,
                activate=options['activate'],
                dry_run=options['dry_run'],
            )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            ObjectDoesNotExist,
            ValidationError,
        ) as exc:
            raise CommandError(f'Onboarding refused: {exc}') from exc
        self.stdout.write(json.dumps(report, indent=2, default=str))
