"""Assign owned demo machines to an explicit physical-location manifest."""

import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assets.locations import client_ids, save_location, transfer_machines
from assets.management.commands.load_asset_demo_data import Command as DemoCommand
from assets.models import AssetLocation, AssetMachine, Client, LocationParentHistory

SEED_REASON = 'Initial physical hierarchy from asset_demo_data v1'


class Command(BaseCommand):
    """Idempotently initialize demo placement without resetting later user moves."""

    help = 'Load explicit physical locations for existing owned demo machines'

    def add_arguments(self, parser):
        """Require an accountable operator and support transaction rollback."""
        parser.add_argument(
            '--actor', required=True, help='Authorized operator username'
        )
        parser.add_argument('--dry-run', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        """Create declared nodes and first placement at current server time only."""
        actor = (
            get_user_model()
            .objects.filter(username=options['actor'], is_active=True)
            .first()
        )
        if actor is None:
            raise CommandError('An active operator is required')
        data = DemoCommand()._read_data()
        allowed = client_ids(actor)
        clients = {c.code: c for c in Client.objects.filter(pk__in=allowed)}
        nodes = {}
        created = assigned = preserved = 0
        for record in data['locations']:
            client = clients.get(record['client'])
            if client is None:
                raise CommandError('Operator must have scope for every declared client')
            parent = nodes.get(record['parent']) if record['parent'] else None
            if record['parent'] and parent is None:
                raise CommandError('Demo parents must precede their children')
            values = {key: record[key] for key in ('name', 'code', 'kind', 'timezone')}
            values['parent'] = parent.pk if parent else None
            node = AssetLocation.objects.filter(
                client=client, code=record['code']
            ).first()
            if node:
                owned = LocationParentHistory.objects.filter(
                    location=node, reason=SEED_REASON
                ).exists()
                if (
                    not owned
                    or node.archived
                    or any(
                        getattr(node, 'parent_id' if key == 'parent' else key) != value
                        for key, value in values.items()
                    )
                ):
                    raise CommandError(
                        f'Demo location {record["code"]!r} conflicts with an existing location'
                    )
            else:
                node = save_location(
                    actor, {**values, 'client': client.pk, 'reason': SEED_REASON}
                )
                created += 1
            nodes[record['code']] = node

        for record in data['machines']:
            machine = AssetMachine.objects.filter(name=record['name']).first()
            if machine is None:
                raise CommandError('Load the base asset demo dataset first')
            if (
                machine.client_id != clients[record['client']].pk
                or DemoCommand._machine_identity(record)
                != DemoCommand._machine_identity({
                    key: getattr(machine, key)
                    for key in ('manufacturer', 'model', 'serial')
                })
                or not DemoCommand._machine_has_managed_part(machine, record)
            ):
                raise CommandError(
                    f'Machine {record["name"]!r} is not owned by this demo dataset'
                )
            if machine.placement_version or machine.physical_location_id:
                preserved += 1
                continue
            destination = nodes[record['physical_location_code']]
            transfer_machines(
                actor,
                {
                    'machines': [
                        {'machine_id': machine.pk, 'expected_placement_version': 0}
                    ],
                    'destination_location_id': destination.pk,
                    'reason': SEED_REASON,
                    'idempotency_key': str(uuid.uuid4()),
                },
            )
            assigned += 1
        if options['dry_run']:
            transaction.set_rollback(True)
        self.stdout.write(
            self.style.SUCCESS(
                f'{"Validated" if options["dry_run"] else "Loaded"}: {created} new locations, '
                f'{assigned} initial placements; preserved {preserved} existing placements. '
                'Earlier history remains unknown.'
            )
        )
