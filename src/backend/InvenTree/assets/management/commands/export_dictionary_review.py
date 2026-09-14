"""Write a bounded dictionary review pack for offline engineering review."""

import json

from django.core.management.base import BaseCommand, CommandError

from assets.dictionary_review import export_review
from assets.models import AssetMachine


class Command(BaseCommand):
    """Print JSON to stdout so the operator chooses its version-controlled destination."""

    def add_arguments(self, parser):
        """Require the local registered station identity."""
        parser.add_argument('--station', type=int, required=True)

    def handle(self, *args, **options):
        """Do not approve, rename, or write any database row."""
        try:
            station = AssetMachine.objects.get(
                pk=options['station'], asset_type='pumphouse'
            )
        except AssetMachine.DoesNotExist as exc:
            raise CommandError('No matching station.') from exc
        self.stdout.write(json.dumps(export_review(station), indent=2))
