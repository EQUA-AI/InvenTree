"""Client-attribution tests for the unwired integration Work source."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder
from tasks.scope import scoped_work_orders

from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-attribution-fallback',
)
class SharedWorkAttributionTests(TestCase):
    """Native shared Work does not by itself establish integration entitlement."""

    def setUp(self):
        """Create an active reader with a grant for A, and A/B machines."""
        self.actor = get_user_model().objects.create_user(username='attribution-reader')
        group = Group.objects.create(name='Attribution Work readers')
        self.actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.client_a = Client.objects.create(name='Synthetic A', code='attribution-a')
        self.client_b = Client.objects.create(name='Synthetic B', code='attribution-b')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.client_a
        )
        self.machine_a = AssetMachine.objects.create(
            name='Synthetic A machine', client=self.client_a
        )
        self.machine_b = AssetMachine.objects.create(
            name='Synthetic B machine', client=self.client_b
        )
        self.customer = Company.objects.create(
            name='Synthetic customer', is_customer=True
        )

    def _work(self, machine, *, customer):
        return WorkOrder.objects.create(
            title='Synthetic shared Work',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=machine,
            customer=customer,
        )

    def test_shared_work_is_withheld_without_verified_attribution(self):
        """Native sharing persists; neither machine's grant proves attribution."""
        own = self._work(self.machine_a, customer=self.customer)
        foreign = self._work(self.machine_b, customer=self.customer)
        self.assertEqual(
            set(scoped_work_orders(self.actor).values_list('pk', flat=True)),
            {own.pk, foreign.pk},
        )
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=own.pk)
        )
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=foreign.pk)
        )

        self.grant.delete()
        ClientScopeGrant.objects.create(user=self.actor, client=self.client_b)
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=own.pk)
        )
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=foreign.pk)
        )
        self.assertEqual(
            set(scoped_work_orders(self.actor).values_list('pk', flat=True)),
            {own.pk, foreign.pk},
        )

    def test_work_without_machine_is_withheld_without_client_attribution(self):
        """An un-attributed shared record cannot inherit an arbitrary grant."""
        customer_work = self._work(None, customer=self.customer)
        unattributed = self._work(None, customer=None)
        self.assertEqual(
            set(scoped_work_orders(self.actor).values_list('pk', flat=True)),
            {customer_work.pk, unattributed.pk},
        )
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=customer_work.pk)
        )
        self.assertIsNone(
            read_work_order(self.actor, environment='test', work_pk=unattributed.pk)
        )
