"""Registry UUID backfill tests against historical migration models."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag('migration_test')
class RegistryMigrationTests(TransactionTestCase):
    """Preserve legacy equipment while introducing independent public identities."""

    def test_uuid_backfill_preserves_legacy_records(self):
        """Three pre-registry machines receive distinct UUIDs without ID changes."""
        before = [('assets', '0010_remove_assetmachine_customer')]
        after = [('assets', '0011_equipment_registry')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            client = apps.get_model('assets', 'Client').objects.create(
                code='uuid-backfill-test', name='UUID backfill test'
            )
            old_model = apps.get_model('assets', 'AssetMachine')
            identities = [
                old_model.objects.create(
                    name=f'Legacy registry test {n}', client=client
                ).pk
                for n in range(3)
            ]
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            model = executor.loader.project_state(after).apps.get_model(
                'assets', 'AssetMachine'
            )
            records = list(model.objects.filter(pk__in=identities))
            self.assertEqual({r.pk for r in records}, set(identities))
            self.assertEqual(len({r.uuid for r in records}), 3)
            self.assertTrue(all(r.uuid and r.client_id == client.pk for r in records))
            self.assertTrue(
                all(
                    r.asset_type == 'equipment' and r.parent_id is None for r in records
                )
            )
        finally:
            MigrationExecutor(connection).migrate(latest)
