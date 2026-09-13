"""Rename a registered station's human label, keeping its pump slots in step.

The label a site actually uses is rarely the code the SCADA feed sends. This
changes only that label: ``uuid``, ``source_namespace``, ``source_entity_uuid``
and ``source_key`` are the station's identity and stay exactly as registered, so
re-import, dictionary points and signal bindings all continue to resolve.

The station is found by its **source** identity rather than by its current name,
because the current name is precisely the thing being replaced and would be a
poor handle for the operation that replaces it.
"""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from assets.models import AssetMachine
from assets.registry import rename_station


class Command(BaseCommand):
    """Operator-only local relabel; no ingestion and no identity change."""

    help = 'Rename a registered station and re-label its pump slots'

    def add_arguments(self, parser):
        """Identify the station by source identity, never by display name."""
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--pk', type=int, help='Station primary key')
        group.add_argument(
            '--source-entity-uuid', help='Station UUID in the source system'
        )
        parser.add_argument('--name', required=True, help='New display name')
        parser.add_argument('--dry-run', action='store_true')

    def find(self, options):
        """Resolve exactly one registered station, or refuse."""
        stations = AssetMachine.objects.filter(asset_type='pumphouse')

        if options['pk']:
            station = stations.filter(pk=options['pk']).first()
        else:
            try:
                source_uuid = UUID(options['source_entity_uuid'])
            except (TypeError, ValueError) as exc:
                raise CommandError(f'Not a UUID: {exc}') from exc
            station = stations.filter(source_entity_uuid=source_uuid).first()

        if station is None:
            raise CommandError('No registered station matches that identifier.')
        return station

    def handle(self, *args, **options):
        """Apply the relabel atomically so children cannot be left half-renamed."""
        try:
            with transaction.atomic():
                station = self.find(options)
                previous = station.name
                changed = rename_station(station, options['name'])

                self.stdout.write(f'{previous!r} -> {station.name!r}')
                self.stdout.write(
                    f'{changed} row(s) relabelled '
                    f'(station + {changed - 1} pump slot(s))'
                )
                self.stdout.write(
                    f'identity unchanged: uuid={station.uuid} '
                    f'source_entity_uuid={station.source_entity_uuid} '
                    f'source_key={station.source_key}'
                )

                if options['dry_run']:
                    transaction.set_rollback(True)

            self.stdout.write(
                'Rolled back preview.' if options['dry_run'] else 'Renamed.'
            )
        except (ValidationError, IntegrityError) as exc:
            raise CommandError(f'Rename failed: {exc}') from exc
