"""Populate locations, a station, pump slots and equipment from a workbook.

Three modes, from safest to least:

``--plan``     read the workbook and the database, print the diff. No writes.
``--dry-run``  perform every write inside a transaction, print the report,
               roll back. Shows generated names and validation outcomes.
``--apply``    keep the writes. Requires ``--backup-ref`` naming the backup
               taken first, and refuses a non-local database host unless
               ``--allow-remote`` is also given.

The default is ``--plan``.
"""

import json
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from assets.population import PopulationError, apply_population, plan_population
from assets.population_workbook import WorkbookError, load_population_workbook

LOCAL_HOSTS = frozenset({'', 'localhost', '127.0.0.1', '::1'})


def database_host() -> str:
    """The configured default database host, for the remote-write gate."""
    return str(settings.DATABASES.get('default', {}).get('HOST', '') or '')


def is_local_database() -> bool:
    """Treat a socket, loopback or compose-internal host name as local."""
    host = database_host()
    if host in LOCAL_HOSTS or host.startswith('/'):
        return True
    # Compose service names have no dots; managed hosts always do.
    return '.' not in host


class Command(BaseCommand):
    """Operator-only population; writes gated behind an explicit backup reference."""

    help = 'Plan, preview or apply a UI population workbook (locations, station, pumps, equipment).'

    def add_arguments(self, parser):
        """Workbook path, mode switches and the write gate."""
        parser.add_argument('workbook', type=Path)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--plan', action='store_true', help='Diff only (default)')
        mode.add_argument(
            '--dry-run',
            action='store_true',
            help='Write inside a rolled-back transaction',
        )
        mode.add_argument('--apply', action='store_true', help='Keep the writes')
        parser.add_argument(
            '--actor', help='Username performing location and placement writes'
        )
        parser.add_argument(
            '--client-name', help='Create the workbook client with this name if absent'
        )
        parser.add_argument(
            '--client-code',
            help=(
                'Populate this existing client instead of the workbook client_code '
                '(for a single-site deployment); never created implicitly'
            ),
        )
        parser.add_argument(
            '--station',
            type=int,
            dest='station_pk',
            help=(
                'Adopt this already-registered station (machine id) instead of '
                'matching the workbook source identity; the registered identity is kept'
            ),
        )
        parser.add_argument(
            '--station-uuid',
            type=UUID,
            help='Reserve this public UUID for the station (pump UUIDs derive from it)',
        )
        parser.add_argument(
            '--backup-ref',
            help='Identifier of the database backup taken before --apply',
        )
        parser.add_argument(
            '--allow-remote',
            action='store_true',
            help='Permit --apply against a non-local database host',
        )
        parser.add_argument(
            '--json', action='store_true', help='Machine-readable output'
        )

    def handle(self, *args, **options):
        """Validate the gate, then run the selected mode."""
        try:
            book = load_population_workbook(options['workbook'])
        except WorkbookError as exc:
            raise CommandError(f'Workbook rejected: {exc}') from exc

        apply = options['apply']
        dry_run = options['dry_run']
        if apply:
            if not options['backup_ref']:
                raise CommandError(
                    '--apply requires --backup-ref naming the backup taken first.'
                )
            if not is_local_database() and not options['allow_remote']:
                raise CommandError(
                    f'Database host {database_host()!r} is not local; re-run with '
                    '--allow-remote once the owner has approved the write.'
                )

        try:
            if not apply and not dry_run:
                plan = plan_population(
                    book,
                    client_name=options['client_name'],
                    station_uuid=options['station_uuid'],
                    client_code=options['client_code'],
                    station_pk=options['station_pk'],
                )
                payload = {'mode': 'plan', 'workbook': book.summary(), **plan.as_dict()}
            else:
                actor = self._actor(options['actor'])
                report = apply_population(
                    book,
                    actor,
                    dry_run=dry_run,
                    client_name=options['client_name'],
                    station_uuid=options['station_uuid'],
                    client_code=options['client_code'],
                    station_pk=options['station_pk'],
                )
                payload = {
                    'mode': 'dry-run' if dry_run else 'apply',
                    'workbook': book.summary(),
                    'database_host': database_host(),
                    'backup_ref': options['backup_ref'],
                    **report,
                }
        except PopulationError as exc:
            raise CommandError(str(exc)) from exc

        if options['json']:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        else:
            self._render(payload)

    def _actor(self, username):
        if not username:
            raise CommandError('--actor is required for --dry-run and --apply.')
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f'Unknown actor: {username}')
        return user

    def _render(self, payload):
        summary = payload['workbook']
        self.stdout.write(
            f'Workbook {summary["sha256"][:12]}: {summary["locations"]} locations, '
            f'station={summary["station"]!r}, {summary["pumps"]} pumps, '
            f'{summary["equipment"]} equipment, {summary["profiles"]} profiles, '
            f'{summary["parts"]} parts, {summary["components"]} components, '
            f'{summary["part_links"]} part links'
        )
        self.stdout.write(
            f'  operations: {summary["stock_locations"]} warehouse locations, '
            f'{summary["companies"]} companies, {summary["offers"]} offers, '
            f'{summary["stock_items"]} stock items, {summary["history"]} history, '
            f'{summary["work_orders"]} work orders'
        )
        if payload['mode'] == 'plan':
            self.stdout.write(
                f'Client {payload["client_code"]}: {payload["client_action"]}'
            )
            for kind, actions in payload['counts'].items():
                line = ', '.join(
                    f'{action}={n}' for action, n in sorted(actions.items())
                )
                self.stdout.write(f'  {kind:<17} {line}')
            for warning in payload['warnings']:
                self.stdout.write(self.style.WARNING(f'  warn: {warning}'))
            for conflict in payload['conflicts']:
                self.stdout.write(self.style.ERROR(f'  conflict: {conflict}'))
            if payload['blocked']:
                blocked = payload['blocked']
                self.stdout.write(
                    f'  blocked    components={blocked["components"]} '
                    f'part_links={blocked["part_links"]} '
                    f'parts_missing={blocked["parts_missing_count"]}/'
                    f'{blocked["parts_required"]}'
                )
            self._render_manual(payload.get('manual') or {})
            if payload['conflicts']:
                raise CommandError(
                    f'{len(payload["conflicts"])} conflict(s); not applicable.'
                )
            return

        label = 'DRY RUN (rolled back)' if payload['dry_run'] else 'APPLIED'
        self.stdout.write(
            self.style.SUCCESS(f'{label} against {payload["database_host"]!r}')
        )
        for key in (
            'client',
            'locations',
            'station',
            'pumps',
            'equipment',
            'details',
            'placements',
            'categories',
            'parts',
            'components',
            'part_links',
            'stock_locations',
            'companies',
            'manufacturer_parts',
            'supplier_parts',
            'stock_items',
            'history',
            'work_orders',
        ):
            self.stdout.write(f'  {key:<18} {payload.get(key)}')
        for warning in payload['warnings']:
            self.stdout.write(self.style.WARNING(f'  warn: {warning}'))
        if payload['blocked']:
            self.stdout.write(
                f'  blocked    parts_missing={payload["blocked"]["parts_missing_count"]}'
            )
        self._render_manual(payload.get('manual') or {})

    def _render_manual(self, manual):
        for key, entry in manual.items():
            self.stdout.write(
                f'  manual     {key}: {entry["rows"]} rows - {entry["note"]}'
            )
