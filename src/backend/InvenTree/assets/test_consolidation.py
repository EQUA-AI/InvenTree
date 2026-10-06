"""The consolidated application exposes only the supported runtime surface."""

from datetime import timedelta
from importlib.util import find_spec
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import get_commands
from django.db import transaction
from django.db.migrations.loader import MigrationLoader
from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.test import SimpleTestCase, TestCase
from django.test.utils import isolate_apps
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from tasks.models import WorkOrder

from assets.models import AssetMachine, Client, HealthSource, MachineSignalBinding


class RetiredRuntimeTests(SimpleTestCase):
    """Retired synthetic tooling cannot be re-enabled in a deployment."""

    def test_synthetic_commands_are_not_registered(self):
        """Removed loaders and lifecycle commands cannot be invoked."""
        retired = {
            'apply_demo_metrics',
            'cleanup_demo_metrics',
            'plan_demo_metrics',
            'replay_demo_metrics',
            'stop_demo_metrics',
            'verify_demo_metrics',
            'load_asset_demo_data',
            'load_asset_location_demo',
            'load_water_workflow_demo_data',
        }
        self.assertFalse(retired.intersection(get_commands()))

    def test_runtime_has_no_synthetic_ledger_models(self):
        """Only supported models are registered with the application."""
        self.assertFalse(
            any(model.__name__.startswith('DemoMetrics') for model in apps.get_models())
        )

    def test_removed_mode_switch_cannot_claim_static_isolation(self):
        """There is no CLI that can announce a nonexistent isolated provider."""
        self.assertIsNone(find_spec('ai.core.switch_mode'))

    def test_synthetic_routes_are_not_registered(self):
        """Native routes remain available without the retired session API."""
        with self.assertRaises(NoReverseMatch):
            reverse('asset-demo-session-list')
        self.assertTrue(reverse('asset-location-machines'))
        self.assertTrue(reverse('asset-machine-list'))

    def test_migration_state_matches_retired_runtime(self):
        """The migration leaf agrees with registered runtime models."""
        state = MigrationLoader(None).project_state()
        self.assertFalse(
            any(
                app == 'assets' and model.startswith('demometrics')
                for app, model in state.models
            )
        )


class RetainedReferenceDeletionTests(TestCase):
    """Historical rows retain their deletion policies without public models."""

    def setUp(self):
        """Use isolated historical metadata to create pre-retirement rows."""
        self.history = (
            MigrationLoader(None)
            .project_state([('assets', '0019_merge_iot_registry_demometrics')])
            .apps
        )
        self.session_model = self.history.get_model('assets', 'DemoMetricsSession')
        self.owner = get_user_model().objects.create(username='retained-owner-test')
        self.session = self.session_model.objects.create(
            dataset_key='retained-deletion-test',
            session_key='retained-deletion-test',
            demo_owner_id=self.owner.pk,
            anchor_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=1),
        )
        tenant = Client.objects.create(
            name='Retained reference test', code='retained-ref'
        )
        self.machine = AssetMachine.objects.create(
            name='Retained machine', client=tenant
        )

    def test_owner_deletion_preserves_session_and_nulls_reference(self):
        """Removing an owner leaves the historical session intact."""
        with transaction.atomic():
            self.owner.delete()
            self.session.refresh_from_db()
            self.assertIsNone(self.session.demo_owner_id)
        self.assertTrue(self.session_model.objects.filter(pk=self.session.pk).exists())

    def test_bulk_owner_deletion_preserves_session_and_nulls_reference(self):
        """QuerySet deletion preserves the same historical SET_NULL policy."""
        with transaction.atomic():
            get_user_model().objects.filter(pk=self.owner.pk).delete()
            self.session.refresh_from_db()
            self.assertIsNone(self.session.demo_owner_id)

    def test_machine_protection_is_reported_before_foreign_key_failure(self):
        """A preserved membership still raises the original ProtectedError."""
        membership_model = self.history.get_model('assets', 'DemoMetricsMachine')
        membership = membership_model.objects.create(
            session_id=self.session.pk,
            machine_id=self.machine.pk,
            alias='retained-machine',
        )
        with transaction.atomic(), self.assertRaises(ProtectedError):
            self.machine.delete()
        self.assertTrue(AssetMachine.objects.filter(pk=self.machine.pk).exists())
        self.assertTrue(membership_model.objects.filter(pk=membership.pk).exists())

    def test_unreferenced_machine_is_still_deletable(self):
        """The compatibility guard does not prohibit normal native deletion."""
        pk = self.machine.pk
        self.machine.delete()
        self.assertFalse(AssetMachine.objects.filter(pk=pk).exists())

    def test_other_native_parents_keep_their_protection(self):
        """Source, binding, and work-order ledger references remain protected."""
        source = HealthSource.objects.create(
            name='Retained offline source', active=False
        )
        binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key='retained/deletion',
            display_name='Retained pressure',
            signal_kind='pressure',
            unit='Pa',
        )
        work_order = WorkOrder.objects.create(
            title='Retained work order',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
        )
        object_model = self.history.get_model('assets', 'DemoMetricsObject')
        for field, target in (
            ('binding', binding),
            ('source', source),
            ('work_order', work_order),
        ):
            with self.subTest(parent=field):
                reference = object_model.objects.create(
                    session_id=self.session.pk,
                    kind=field,
                    fixture_key=f'retained-{field}',
                    **{f'{field}_id': target.pk},
                )
                with transaction.atomic(), self.assertRaises(ProtectedError):
                    target.delete()
                self.assertTrue(type(target).objects.filter(pk=target.pk).exists())
                self.assertTrue(object_model.objects.filter(pk=reference.pk).exists())

    def test_bulk_machine_deletion_keeps_protection(self):
        """The historical reference also prevents QuerySet machine deletion."""
        membership_model = self.history.get_model('assets', 'DemoMetricsMachine')
        membership_model.objects.create(
            session_id=self.session.pk,
            machine_id=self.machine.pk,
            alias='retained-bulk',
        )
        with transaction.atomic(), self.assertRaises(ProtectedError):
            AssetMachine.objects.filter(pk=self.machine.pk).delete()
        self.assertTrue(AssetMachine.objects.filter(pk=self.machine.pk).exists())

    @isolate_apps('django.contrib.auth')
    def test_owner_proxy_queryset_keeps_nulling_policy(self):
        """An isolated live proxy must not bypass the retained owner relation."""
        from InvenTree.retired_relations import bind_retired_relations

        class OwnerProxy(get_user_model()):
            """Test-only proxy sharing the concrete user table."""

            class Meta:
                """Keep test registration outside the global app registry."""

                proxy = True
                app_label = 'auth'

        live_models = [*apps.get_models(), OwnerProxy]
        with patch(
            'InvenTree.retired_relations.apps.get_models', return_value=live_models
        ):
            bind_retired_relations()
        try:
            with transaction.atomic():
                OwnerProxy.objects.filter(pk=self.owner.pk).delete()
                self.session.refresh_from_db()
                self.assertIsNone(self.session.demo_owner_id)
        finally:
            pre_delete.disconnect(
                sender=OwnerProxy,
                dispatch_uid=f'InvenTree.retired_relations.{OwnerProxy._meta.label_lower}',
            )

    @isolate_apps('assets')
    def test_machine_proxy_queryset_keeps_protection(self):
        """Proxy querysets must still run historical protection preflight."""
        from InvenTree.retired_relations import bind_retired_relations

        class MachineProxy(AssetMachine):
            """Test-only proxy sharing the concrete machine table."""

            class Meta:
                """Keep test registration outside the global app registry."""

                proxy = True
                app_label = 'assets'

        self.history.get_model('assets', 'DemoMetricsMachine').objects.create(
            session_id=self.session.pk,
            machine_id=self.machine.pk,
            alias='retained-proxy',
        )
        live_models = [*apps.get_models(), MachineProxy]
        with patch(
            'InvenTree.retired_relations.apps.get_models', return_value=live_models
        ):
            bind_retired_relations()
        try:
            with transaction.atomic(), self.assertRaises(ProtectedError):
                MachineProxy.objects.filter(pk=self.machine.pk).delete()
        finally:
            pre_delete.disconnect(
                sender=MachineProxy,
                dispatch_uid=f'InvenTree.retired_relations.{MachineProxy._meta.label_lower}',
            )
