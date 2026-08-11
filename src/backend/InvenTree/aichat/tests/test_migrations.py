"""Migration round-trip test for the durable chat foundation."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag('migration_test')
class AIChatMigrationTests(TransactionTestCase):
    """Prove the initial app migration can move forward and roll back."""

    migrate_from = [('aichat', None)]
    migrate_to = [('aichat', '0001_initial')]

    def test_initial_migration_round_trip(self) -> None:
        """Create and remove all three durable store tables."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        tables = set(connection.introspection.table_names())
        self.assertNotIn('aichat_chatthread', tables)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        tables = set(connection.introspection.table_names())
        self.assertIn('aichat_chatthread', tables)
        self.assertIn('aichat_chatmessage', tables)
        self.assertIn('aichat_chatturn', tables)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.assertNotIn(
            'aichat_chatthread',
            set(connection.introspection.table_names()),
        )

        MigrationExecutor(connection).migrate(self.migrate_to)


@tag('migration_test')
class ThreadGrantMigrationTests(TransactionTestCase):
    """Prove 0015 adds the grant table additively and reverses cleanly."""

    migrate_from = [('aichat', '0014_drop_scoped_chat')]
    migrate_to = [('aichat', '0015_chatthreadgrant')]

    def test_grant_table_round_trips(self) -> None:
        """The grant table appears, reverses, and re-applies."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.assertNotIn(
            'aichat_chatthreadgrant', set(connection.introspection.table_names())
        )

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.assertIn(
            'aichat_chatthreadgrant', set(connection.introspection.table_names())
        )

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)
        self.assertNotIn(
            'aichat_chatthreadgrant', set(connection.introspection.table_names())
        )

        MigrationExecutor(connection).migrate(self.migrate_to)
