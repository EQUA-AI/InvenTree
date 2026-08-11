"""Migration tests for the client backfill and the customer column removal."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag('migration_test')
class ClientBackfillMigrationTests(TransactionTestCase):
    """Prove 0009 adopts clientless machines and 0010 drops the column."""

    migrate_from = [('assets', '0008_machineanomaly_repair_packet')]
    migrate_to = [('assets', '0010_remove_assetmachine_customer')]

    def _machine_columns(self) -> set[str]:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(
                cursor, 'assets_assetmachine'
            )
        return {column.name for column in description}

    def test_backfill_assigns_the_internal_client_and_drops_customer(self) -> None:
        """A pre-existing machine gains the internal client; the column dies."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        OldMachine = old_apps.get_model('assets', 'AssetMachine')
        OldClient = old_apps.get_model('assets', 'Client')
        orphan = OldMachine.objects.create(name='Backfill orphan')
        owned_client = OldClient.objects.create(
            name='Pre-existing tenant', code='pre-existing'
        )
        owned = OldMachine.objects.create(name='Backfill owned', client=owned_client)
        self.assertIn('customer_id', self._machine_columns())

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps

        NewMachine = new_apps.get_model('assets', 'AssetMachine')
        NewClient = new_apps.get_model('assets', 'Client')

        internal = NewClient.objects.get(code='internal')
        self.assertEqual(internal.name, 'Internal')
        self.assertEqual(NewMachine.objects.get(pk=orphan.pk).client_id, internal.pk)
        # A machine that already had a tenant is not re-adopted.
        self.assertEqual(NewMachine.objects.get(pk=owned.pk).client_id, owned_client.pk)
        self.assertNotIn('customer_id', self._machine_columns())

        # Roll back: the column returns and the migration-created tenant is
        # removed, releasing only the machines it had adopted.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)

        self.assertIn('customer_id', self._machine_columns())
        rollback_apps = executor.loader.project_state(self.migrate_from).apps
        RolledBackClient = rollback_apps.get_model('assets', 'Client')
        RolledBackMachine = rollback_apps.get_model('assets', 'AssetMachine')
        self.assertFalse(RolledBackClient.objects.filter(code='internal').exists())
        self.assertIsNone(RolledBackMachine.objects.get(pk=orphan.pk).client_id)
        self.assertEqual(
            RolledBackMachine.objects.get(pk=owned.pk).client_id, owned_client.pk
        )

        MigrationExecutor(connection).migrate(self.migrate_to)


@tag('migration_test')
class ProfileFieldMigrationTests(TransactionTestCase):
    """Prove 0011 adds the profile column additively and reverses cleanly."""

    migrate_from = [('assets', '0010_remove_assetmachine_customer')]
    migrate_to = [('assets', '0011_assetmachine_profile')]

    def _machine_columns(self) -> set[str]:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(
                cursor, 'assets_assetmachine'
            )
        return {column.name for column in description}

    def test_profile_column_round_trips(self) -> None:
        """Existing rows survive forward and backward with data intact."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldMachine = old_apps.get_model('assets', 'AssetMachine')
        machine = OldMachine.objects.create(name='Profile migration pump')
        self.assertNotIn('profile', self._machine_columns())

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.assertIn('profile', self._machine_columns())
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewMachine = new_apps.get_model('assets', 'AssetMachine')
        migrated = NewMachine.objects.get(pk=machine.pk)
        self.assertEqual(migrated.profile, {})
        self.assertEqual(migrated.name, 'Profile migration pump')

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)
        self.assertNotIn('profile', self._machine_columns())

        MigrationExecutor(connection).migrate(self.migrate_to)


@tag('migration_test')
class BarcodeFieldMigrationTests(TransactionTestCase):
    """Prove 0012 adds the barcode columns additively and reverses cleanly."""

    migrate_from = [('assets', '0011_assetmachine_profile')]
    migrate_to = [
        ('assets', '0012_assetmachine_barcode_data_assetmachine_barcode_hash')
    ]

    def _machine_columns(self) -> set[str]:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(
                cursor, 'assets_assetmachine'
            )
        return {column.name for column in description}

    def test_barcode_columns_round_trip(self) -> None:
        """Existing rows survive forward and backward with data intact."""
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldMachine = old_apps.get_model('assets', 'AssetMachine')
        machine = OldMachine.objects.create(name='Barcode migration pump')
        self.assertNotIn('barcode_data', self._machine_columns())

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.assertIn('barcode_data', self._machine_columns())
        self.assertIn('barcode_hash', self._machine_columns())
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewMachine = new_apps.get_model('assets', 'AssetMachine')
        migrated = NewMachine.objects.get(pk=machine.pk)
        self.assertEqual(migrated.barcode_data, '')
        self.assertEqual(migrated.name, 'Barcode migration pump')

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_from)
        self.assertNotIn('barcode_data', self._machine_columns())

        MigrationExecutor(connection).migrate(self.migrate_to)
