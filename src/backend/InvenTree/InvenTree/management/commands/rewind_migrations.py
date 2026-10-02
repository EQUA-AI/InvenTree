"""Preview an ordered schema rewind for an explicitly disposable SQLite database."""

import hashlib
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from InvenTree.migration_rewind import ordered_rewind_plan


class Command(BaseCommand):
    """Rehearse a canonical rewind; never substitute it for production recovery."""

    help = (
        'Preview a canonical migration rewind (including later sibling/other-app '
        'migrations). Execution is opt-in and for disposable SQLite databases only.'
    )
    requires_system_checks = []

    def add_arguments(self, parser):
        """Require exact migration names and approval of the complete plan."""
        parser.add_argument('app_label')
        parser.add_argument('migration_name')
        parser.add_argument('--database', default='default', choices=tuple(connections))
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--disposable-database', action='store_true')
        parser.add_argument('--expected-plan-sha256')

    def handle(self, *args, **options):
        """Preview without writes, or execute precisely the acknowledged plan."""
        connection = connections[options['database']]
        if options['apply']:
            if connection.vendor != 'sqlite':
                raise CommandError(
                    'Execution is restricted to disposable SQLite databases.'
                )
            if not options['disposable_database']:
                raise CommandError(
                    'Execution requires --disposable-database acknowledgement.'
                )

        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        target = (options['app_label'], options['migration_name'])
        if target not in executor.loader.graph.nodes:
            raise CommandError('Use an exact app label and full migration name.')
        if target not in executor.loader.applied_migrations:
            raise CommandError('The exact target migration must already be applied.')
        try:
            requested = executor.migration_plan([target])
            plan = ordered_rewind_plan(executor, [target])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        keys = [(migration.app_label, migration.name) for migration, _ in plan]
        requested_keys = {
            (migration.app_label, migration.name) for migration, _ in requested
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    'database_alias': options['database'],
                    'database_name': str(connection.settings_dict['NAME']),
                    'target': target,
                    'plan': keys,
                },
                sort_keys=True,
                separators=(',', ':'),
            ).encode()
        ).hexdigest()
        report = {
            'database_alias': options['database'],
            'backend': connection.vendor,
            'target': target,
            'plan': keys,
            'additional_rewinds': [key for key in keys if key not in requested_keys],
            'plan_sha256': fingerprint,
            'warning': (
                'This rewinds a complete canonical suffix, including later '
                'migrations in other apps. Historical data operations may lose '
                'data on reversal. Disposable rehearsal only; deployments are forward-fix.'
            ),
        }
        if options['apply']:
            if options['expected_plan_sha256'] != fingerprint:
                raise CommandError(
                    'Plan hash does not match; review a new preview before execution.'
                )
            executor.migrate([target], plan=plan)
            report['executed'] = True
        else:
            report['executed'] = False
        self.stdout.write(json.dumps(report, indent=2))
