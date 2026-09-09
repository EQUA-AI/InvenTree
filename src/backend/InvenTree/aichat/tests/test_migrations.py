"""Migration round-trip test for the durable chat foundation."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from aimms_testing import MigrationRoundTripMixin


@tag('migration_test')
class AIChatMigrationTests(MigrationRoundTripMixin, TransactionTestCase):
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
class ThreadGrantMigrationTests(MigrationRoundTripMixin, TransactionTestCase):
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
class AttachmentRagMigrationTests(MigrationRoundTripMixin, TransactionTestCase):
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


@tag('migration_test')
class CompactionEventMigrationTests(MigrationRoundTripMixin, TransactionTestCase):
    """Prove 0032 adds the compaction ledger additively and reverses cleanly."""

    migrate_from = [('aichat', '0031_attachment_rag_rebuildable')]
    migrate_to = [('aichat', '0032_chatcompactionevent')]

    def test_compaction_event_table_round_trips(self) -> None:
        """The ledger table appears with its indexes, reverses, re-applies."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.assertNotIn(
            'aichat_chatcompactionevent', set(connection.introspection.table_names())
        )

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.assertIn(
            'aichat_chatcompactionevent', set(connection.introspection.table_names())
        )
        with connection.cursor() as cursor:
            columns = {
                col.name
                for col in connection.introspection.get_table_description(
                    cursor, 'aichat_chatcompactionevent'
                )
            }
            constraints = connection.introspection.get_constraints(
                cursor, 'aichat_chatcompactionevent'
            )
        for column in (
            'outcome',
            'error_code',
            'latency_ms',
            'from_sequence',
            'through_sequence',
            'redacted_counts',
            'flag_state',
        ):
            self.assertIn(column, columns)
        self.assertIn('aichat_compaction_thread_idx', constraints)
        self.assertIn('aichat_compaction_outcome_idx', constraints)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)
        self.assertNotIn(
            'aichat_chatcompactionevent', set(connection.introspection.table_names())
        )

        MigrationExecutor(connection).migrate(self.migrate_to)


@tag('migration_test')
class WorkerUsageEventMigrationTests(MigrationRoundTripMixin, TransactionTestCase):
    """Prove 0033 adds the worker spend ledger additively and reverses cleanly."""

    migrate_from = [('aichat', '0032_chatcompactionevent')]
    migrate_to = [('aichat', '0033_aiworkerusageevent')]

    def test_worker_usage_table_round_trips(self) -> None:
        """The ledger table appears with its indexes, reverses, re-applies."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        tables = set(connection.introspection.table_names())
        self.assertNotIn('aichat_aiworkerusageevent', tables)
        # 0032's table is untouched on either side of the step.
        self.assertIn('aichat_chatcompactionevent', tables)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.assertIn(
            'aichat_aiworkerusageevent', set(connection.introspection.table_names())
        )
        with connection.cursor() as cursor:
            columns = {
                col.name
                for col in connection.introspection.get_table_description(
                    cursor, 'aichat_aiworkerusageevent'
                )
            }
            constraints = connection.introspection.get_constraints(
                cursor, 'aichat_aiworkerusageevent'
            )
        for column in (
            'purpose',
            'task',
            'thread_id',
            'deployment',
            'input_tokens',
            'output_tokens',
            'attempts',
            'created_at',
        ):
            self.assertIn(column, columns)
        self.assertIn('aichat_worker_usage_deploy_idx', constraints)
        self.assertIn('aichat_worker_usage_purp_idx', constraints)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)
        tables = set(connection.introspection.table_names())
        self.assertNotIn('aichat_aiworkerusageevent', tables)
        self.assertIn('aichat_chatcompactionevent', tables)

        MigrationExecutor(connection).migrate(self.migrate_to)


@tag('migration_test')
class CompactionDirectivesFlaggedMigrationTests(
    MigrationRoundTripMixin, TransactionTestCase
):
    """Prove 0034 adds ``directives_flagged`` additively and reverses cleanly."""

    migrate_from = [('aichat', '0033_aiworkerusageevent')]
    migrate_to = [('aichat', '0034_chatcompactionevent_directives_flagged')]

    def _columns(self) -> set[str]:
        with connection.cursor() as cursor:
            return {
                col.name
                for col in connection.introspection.get_table_description(
                    cursor, 'aichat_chatcompactionevent'
                )
            }

    def test_directives_flagged_column_round_trips(self) -> None:
        """The column appears beside the table's other counters, reverses, re-applies."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        columns = self._columns()
        self.assertNotIn('directives_flagged', columns)
        self.assertIn('directives_stripped', columns)
        self.assertIn('entropy_flags', columns)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        columns = self._columns()
        self.assertIn('directives_flagged', columns)
        self.assertIn('directives_stripped', columns)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)
        columns = self._columns()
        self.assertNotIn('directives_flagged', columns)
        self.assertIn('directives_stripped', columns)

        MigrationExecutor(connection).migrate(self.migrate_to)
