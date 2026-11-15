"""Real test-reset capture/foreign-key regressions; ordinary deletion stays live."""

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.commands.flush import Command as NativeFlush
from django.db import connection
from django.test import TransactionTestCase, override_settings

from assets.models import AssetMachine
from integration_state.capture import capture_is_installed, install_capture
from integration_state.models import SourceChange, SourceNamespace, SourceState


class SQLiteCaptureResetTests(TransactionTestCase):
    """Only scratch test databases are flushed by these controls."""

    def setUp(self):
        """Install the real capture and create an actual captured source row."""
        super().setUp()
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite DELETE reset differs from PostgreSQL TRUNCATE')
        if not capture_is_installed(connection):
            install_capture(connection)
        self.namespace = SourceNamespace.objects.create(
            environment='sqlite-reset-fixture', active=True
        )
        self.machine = AssetMachine.objects.create(
            name='Synthetic captured reset source'
        )
        self.assertTrue(capture_is_installed(connection))
        self.assertTrue(SourceChange.objects.exists())

    def test_reset_clears_captured_source_without_reinserting_dangling_events(self):
        """Adverse valid table order used to create FK orphans during DELETE flush."""
        native = connection.ops.sql_flush

        def ordered(*args, **kwargs):
            statements = native(*args, **kwargs)
            ranks = {
                'integration_state_sourcestate': 1,
                'integration_state_sourcechange': 2,
                'assets_assetmachine': 3,
                'integration_state_sourcenamespace': 4,
            }
            return sorted(
                statements,
                key=lambda sql: next(
                    (
                        rank
                        for table, rank in ranks.items()
                        if sql.startswith(f'DELETE FROM "{table}"')
                    ),
                    0,
                ),
            )

        with patch.object(connection.ops, 'sql_flush', side_effect=ordered):
            call_command('flush', interactive=False, verbosity=0)
        self.assertFalse(AssetMachine.objects.exists())
        self.assertFalse(SourceNamespace.objects.exists())
        self.assertFalse(SourceState.objects.exists())
        self.assertFalse(SourceChange.objects.exists())
        connection.check_constraints()
        self.assertTrue(capture_is_installed(connection))
        # The reset must not leave future normal source writes uncaptured.
        SourceNamespace.objects.create(environment='sqlite-reset-next', active=True)
        AssetMachine.objects.create(name='Synthetic post-reset source')
        self.assertTrue(SourceChange.objects.exists())

    def test_failed_reset_restores_catalog_and_preserves_original_exception(self):
        """A failed test reset cannot silently disable subsequent source capture."""
        error = MemoryError('synthetic-reset-interruption')
        with patch.object(NativeFlush, 'handle', side_effect=error):
            with self.assertRaises(MemoryError) as caught:
                call_command('flush', interactive=False, verbosity=0)
        self.assertIs(caught.exception, error)
        self.assertTrue(capture_is_installed(connection))
        self.assertTrue(AssetMachine.objects.filter(pk=self.machine.pk).exists())
        self.machine.delete()
        self.assertTrue(SourceState.objects.filter(deleted=True).exists())

    def test_non_test_flush_does_not_suspend_capture(self):
        """The test-only reset accommodation cannot alter an ordinary flush call."""
        sentinel = RuntimeError('native-flush-boundary')
        with (
            override_settings(TESTING=False),
            patch.object(NativeFlush, 'handle', side_effect=sentinel),
            patch('integration_state.capture.uninstall_capture') as uninstall,
        ):
            with self.assertRaises(RuntimeError) as caught:
                call_command('flush', interactive=False, verbosity=0)
        self.assertIs(caught.exception, sentinel)
        uninstall.assert_not_called()
        self.assertTrue(capture_is_installed(connection))
        self.assertTrue(AssetMachine.objects.filter(pk=self.machine.pk).exists())
