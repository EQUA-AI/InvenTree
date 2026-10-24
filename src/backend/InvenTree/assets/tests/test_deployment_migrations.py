"""Verify startup atomicity with real PostgreSQL DDL and migration records."""

from importlib import import_module
from io import StringIO
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, tag

from aimms_testing import MigrationRoundTripMixin, requires_postgres
from assets.deployment_migrations import MIGRATION_LOCK, apply

BEFORE = [('aichat', '0053_controlled_projection_audit')]
FOCUS = '0054_chatthread_conversation_focus'
DEFAULTS = '0055_chatthread_focus_db_defaults'


@requires_postgres
@tag('migration_test')
class AtomicStartupMigrationTests(MigrationRoundTripMixin, TransactionTestCase):
    """Failures and competing revisions cannot expose half of a rollout."""

    def setUp(self):
        """Start immediately before the two real focus migrations."""
        super().setUp()
        MigrationExecutor(connection).migrate(BEFORE)

    def observer(self):
        """Open a separate connection that sees only committed schema."""
        observer = connection.copy()
        self.addCleanup(observer.close)
        observer.ensure_connection()
        self.assertIsNot(observer.connection, connection.connection)
        return observer

    def focus_columns(self, database=connection):
        """Read committed catalog entries without locking the thread table."""
        with database.cursor() as cursor:
            cursor.execute(
                'SELECT column_name FROM information_schema.columns '
                "WHERE table_schema='public' AND table_name='aichat_chatthread' "
                "AND column_name IN ('conversation_focus', 'focus_version')"
            )
            return {row[0] for row in cursor.fetchall()}

    def test_both_migrations_become_visible_together_and_repeat_is_safe(self):
        """A second connection cannot see 0054 before 0055 commits."""
        observer = self.observer()
        original = MigrationExecutor.apply_migration
        inspected = []

        def inspect_step(executor, state, migration, *args, **kwargs):
            state = original(executor, state, migration, *args, **kwargs)
            if migration.app_label == 'aichat' and migration.name in (FOCUS, DEFAULTS):
                self.assertEqual(self.focus_columns(observer), set())
                with observer.cursor() as cursor:
                    cursor.execute(
                        'SELECT EXISTS (SELECT 1 FROM django_migrations '
                        'WHERE app=%s AND name IN (%s, %s))',
                        ['aichat', FOCUS, DEFAULTS],
                    )
                    self.assertFalse(cursor.fetchone()[0])
                inspected.append(migration.name)
            return state

        with patch.object(MigrationExecutor, 'apply_migration', inspect_step):
            apply(out=StringIO())
        self.assertEqual(inspected, [FOCUS, DEFAULTS])
        self.assertEqual(
            self.focus_columns(observer), {'conversation_focus', 'focus_version'}
        )
        before = set(MigrationRecorder(connection).applied_migrations())
        apply(out=StringIO())
        self.assertEqual(
            set(MigrationRecorder(connection).applied_migrations()), before
        )

    def test_failure_between_migrations_rolls_back_schema_and_ledger(self):
        """A crash after real 0054 DDL leaves neither columns nor records."""
        original = MigrationExecutor.apply_migration
        before = set(MigrationRecorder(connection).applied_migrations())

        def fail_after_focus(executor, state, migration, *args, **kwargs):
            state = original(executor, state, migration, *args, **kwargs)
            if migration.app_label == 'aichat' and migration.name == FOCUS:
                raise RuntimeError('Injected failure after focus DDL')
            return state

        with patch.object(MigrationExecutor, 'apply_migration', fail_after_focus):
            with self.assertRaisesRegex(RuntimeError, 'Injected failure'):
                apply(out=StringIO())
        self.assertEqual(self.focus_columns(), set())
        self.assertEqual(
            set(MigrationRecorder(connection).applied_migrations()), before
        )

    def test_concurrent_runner_refuses_before_ddl(self):
        """A separate transaction holds the same per-database rollout lock."""
        observer = self.observer()
        observer.set_autocommit(False)
        try:
            with observer.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [MIGRATION_LOCK])
            with self.assertRaisesRegex(RuntimeError, 'holds the lock'):
                apply(out=StringIO())
            self.assertEqual(self.focus_columns(), set())
        finally:
            observer.rollback()
            observer.set_autocommit(True)

    def test_unknown_lineage_still_refuses_without_schema_changes(self):
        """The atomic runner does not weaken the existing lineage refusal."""
        MigrationRecorder(connection).record_applied(
            'assets', '9999_unrecovered_source'
        )
        self.addCleanup(
            MigrationRecorder(connection).record_unapplied,
            'assets',
            '9999_unrecovered_source',
        )
        with self.assertRaises(SystemExit):
            apply(out=StringIO())
        self.assertEqual(self.focus_columns(), set())

    def test_non_atomic_plan_refuses_before_any_migration(self):
        """A migration opting out of transactions needs a maintenance run."""
        migration = import_module(f'aichat.migrations.{DEFAULTS}').Migration
        before = set(MigrationRecorder(connection).applied_migrations())
        with patch.object(migration, 'atomic', False):
            with self.assertRaisesRegex(RuntimeError, 'only atomic forward'):
                apply(out=StringIO())
        self.assertEqual(self.focus_columns(), set())
        self.assertEqual(
            set(MigrationRecorder(connection).applied_migrations()), before
        )
