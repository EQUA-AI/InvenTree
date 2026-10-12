"""Migration tests for the client backfill, customer column and unit registry."""

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, tag

from common.models import CustomUnit
from InvenTree.conversion import convert_physical_value, reload_unit_registry

from InvenTree.migration_rewind import migrate_for_rehearsal


class RestoringMigrationTestCase(TransactionTestCase):
    """Keep merged-history rehearsals isolated from the next test's schema."""

    def setUp(self):
        """Restore all leaves before Django flushes the current-model tables."""
        super().setUp()
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))


@tag('migration_test')
class ClientBackfillMigrationTests(RestoringMigrationTestCase):
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
        migrate_for_rehearsal(connection, self.migrate_from)
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
        migrate_for_rehearsal(connection, self.migrate_from)

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
class ProfileFieldMigrationTests(RestoringMigrationTestCase):
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
        migrate_for_rehearsal(connection, self.migrate_from)
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
        migrate_for_rehearsal(connection, self.migrate_from)
        self.assertNotIn('profile', self._machine_columns())

        MigrationExecutor(connection).migrate(self.migrate_to)


@tag('migration_test')
class BarcodeFieldMigrationTests(RestoringMigrationTestCase):
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
        migrate_for_rehearsal(connection, self.migrate_from)
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
        migrate_for_rehearsal(connection, self.migrate_from)
        self.assertNotIn('barcode_data', self._machine_columns())

        MigrationExecutor(connection).migrate(self.migrate_to)
class CusecUnitMigrationTests(TestCase):
    """The estate must be rebuildable from the repository alone."""

    def test_the_cusec_resolves_without_anyone_creating_it_by_hand(self):
        """Thirty approved discharge points are stored in a unit Pint lacks.

        This asserts against a database built only by migrations. Before 0015
        the unit existed solely as a row somebody had typed into one deployment,
        so a fresh database could not convert it and re-applying the checked-in
        review packs failed at every discharge point.

        The reload is load-bearing. The registry is built once at startup and
        cached in a module global, and at that point the process is still
        pointed at the developer's own database - which has the hand-made row.
        Without forcing a rebuild this test reads that registry and passes on
        any machine where someone has already created the unit by hand, which
        is precisely the state it is meant to detect.
        """
        reload_unit_registry()

        self.assertEqual(
            float(convert_physical_value('2931 cusec', 'm**3/s')), 82.99667736115198
        )

    def test_the_definition_is_the_one_the_approvals_assume(self):
        """A cusec is a cubic foot per second; nothing else reproduces 82.997."""
        unit = CustomUnit.objects.get(name='cusec')

        self.assertEqual(unit.definition, 'foot**3/second')


class VarUnitMigrationTests(TestCase):
    """Reactive power has a unit of its own on a database built by migrations."""

    def setUp(self):
        """Rebuild the registry from this database, for the reason in the cusec test."""
        super().setUp()
        reload_unit_registry()

    def test_both_spellings_resolve_with_si_prefixes(self):
        """The plant quotes MVAR; IEC writes Mvar, switchgear writes MVAr."""
        for text, kvar in (
            ('1 Mvar', 1000),
            ('1 MVAr', 1000),
            ('250 kvar', 250),
            ('250 kVAr', 250),
            ('500 var', 0.5),
        ):
            with self.subTest(text=text):
                self.assertAlmostEqual(
                    float(convert_physical_value(text, 'kvar')), kvar
                )

    def test_the_spellings_are_one_quantity(self):
        """Two rows, one unit: a reading in one converts exactly to the other."""
        self.assertAlmostEqual(float(convert_physical_value('3.2 MVAr', 'Mvar')), 3.2)

    def test_ambiguous_case_variants_stay_undefined(self):
        """MVar and MVAR are not guessed at; mvar would already mean milli-var."""
        for text in ('1 MVar', '1 MVAR'):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    convert_physical_value(text, 'kvar')

    def test_the_definition_is_a_volt_ampere(self):
        """Both rows stand alone, so load order cannot drop one of them."""
        for name in ('volt_ampere_reactive', 'VAr'):
            with self.subTest(name=name):
                self.assertEqual(
                    CustomUnit.objects.get(name=name).definition, 'volt * ampere'
                )
