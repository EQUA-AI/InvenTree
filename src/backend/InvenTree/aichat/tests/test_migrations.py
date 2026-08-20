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


@tag('migration_test')
class AttachmentRagMigrationTests(TransactionTestCase):
    """Prove 0018-0020 create the RAG registry additively and reverse cleanly."""

    migrate_from = [('aichat', '0017_thread_summary_watermark')]
    migrate_to = [('aichat', '0021_retrievalmiss_corpus_part_filter')]

    def _columns(self, table: str) -> set[str]:
        """Introspect a table's column names."""
        with connection.cursor() as cursor:
            return {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, table
                )
            }

    def test_registry_tables_round_trip(self) -> None:
        """Registry tables appear with the hardening columns and reverse."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        tables = set(connection.introspection.table_names())
        self.assertNotIn('aichat_attachmentingest', tables)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        tables = set(connection.introspection.table_names())
        for table in (
            'aichat_attachmentingest',
            'aichat_attachmentchunk',
            'aichat_mediasegment',
        ):
            self.assertIn(table, tables)
        columns = self._columns('aichat_attachmentingest')
        # 0019's additive extractor + 0020's additive claim fence.
        self.assertIn('extractor', columns)
        self.assertIn('claimed_at', columns)
        self.assertIn('client_codes', columns)
        # 0021's additive R2 ledger columns.
        miss_columns = self._columns('aichat_retrievalmiss')
        self.assertIn('corpus', miss_columns)
        self.assertIn('part_filter', miss_columns)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)
        self.assertNotIn(
            'aichat_attachmentingest',
            set(connection.introspection.table_names()),
        )

        MigrationExecutor(connection).migrate(self.migrate_to)
