"""Register reviewed station identity and optionally import an offline dictionary."""

import json
from pathlib import Path
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from assets.models import Client
from assets.registry import (
    ensure_pump,
    import_dictionary,
    plan_dictionary,
    register_station,
)


class Command(BaseCommand):
    """Operator-only local bootstrap; the HTTP registry applies actor scopes."""

    help = 'Register a station UUID crosswalk and optional offline dictionary; no live ingestion'

    def add_arguments(self, parser):
        """Require explicit Client, namespace and mapping rather than inferring them."""
        parser.add_argument('--client', required=True)
        parser.add_argument('--source-namespace', required=True)
        parser.add_argument('--mapping', type=Path, required=True)
        parser.add_argument('--json', type=Path)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        """Apply a validated crosswalk atomically, preserving existing identities."""
        try:
            mapping = json.loads(options['mapping'].read_text(encoding='utf-8'))
            info = mapping['station']
            client = Client.objects.get(code=options['client'], active=True)
            with transaction.atomic():
                station = register_station(
                    client=client,
                    name=info['display_name'],
                    public_uuid=UUID(info['uuid']),
                    source_namespace=options['source_namespace'],
                    source_key=info['external_code'],
                    source_entity_uuid=UUID(info['source_uuid']),
                    source_context=mapping['source_layout']['constant_fields'],
                )
                for record in mapping['pumps']:
                    pump = ensure_pump(station, record['source_key'])
                    if pump.uuid != UUID(record['uuid']):
                        raise CommandError(
                            'Pump UUID conflicts with its registered source key.'
                        )
                if not station.description:
                    station.description = 'Offline equipment registry pilot. Imported dictionary may be a sample excerpt; physical component assignments require review.'
                    station.save(update_fields=['description'])
                self.stdout.write(
                    f'Station: {station.pk}; UUID: {station.uuid}; pump slots: {station.children.count()}'
                )
                if options['json']:
                    raw = options['json'].read_bytes()
                    plan = plan_dictionary(station, raw)
                    result = import_dictionary(
                        station, raw, expected_hash=plan['source_hash']
                    )
                    self.stdout.write(f'Dictionary: {result}')
                if options['dry_run']:
                    transaction.set_rollback(True)
            self.stdout.write(
                'Rolled back preview.'
                if options['dry_run']
                else 'Registered. Live ingestion remains disabled.'
            )
        except (
            OSError,
            ValueError,
            KeyError,
            Client.DoesNotExist,
            ValidationError,
            IntegrityError,
        ) as exc:
            raise CommandError(f'Registration failed: {exc}') from exc
