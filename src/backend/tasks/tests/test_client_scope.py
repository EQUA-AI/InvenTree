"""Client scope for plant assets.

A machine is reachable through the client that owns this deployment; a sales
customer is a claim about a work order, never about the asset. These tests pin
that client resolution works, that an explicit work-order customer still wins
over the asset's client, and that a machine without a client remains
unreachable rather than falling open.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import WorkOrder, WorkOrderType
from tasks.scope import (
    MaintenanceScope,
    ScopeError,
    require_work_order_scope,
    scope_for_actor,
    scope_for_work_order,
)

from assets.models import AssetMachine, Client
from company.models import Company


class ClientScopeResolutionTest(TestCase):
    """scope_for_work_order resolves customer or client."""

    def setUp(self):
        """Create a client, a customer, a client machine and an orphan."""
        suffix = uuid.uuid4().hex[:6]
        self.client_record = Client.objects.create(
            name=f'Northgate Water {suffix}', code=f'northgate-{suffix}'
        )
        self.customer = Company.objects.create(
            name=f'ACME {suffix}', is_customer=True
        )
        self.internal = AssetMachine.objects.create(
            name=f'Internal pump {suffix}', client=self.client_record
        )
        self.unscoped = AssetMachine.objects.create(name=f'Orphan {suffix}')

    def _card(self, machine, **overrides):
        values = {
            'title': 'Work',
            'status': WorkOrder.STATUS_BACKLOG,
            'priority': WorkOrder.PRIORITY_MEDIUM,
            'machine': machine,
            'work_order_type': WorkOrderType.CORRECTIVE,
        }
        values.update(overrides)
        return WorkOrder.objects.create(**values)

    def test_internal_asset_resolves_to_its_client(self):
        """An asset nobody bought is still reachable."""
        scope = scope_for_work_order(self._card(self.internal))

        self.assertEqual(scope.client_id, self.client_record.pk)
        self.assertIsNone(scope.customer_id)
        self.assertTrue(scope.is_resolved)

    def test_explicit_work_order_customer_resolves_to_that_customer(self):
        """A work order that names a customer is that customer's job."""
        scope = scope_for_work_order(self._card(None, customer=self.customer))

        self.assertEqual(scope.customer_id, self.customer.pk)
        self.assertIsNone(scope.client_id)

    def test_explicit_work_order_customer_wins_over_the_machine_client(self):
        """The explicit sales claim beats the asset's client.

        Pinned deliberately: this is the one exception to client-first, and a
        later "simplification" must not silently flip it.
        """
        scope = scope_for_work_order(
            self._card(self.internal, customer=self.customer)
        )

        self.assertEqual(scope.customer_id, self.customer.pk)
        self.assertIsNone(scope.client_id)

    def test_machine_with_neither_identity_stays_unreachable(self):
        """A boundary is never guessed for an unscoped record."""
        with self.assertRaisesMessage(ScopeError, 'neither a customer nor a client'):
            scope_for_work_order(self._card(self.unscoped))


class ClientScopeAuthorizationTest(TestCase):
    """An actor granted a client scope reaches that client's assets only."""

    def setUp(self):
        """Create two clients and an actor scoped to one of them."""
        suffix = uuid.uuid4().hex[:6]
        self.client_a = Client.objects.create(
            name=f'Client A {suffix}', code=f'client-a-{suffix}'
        )
        self.client_b = Client.objects.create(
            name=f'Client B {suffix}', code=f'client-b-{suffix}'
        )
        self.actor = get_user_model().objects.create_superuser(
            username=f'client-scoped-{suffix}',
            email=f'{suffix}@example.com',
            password='pw',
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_a.pk
            )
        }
        self.machine_a = AssetMachine.objects.create(
            name=f'A pump {suffix}', client=self.client_a
        )
        self.machine_b = AssetMachine.objects.create(
            name=f'B pump {suffix}', client=self.client_b
        )

    def _card(self, machine):
        return WorkOrder.objects.create(
            title='Work',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=machine,
        )

    def test_client_only_scope_is_accepted_by_the_resolver(self):
        """A scope naming a client but no customer is now valid."""
        scopes = scope_for_actor(self.actor)

        self.assertEqual(len(scopes), 1)
        self.assertTrue(next(iter(scopes)).is_resolved)

    def test_actor_reaches_their_own_client_asset(self):
        """The gap this closes: internal assets are usable again."""
        scope = require_work_order_scope(self.actor, self._card(self.machine_a))

        self.assertEqual(scope.client_id, self.client_a.pk)

    def test_actor_cannot_reach_another_clients_asset(self):
        """Client scope is a boundary, not a label."""
        with self.assertRaises(ScopeError):
            require_work_order_scope(self.actor, self._card(self.machine_b))

    def test_an_empty_scope_still_authorizes_nothing(self):
        """A scope naming neither identity is refused, not treated as global."""
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key='plant-a', client_id=None)
        }

        with self.assertRaisesMessage(ScopeError, 'unresolved'):
            scope_for_actor(self.actor)
