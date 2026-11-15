"""Verify the integration intersects native scope and explicit active grants."""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope, scoped_machines

from assets.integration_sources import read_machine, read_maintenance_record
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from users.models import RuleSet


@assume_synthetic_attribution
class ExplicitClientGrantIntersectionTests(TestCase):
    """A custom native scope and a different client grant do not combine."""

    def setUp(self):
        """Create an actor with client A grant and records belonging to B."""
        self.actor = get_user_model().objects.create_user(
            username='intersection-reader'
        )
        group = Group.objects.create(name='Intersection readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.client_a = Client.objects.create(name='Synthetic A', code='intersection-a')
        self.client_b = Client.objects.create(name='Synthetic B', code='intersection-b')
        ClientScopeGrant.objects.create(user=self.actor, client=self.client_a)
        self.machine = AssetMachine.objects.create(
            name='Synthetic B machine', client=self.client_b
        )
        self.record = AssetMaintenanceRecord.objects.create(
            machine=self.machine, date=date(2020, 1, 2), summary='Synthetic service'
        )
        self.work = WorkOrder.objects.create(
            title='Synthetic B job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=self.machine,
        )

    def _results(self):
        return (
            read_machine(self.actor, environment='test', machine_pk=self.machine.pk),
            read_maintenance_record(
                self.actor, environment='test', record_pk=self.record.pk
            ),
            read_work_order(self.actor, environment='test', work_pk=self.work.pk),
        )

    def test_custom_scope_requires_matching_explicit_grant(self):
        """A native B scope and an unrelated A grant must not authorize B."""
        scope = MaintenanceScope(
            customer_id=None, site_key=None, client_id=self.client_b.pk
        )
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: {scope}):
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assertEqual(self._results(), (None, None, None))
            grant_b = ClientScopeGrant.objects.create(
                user=self.actor, client=self.client_b
            )
            self.assertTrue(all(result is not None for result in self._results()))
            grant_b.delete()
            self.assertTrue(
                ClientScopeGrant.objects.filter(
                    user=self.actor, client=self.client_a
                ).exists()
            )
            self.assertEqual(self._results(), (None, None, None))

    def test_inactive_granted_client_is_withheld_even_with_custom_scope(self):
        """An inactive client does not become authorized via a stale grant."""
        scope = MaintenanceScope(
            customer_id=None, site_key=None, client_id=self.client_b.pk
        )
        ClientScopeGrant.objects.create(user=self.actor, client=self.client_b)
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: {scope}):
            self.assertTrue(all(result is not None for result in self._results()))
            self.client_b.active = False
            self.client_b.save(update_fields=['active'])
            self.assertTrue(
                scoped_machines(self.actor).filter(pk=self.machine.pk).exists()
            )
            self.assertEqual(self._results(), (None, None, None))
            self.client_b.active = True
            self.client_b.save(update_fields=['active'])
            self.assertTrue(all(result is not None for result in self._results()))

    def test_superuser_also_requires_explicit_matching_grant(self):
        """Superuser role status does not replace an integration client grant."""
        admin = get_user_model().objects.create_user(
            username='synthetic-superuser', is_superuser=True
        )
        scope = MaintenanceScope(
            customer_id=None, site_key=None, client_id=self.client_b.pk
        )
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: {scope}):
            self.assertTrue(scoped_machines(admin).filter(pk=self.machine.pk).exists())
            self.assertIsNone(
                read_machine(admin, environment='test', machine_pk=self.machine.pk)
            )
            self.assertIsNone(
                read_maintenance_record(
                    admin, environment='test', record_pk=self.record.pk
                )
            )
            self.assertIsNone(
                read_work_order(admin, environment='test', work_pk=self.work.pk)
            )
            ClientScopeGrant.objects.create(user=admin, client=self.client_b)
            self.assertIsNotNone(
                read_machine(admin, environment='test', machine_pk=self.machine.pk)
            )
            self.assertIsNotNone(
                read_maintenance_record(
                    admin, environment='test', record_pk=self.record.pk
                )
            )
            self.assertIsNotNone(
                read_work_order(admin, environment='test', work_pk=self.work.pk)
            )
