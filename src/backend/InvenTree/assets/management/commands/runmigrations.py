"""Run guarded migrations using InvenTree's native migration startup mode."""

from django.core.management.base import BaseCommand
from django.db import connections, transaction
from django.db.migrations.executor import MigrationExecutor

from assets.deploy_lineage import report
from assets.deployment_migrations import apply


class Command(BaseCommand):
    """Preflight or atomically apply the complete forward migration plan."""

    help = 'Check lineage and apply an atomic PostgreSQL rollout; --check only reads metadata.'
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        """Select the configured database and optional read-only preflight."""
        parser.add_argument('--database', default='default')
        parser.add_argument('--check', action='store_true')

    def handle(self, *args, **options):
        """Keep planning and application behind the same lineage guard."""
        connection = connections[options['database']]
        if options['check']:
            with transaction.atomic(using=connection.alias):
                if connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute('SET TRANSACTION READ ONLY')
                report(connection, out=self.stdout)
                MigrationExecutor(connection).loader.check_consistent_history(
                    connection
                )
        else:
            apply(connection, out=self.stdout)
