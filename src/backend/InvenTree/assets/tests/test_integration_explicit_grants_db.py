"""Tests for explicit current client grants on new source-reader boundaries."""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder
from tasks.scope import scoped_machines, scoped_work_orders

from assets.integration_sources import read_machine, read_maintenance_record
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from company.models import Company
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='explicit-policy-fallback',
)
class ExplicitClientGrantMachineTests(TestCase):
    """The integration must not turn native single-site fallback into a grant."""

    def test_fallback_machine_requires_an_explicit_current_grant(self):
        """Deny fallback after revocation, while leaving native scope unchanged."""
        actor = get_user_model().objects.create_user(username='explicit-policy-reader')
        group = Group.objects.create(name='Explicit policy readers')
        actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        explicit = Client.objects.create(
            name='Synthetic explicit', code='policy-explicit'
        )
        fallback = Client.objects.create(
            name='Synthetic fallback', code='explicit-policy-fallback'
        )
        grant = ClientScopeGrant.objects.create(user=actor, client=explicit)
        machine = AssetMachine.objects.create(
            name='Synthetic fallback machine', client=fallback
        )
        self.assertIsNone(
            read_machine(actor, environment='test', machine_pk=machine.pk)
        )

        grant.delete()

        self.assertTrue(scoped_machines(actor).filter(pk=machine.pk).exists())
        self.assertIsNone(
            read_machine(actor, environment='test', machine_pk=machine.pk)
        )

        ClientScopeGrant.objects.create(user=actor, client=fallback)
        self.assertIsNotNone(
            read_machine(actor, environment='test', machine_pk=machine.pk)
        )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='explicit-maintenance-fallback',
)
@assume_synthetic_attribution
class ExplicitClientGrantMaintenanceTests(TestCase):
    """Maintenance history also requires a current explicit client grant."""

    def test_fallback_maintenance_requires_an_explicit_current_grant(self):
        """Native fallback must not confer integration access to history."""
        actor = get_user_model().objects.create_user(username='explicit-maint-reader')
        group = Group.objects.create(name='Explicit maintenance readers')
        actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        explicit = Client.objects.create(
            name='Synthetic explicit', code='maint-explicit'
        )
        fallback = Client.objects.create(
            name='Synthetic fallback', code='explicit-maintenance-fallback'
        )
        grant = ClientScopeGrant.objects.create(user=actor, client=explicit)
        machine = AssetMachine.objects.create(
            name='Synthetic maint machine', client=fallback
        )
        record = AssetMaintenanceRecord.objects.create(
            machine=machine, date=date(2020, 1, 2), summary='Synthetic service'
        )
        self.assertIsNone(
            read_maintenance_record(actor, environment='test', record_pk=record.pk)
        )

        grant.delete()

        self.assertTrue(scoped_machines(actor).filter(pk=machine.pk).exists())
        self.assertIsNone(
            read_maintenance_record(actor, environment='test', record_pk=record.pk)
        )

        ClientScopeGrant.objects.create(user=actor, client=fallback)
        self.assertIsNotNone(
            read_maintenance_record(actor, environment='test', record_pk=record.pk)
        )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='explicit-work-fallback',
)
@assume_synthetic_attribution
class ExplicitClientGrantWorkTests(TestCase):
    """Apply explicit-grant policy without changing native shared Work."""

    def test_fallback_and_shared_work_require_current_explicit_grant(self):
        """Without any current grant, even natively shared Work is withheld."""
        actor = get_user_model().objects.create_user(username='explicit-work-reader')
        group = Group.objects.create(name='Explicit Work readers')
        actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        explicit = Client.objects.create(
            name='Synthetic explicit', code='work-explicit'
        )
        fallback = Client.objects.create(
            name='Synthetic fallback', code='explicit-work-fallback'
        )
        grant = ClientScopeGrant.objects.create(user=actor, client=explicit)
        machine = AssetMachine.objects.create(
            name='Synthetic Work machine', client=fallback
        )
        customer = Company.objects.create(name='Synthetic customer', is_customer=True)
        private = WorkOrder.objects.create(
            title='Synthetic private job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=machine,
        )
        shared = WorkOrder.objects.create(
            title='Synthetic shared job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=machine,
            customer=customer,
        )
        self.assertIsNone(
            read_work_order(actor, environment='test', work_pk=private.pk)
        )
        self.assertTrue(scoped_work_orders(actor).filter(pk=shared.pk).exists())
        self.assertIsNone(read_work_order(actor, environment='test', work_pk=shared.pk))

        grant.delete()

        self.assertEqual(
            set(scoped_work_orders(actor).values_list('pk', flat=True)),
            {private.pk, shared.pk},
        )
        self.assertIsNone(
            read_work_order(actor, environment='test', work_pk=private.pk)
        )
        self.assertIsNone(read_work_order(actor, environment='test', work_pk=shared.pk))

        ClientScopeGrant.objects.create(user=actor, client=fallback)
        self.assertIsNotNone(
            read_work_order(actor, environment='test', work_pk=private.pk)
        )
        self.assertIsNone(read_work_order(actor, environment='test', work_pk=shared.pk))
