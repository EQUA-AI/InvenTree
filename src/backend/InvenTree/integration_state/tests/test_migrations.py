"""Direct migration-callback controls; native executor qualification is separate."""

from importlib import import_module
from types import SimpleNamespace
from unittest import skipUnless

from django.apps import apps
from django.conf import settings
from django.db import connection, migrations
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, TransactionTestCase

from integration_state.models import SourceNamespace


class CaptureMigrationCallbackTests(TestCase):
    """Actual synthetic DB evidence is retained; this is not an upgrade replay."""

    def test_initial_schema_also_checks_retained_state_before_dropping_tables(self):
        """Rolling back an initial-only installation must not bypass protection."""
        initial = import_module('integration_state.migrations.0001_initial')
        operation = initial.Migration.operations[-1]
        self.assertIsInstance(operation, migrations.RunPython)
        namespace = SourceNamespace.objects.create(environment='initial-only-retained')
        with self.assertRaises(IrreversibleError):
            operation.reverse_code(apps, SimpleNamespace(connection=connection))
        self.assertTrue(SourceNamespace.objects.filter(pk=namespace.pk).exists())

    def test_populated_state_refuses_capture_reversal_without_deleting_data(self):
        """Even inactive retained metadata needs a reviewed recovery disposition."""
        from integration_state.migration_operations import reverse_placement_capture

        namespace = SourceNamespace.objects.create(environment='synthetic-retained')
        with self.assertRaises(IrreversibleError):
            reverse_placement_capture(apps, SimpleNamespace(connection=connection))
        self.assertTrue(SourceNamespace.objects.filter(pk=namespace.pk).exists())

    def test_empty_capture_reversal_and_reapplication_use_actual_database(self):
        """An empty owned store may back out its capture without touching source."""
        from integration_state.capture import INSERT_TRIGGER
        from integration_state.migration_operations import (
            apply_placement_capture,
            reverse_placement_capture,
        )

        def installed():
            with connection.cursor() as cursor:
                if connection.vendor == 'sqlite':
                    cursor.execute(
                        'SELECT count(*) FROM sqlite_master WHERE type=%s AND name=%s',
                        ['trigger', INSERT_TRIGGER],
                    )
                else:
                    cursor.execute(
                        'SELECT count(*) FROM pg_trigger WHERE tgname=%s',
                        [INSERT_TRIGGER],
                    )
                return cursor.fetchone()[0]

        schema_editor = SimpleNamespace(connection=connection)
        self.assertEqual(installed(), 1)
        reverse_placement_capture(apps, schema_editor)
        self.assertEqual(installed(), 0)
        apply_placement_capture(apps, schema_editor)
        self.assertEqual(installed(), 1)


@skipUnless(
    getattr(settings, 'S5_NATIVE_MIGRATIONS', False)
    and connection.vendor == 'postgresql',
    'Requires the explicitly admitted native PostgreSQL migration profile',
)
class NativePlacementMigrationTests(TransactionTestCase):
    """Fresh native setup plus real populated additive-history transitions."""

    def migrate(self, target):
        """Reload actual applied history for each executor transition."""
        MigrationExecutor(connection).migrate([('integration_state', target)])

    def final_target(self):
        """Restore the actual allocated additive leaf, not an older schema."""
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes(
            'integration_state'
        )
        self.assertEqual(len(leaves), 1)
        self.assertIn(
            leaves[0][1],
            (
                '0002_machine_placement_capture',
                '0003_generic_source_events',
                '0004_event_delivery',
                '0005_reconciliation_progress',
                '0006_private_request_copy_progress',
                '0007_native_authority',
            ),
        )
        return leaves[0][1]

    def test_native_schema_model_columns_and_declared_constraints_agree(self):
        """Real migration state and installed metadata match the runtime models."""
        from django.db import models
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.state import ProjectState

        loader = MigrationLoader(connection)
        changes = MigrationAutodetector(
            loader.project_state(), ProjectState.from_apps(apps)
        ).changes(graph=loader.graph, trim_to_apps={'integration_state'})
        self.assertFalse(changes.get('integration_state'), changes)
        with connection.cursor() as cursor:
            for model in apps.get_app_config('integration_state').get_models():
                with self.subTest(model=model._meta.label):
                    description = connection.introspection.get_table_description(
                        cursor, model._meta.db_table
                    )
                    self.assertEqual(
                        {column.name for column in description},
                        {field.column for field in model._meta.local_concrete_fields},
                    )
                    constraints = connection.introspection.get_constraints(
                        cursor, model._meta.db_table
                    )
                    for constraint in model._meta.constraints:
                        self.assertIn(constraint.name, constraints)
                        actual = constraints[constraint.name]
                        if isinstance(constraint, models.CheckConstraint):
                            self.assertTrue(actual['check'])
                        elif isinstance(constraint, models.UniqueConstraint):
                            self.assertTrue(actual['unique'])
                            self.assertEqual(
                                actual['columns'],
                                [
                                    model._meta.get_field(name).column
                                    for name in constraint.fields
                                ],
                            )

    def tearDown(self):
        """Restore only this owned test database before the native test flush."""
        self.migrate(self.final_target())
        if SourceNamespace._meta.db_table in connection.introspection.table_names():
            SourceNamespace.objects.filter(
                environment='synthetic-native-retained'
            ).delete()
        super().tearDown()

    def test_populated_native_foundation_upgrade_preserves_source(self):
        """A real native-only foundation upgrades without adopting rows as current."""
        from assets.location_models import AssetLocation
        from assets.models import AssetMachine, Client

        owner = Client.objects.create(name='Synthetic native upgrade', code='s5-up')
        location = AssetLocation.objects.create(
            client=owner,
            name='Synthetic preserved placement',
            code='s5-up-location',
            name_key='synthetic preserved placement',
            sibling_key='root',
        )
        machine = AssetMachine.objects.create(
            name='Synthetic legacy placement',
            client=owner,
            physical_location=location,
            placement_version=7,
        )

        def snapshot():
            return AssetMachine.objects.values(
                'client_id',
                'physical_location_id',
                'placement_version',
                'uuid',
                'name',
                'created_at',
                'updated_at',
            ).get(pk=machine.pk)

        expected = snapshot()
        native_history = {
            key
            for key in MigrationRecorder(connection).applied_migrations()
            if key[0] != 'integration_state'
        }
        self.assertFalse(SourceNamespace.objects.exists())
        self.migrate(None)
        self.assertNotIn(
            SourceNamespace._meta.db_table, connection.introspection.table_names()
        )
        self.assertEqual(snapshot(), expected)
        self.migrate(self.final_target())
        self.assertEqual(snapshot(), expected)
        self.assertFalse(SourceNamespace.objects.exists())
        self.assertEqual(
            {
                key
                for key in MigrationRecorder(connection).applied_migrations()
                if key[0] != 'integration_state'
            },
            native_history,
        )

    def test_native_executor_refuses_populated_backout_at_both_positions(self):
        """Neither capture nor initial-only rollback may drop retained metadata."""
        for position in ('0002_machine_placement_capture', '0001_initial'):
            with self.subTest(position=position):
                self.migrate(position)
                namespace = SourceNamespace.objects.create(
                    environment='synthetic-native-retained'
                )
                try:
                    with self.assertRaises(IrreversibleError):
                        self.migrate(None)
                    self.assertTrue(
                        SourceNamespace.objects.filter(pk=namespace.pk).exists()
                    )
                    self.assertIn(
                        ('integration_state', position),
                        MigrationRecorder(connection).applied_migrations(),
                    )
                finally:
                    # Current ORM deletion traverses new reverse relations: restore
                    # their actual schema before cleaning only the synthetic row.
                    self.migrate(self.final_target())
                    if (
                        SourceNamespace._meta.db_table
                        in connection.introspection.table_names()
                    ):
                        SourceNamespace.objects.filter(
                            environment='synthetic-native-retained'
                        ).delete()
