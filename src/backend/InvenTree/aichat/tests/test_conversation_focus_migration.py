"""Exercise the focus migration against historical models and existing rows."""

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from aimms_testing import MigrationRoundTripMixin


@tag('migration_test')
class ConversationFocusMigrationTests(MigrationRoundTripMixin, TransactionTestCase):
    """Preserve old threads, initialize focus, and support reverse/reapplication."""

    migrate_from = [('aichat', '0053_controlled_projection_audit')]
    migrate_to = [('aichat', '0055_chatthread_focus_db_defaults')]

    def migrate(self, targets):
        """Return the actual historical model registry after migrating."""
        if connection.vendor == 'postgresql' and connection.in_atomic_block:
            # The operator harness keeps probes rollback-only. Validate any
            # deferred foreign keys before PostgreSQL can perform further DDL.
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def legacy_thread(self, apps, suffix):
        """Insert through the pre-focus model, with no new fields available."""
        user = apps.get_model('auth', 'User').objects.create(
            username=f'focus-migration-{suffix}', password='!unusable'
        )
        return apps.get_model('aichat', 'ChatThread').objects.create(
            id=f'thread_focus_migration_{suffix}',
            owner_id=user.pk,
            scope_key='migration-test',
            scope_hash='a' * 64,
            title='Preserve this existing conversation',
            summary='Existing summary',
            next_sequence=7,
        )

    def test_existing_threads_defaults_constraints_and_round_trip(self):
        """Run the real migration forward, reverse, and forward again."""
        old_apps = self.migrate(self.migrate_from)
        thread = self.legacy_thread(old_apps, 'existing')
        old_thread = old_apps.get_model('aichat', 'ChatThread')
        original = old_thread.objects.filter(pk=thread.pk).values().get()

        apps = self.migrate(self.migrate_to)
        focused_thread = apps.get_model('aichat', 'ChatThread')
        migrated = focused_thread.objects.get(pk=thread.pk)
        self.assertEqual(migrated.conversation_focus, {})
        self.assertEqual(migrated.focus_version, 0)
        self.assertEqual(
            focused_thread.objects.filter(pk=thread.pk).values(*original).get(),
            original,
        )

        # Callable JSON defaults must not share a mutable dictionary.
        first, second = focused_thread(), focused_thread()
        first.conversation_focus['target'] = 'synthetic'
        self.assertEqual(second.conversation_focus, {})

        migrated.conversation_focus = {'kind': 'machine', 'id': 'synthetic'}
        migrated.focus_version = 3
        migrated.save(update_fields=['conversation_focus', 'focus_version'])
        self.migrate(self.migrate_to)
        migrated.refresh_from_db()
        self.assertEqual(migrated.focus_version, 3)
        self.assertEqual(migrated.conversation_focus['id'], 'synthetic')

        with self.assertRaises(IntegrityError), transaction.atomic():
            focused_thread.objects.filter(pk=thread.pk).update(focus_version=-1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            focused_thread.objects.filter(pk=thread.pk).update(conversation_focus=None)

        self.migrate(self.migrate_from)
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, 'aichat_chatthread'
                )
            }
        self.assertNotIn('conversation_focus', columns)
        self.assertNotIn('focus_version', columns)
        self.assertEqual(
            old_thread.objects.filter(pk=thread.pk).values().get(), original
        )

        apps = self.migrate(self.migrate_to)
        reapplied = apps.get_model('aichat', 'ChatThread').objects.get(pk=thread.pk)
        self.assertEqual(reapplied.conversation_focus, {})
        self.assertEqual(reapplied.focus_version, 0)

    def test_pre_focus_writer_can_create_threads_after_upgrade(self):
        """The still-serving old model must survive the additive schema change."""
        old_apps = self.migrate(self.migrate_from)
        self.migrate(self.migrate_to)
        thread = self.legacy_thread(old_apps, 'old_writer')
        current_apps = (
            MigrationExecutor(connection).loader.project_state(self.migrate_to).apps
        )
        created = current_apps.get_model('aichat', 'ChatThread').objects.get(
            pk=thread.pk
        )
        self.assertEqual(created.conversation_focus, {})
        self.assertEqual(created.focus_version, 0)
