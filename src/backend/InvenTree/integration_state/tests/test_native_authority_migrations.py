"""Exact additive graph/state and actual-native authority schema controls."""

from importlib import import_module
from unittest import skipUnless

from django.conf import settings
from django.db import connection, migrations
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ModelState
from django.test import TestCase, TransactionTestCase, override_settings

from integration_state.models import (
    NativeAuthorityDecision,
    NativeAuthorityEpoch,
    NativeAuthorityRequest,
    NativeAuthorityWithdrawal,
    NativeSourceBirth,
    SourceNamespace,
)

MODELS = (
    NativeAuthorityEpoch,
    NativeSourceBirth,
    NativeAuthorityRequest,
    NativeAuthorityWithdrawal,
    NativeAuthorityDecision,
)
BEFORE = ('integration_state', '0006_private_request_copy_progress')
AFTER = ('integration_state', '0007_native_authority')


class AuthorityMigrationStateTests(TestCase):
    """No model-sync run is presented as execution of a migration."""

    def test_actual_effective_graph_and_new_model_state_match(self):
        """The new node depends on the real parent and matches every new model."""
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None)
        self.assertIn(AFTER, loader.graph.leaf_nodes())
        self.assertIn(BEFORE, loader.graph.forwards_plan(AFTER))
        migration = import_module(
            'integration_state.migrations.0007_native_authority'
        ).Migration
        self.assertEqual(migration.dependencies, [BEFORE])
        state = loader.project_state([BEFORE])
        changed = migration('0007_native_authority', 'integration_state').mutate_state(
            state
        )
        for model in MODELS:
            with self.subTest(model=model._meta.model_name):
                self.assertEqual(
                    changed.models['integration_state', model._meta.model_name],
                    ModelState.from_model(model),
                )

    def test_retention_guard_precedes_any_schema_drop(self):
        """The last forward operation runs first during guarded backout."""
        migration = import_module(
            'integration_state.migrations.0007_native_authority'
        ).Migration
        operation = migration.operations[-1]
        self.assertIsInstance(operation, migrations.RunPython)
        self.assertEqual(operation.reverse_code.__name__, 'guard_native_authority')
        self.assertFalse(
            any(isinstance(op, migrations.RunSQL) for op in migration.operations)
        )


@skipUnless(
    getattr(settings, 'S5_NATIVE_MIGRATIONS', False),
    'Requires actual native migration profile',
)
class AuthorityNativeMigrationTests(TransactionTestCase):
    """Run only on an owned database created by the actual native graph."""

    def test_native_schema_and_applied_history_include_the_new_node(self):
        """Actual tables and recorder history prove execution, not model sync."""
        self.assertIn(AFTER, MigrationRecorder(connection).applied_migrations())
        tables = connection.introspection.table_names()
        for model in MODELS:
            self.assertIn(model._meta.db_table, tables)

    def test_populated_upgrade_preserves_existing_namespace_and_does_not_enroll(self):
        """Existing source metadata survives upgrade and empty-only backout."""
        executor = MigrationExecutor(connection)
        executor.migrate([BEFORE])
        old_apps = executor.loader.project_state([BEFORE]).apps
        namespace = old_apps.get_model(
            'integration_state', 'SourceNamespace'
        ).objects.create(environment='native-authority-upgrade', active=False)
        pk, epoch = namespace.pk, namespace.epoch
        try:
            MigrationExecutor(connection).migrate([AFTER])
            row = SourceNamespace.objects.get(pk=pk)
            self.assertEqual(row.epoch, epoch)
            self.assertEqual(row.environment, 'native-authority-upgrade')
            self.assertFalse(row.active)
            for model in MODELS:
                self.assertEqual(model.objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate([AFTER])

    def test_native_reverse_refuses_retained_history(self):
        """A real executor cannot drop an enrolled epoch or its later audit."""
        namespace = SourceNamespace.objects.create(
            environment='native-authority-retained', active=False
        )
        epoch = NativeAuthorityEpoch.objects.create(
            namespace=namespace, namespace_epoch=namespace.epoch, active=False
        )
        with self.assertRaises(IrreversibleError):
            MigrationExecutor(connection).migrate([BEFORE])
        self.assertTrue(NativeAuthorityEpoch.objects.filter(pk=epoch.pk).exists())
        self.assertIn(AFTER, MigrationRecorder(connection).applied_migrations())
