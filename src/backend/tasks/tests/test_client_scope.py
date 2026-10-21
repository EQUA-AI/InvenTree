"""Client scope for plant assets.

A machine is reachable through the client that owns this deployment; a sales
customer is a claim about a work order, never about the asset. These tests pin client resolution, shared customer/unattributed Work under
O24, and fail-closed machine-only Work when a machine has no client.
"""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client
from company.models import Company
from tasks.models import WorkOrder, WorkOrderType
from tasks.scope import (
    MaintenanceScope,
    ScopeError,
    require_work_order_scope,
    scope_for_actor,
    scope_for_work_order,
    work_order_scope_filter,
)


class ClientScopeResolutionTest(TestCase):
    """scope_for_work_order resolves customer or client."""

    def setUp(self):
        """Create a client, a customer, a client machine and an orphan."""
        suffix = uuid.uuid4().hex[:6]
        self.client_record = Client.objects.create(
            name=f'Northgate Water {suffix}', code=f'northgate-{suffix}'
        )
        self.customer = Company.objects.create(name=f'ACME {suffix}', is_customer=True)
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
        scope = scope_for_work_order(self._card(self.internal, customer=self.customer))

        self.assertEqual(scope.customer_id, self.customer.pk)
        self.assertIsNone(scope.client_id)

    def test_unattributed_work_has_empty_provenance_but_missing_work_is_refused(self):
        """An actual shared record must never be confused with a missing parent."""
        self.assertFalse(scope_for_work_order(self._card(None)).is_resolved)
        with self.assertRaises(ScopeError):
            scope_for_work_order(None)

    def test_machine_with_neither_identity_stays_unreachable(self):
        """A boundary is never guessed for an unscoped record."""
        with self.assertRaisesMessage(ScopeError, 'machine has no client'):
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


class WorkOrderScopeFilterTest(TestCase):
    """work_order_scope_filter is the exact set form of require_work_order_scope.

    The filter is what listing surfaces run before any per-record check gets a
    chance, so it must never be wider than the row-by-row gate. These tests pin
    the two selection rules (shared customer/unattributed Work for active viewers,
    machine-only orders on client machines for client grants), that site-keyed
    grants contribute no machine-only Work, and that the two authorization forms agree on
    every cell of a small matrix.
    """

    def setUp(self):
        """Two clients, two customers, and one work order per boundary shape."""
        suffix = uuid.uuid4().hex[:6]
        self.client_a = Client.objects.create(
            name=f'Client A {suffix}', code=f'client-a-{suffix}'
        )
        self.client_b = Client.objects.create(
            name=f'Client B {suffix}', code=f'client-b-{suffix}'
        )
        self.customer_x = Company.objects.create(
            name=f'Customer X {suffix}', is_customer=True
        )
        self.customer_y = Company.objects.create(
            name=f'Customer Y {suffix}', is_customer=True
        )
        self.machine_a = AssetMachine.objects.create(
            name=f'A press {suffix}', client=self.client_a
        )
        self.machine_b = AssetMachine.objects.create(
            name=f'B press {suffix}', client=self.client_b
        )
        self.machine_orphan = AssetMachine.objects.create(name=f'Orphan {suffix}')
        self.actor = get_user_model().objects.create_user(
            username=f'filter-actor-{suffix}',
            email=f'filter-{suffix}@example.com',
            password='pw',
        )

        group = Group.objects.create(name=f'filter-viewers-{suffix}')
        rule = group.rule_sets.get(name='work_order')
        rule.can_view = True
        rule.save()
        self.actor.groups.add(group)

        # One card per boundary shape the filter must distinguish.
        self.wo_customer_x = self._card(None, customer=self.customer_x)
        # Customer Work is shared even when it names a client machine.
        self.wo_customer_x_on_a = self._card(self.machine_a, customer=self.customer_x)
        self.wo_customer_y_on_a = self._card(self.machine_a, customer=self.customer_y)
        self.wo_client_a = self._card(self.machine_a)
        self.wo_client_b = self._card(self.machine_b)
        self.wo_orphan = self._card(self.machine_orphan)
        self.wo_unattributed = self._card(None)
        self.matrix = [
            self.wo_customer_x,
            self.wo_customer_x_on_a,
            self.wo_customer_y_on_a,
            self.wo_client_a,
            self.wo_client_b,
            self.wo_orphan,
            self.wo_unattributed,
        ]

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

    def _selected(self):
        """Every work order the filter surfaces for the actor, exactly."""
        return set(
            WorkOrder.objects.filter(work_order_scope_filter(self.actor)).values_list(
                'pk', flat=True
            )
        )

    def _customer_grant(self, customer, site_key=None):
        return MaintenanceScope(customer_id=customer.pk, site_key=site_key)

    def _client_grant(self, client, site_key=None):
        return MaintenanceScope(
            customer_id=None, site_key=site_key, client_id=client.pk
        )

    def test_customer_grant_selects_all_shared_work(self):
        """Any resolved scope reaches all customer and unattributed Work."""
        self.actor.maintenance_scopes = {self._customer_grant(self.customer_x)}

        self.assertEqual(
            self._selected(),
            {
                self.wo_customer_x.pk,
                self.wo_customer_x_on_a.pk,
                self.wo_customer_y_on_a.pk,
                self.wo_unattributed.pk,
            },
        )

    def test_client_grant_selects_shared_work_and_its_machine_only_work(self):
        """A client grant reaches shared Work as well as its own machine-only jobs."""
        self.actor.maintenance_scopes = {self._client_grant(self.client_a)}

        self.assertEqual(
            self._selected(),
            {
                self.wo_client_a.pk,
                self.wo_customer_x.pk,
                self.wo_customer_x_on_a.pk,
                self.wo_customer_y_on_a.pk,
                self.wo_unattributed.pk,
            },
        )

    def test_combined_grants_select_the_union(self):
        """Multiple grants widen the selection to exactly their union."""
        self.actor.maintenance_scopes = {
            self._customer_grant(self.customer_x),
            self._client_grant(self.client_a),
        }

        self.assertEqual(
            self._selected(),
            {
                self.wo_customer_x.pk,
                self.wo_customer_x_on_a.pk,
                self.wo_client_a.pk,
                self.wo_customer_y_on_a.pk,
                self.wo_unattributed.pk,
            },
        )

    def test_site_keyed_grants_contribute_no_machine_only_work(self):
        """Site-qualified scopes authorize shared Work but no machine-only rows.
        """
        self.actor.maintenance_scopes = {
            self._customer_grant(self.customer_x, site_key='plant-1'),
            self._client_grant(self.client_a, site_key='plant-1'),
        }

        self.assertEqual(
            self._selected(),
            {
                self.wo_customer_x.pk,
                self.wo_customer_x_on_a.pk,
                self.wo_customer_y_on_a.pk,
                self.wo_unattributed.pk,
            },
        )

    def test_filter_never_selects_what_the_row_check_denies(self):
        """Property: over the whole matrix, selected == allowed, cell by cell."""
        grant_sets = {
            'no grants': set(),
            'unresolved grant': {MaintenanceScope(None, None)},
            'ambiguous grant': {
                MaintenanceScope(self.customer_x.pk, None, self.client_a.pk)
            },
            'customer grant': {self._customer_grant(self.customer_x)},
            'client grant': {self._client_grant(self.client_a)},
            'both grants': {
                self._customer_grant(self.customer_x),
                self._client_grant(self.client_a),
            },
            'site-keyed grants': {
                self._customer_grant(self.customer_x, site_key='plant-1'),
                self._client_grant(self.client_a, site_key='plant-1'),
            },
        }

        for label, grants in grant_sets.items():
            self.actor.maintenance_scopes = grants
            selected = self._selected()

            for work_order in self.matrix:
                try:
                    require_work_order_scope(self.actor, work_order)
                    allowed = True
                except ScopeError:
                    allowed = False

                with self.subTest(grants=label, work_order=work_order.pk):
                    self.assertEqual(work_order.pk in selected, allowed)

    def test_viewer_without_grants_selects_shared_work_only(self):
        """Work view alone authorizes shared Work, never machine-only Work."""
        self.assertEqual(
            self._selected(),
            {
                self.wo_customer_x.pk,
                self.wo_customer_x_on_a.pk,
                self.wo_customer_y_on_a.pk,
                self.wo_unattributed.pk,
            },
        )

    def test_role_and_active_checks_agree_for_sets_and_records(self):
        """Independent scope providers cannot substitute for active Work view."""
        self.actor.maintenance_scopes = {self._client_grant(self.client_a)}
        self.actor.is_active = False
        for actor in (self.actor, get_user_model().objects.create_user('roleless')):
            with self.subTest(actor=actor.pk):
                with self.assertRaises(ScopeError):
                    work_order_scope_filter(actor)
                for work_order in self.matrix:
                    with self.assertRaises(ScopeError):
                        require_work_order_scope(actor, work_order)

    def test_unauthenticated_actor_raises(self):
        """The filter is not reachable anonymously."""
        with self.assertRaisesMessage(ScopeError, 'not authenticated'):
            work_order_scope_filter(None)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver'
)
class SingleSiteScopeResolverTest(TestCase):
    """The single-tenant resolver grants the internal client, fail-closed.

    An empty resolution makes ``scope_for_actor`` raise, so every refusal path
    (missing tenant row, inactive tenant, inactive user, missing role) is
    pinned as an error rather than as an implicit global scope.

    Migration ``assets.0009`` seeds ``Client(code='internal')``, so the happy
    paths use that row and the missing-row test deletes it explicitly.
    """

    def setUp(self):
        """A superuser, a plain user, and the seeded internal tenant."""
        suffix = uuid.uuid4().hex[:6]
        self.internal_client, _created = Client.objects.get_or_create(
            code='internal', defaults={'name': f'Internal {suffix}'}
        )
        self.superuser = get_user_model().objects.create_superuser(
            username=f'site-admin-{suffix}',
            email=f'admin-{suffix}@example.com',
            password='pw',
        )
        self.plain_user = get_user_model().objects.create_user(
            username=f'site-user-{suffix}',
            email=f'user-{suffix}@example.com',
            password='pw',
        )
        self.suffix = suffix

    def test_superuser_gets_the_internal_client_scope(self):
        """A superuser holds every role, so the tenant scope resolves."""
        scopes = scope_for_actor(self.superuser)

        self.assertEqual(
            scopes,
            {
                MaintenanceScope(
                    customer_id=None, site_key=None, client_id=self.internal_client.pk
                )
            },
        )

    def test_role_holding_user_gets_the_internal_client_scope(self):
        """The work_order view role is the whole requirement, not superuser."""
        group = Group.objects.create(name=f'operators-{self.suffix}')
        ruleset = group.rule_sets.get(name='work_order')
        ruleset.can_view = True
        ruleset.save()
        self.plain_user.groups.add(group)

        scopes = scope_for_actor(self.plain_user)

        self.assertEqual(
            {scope.client_id for scope in scopes}, {self.internal_client.pk}
        )

    def test_user_without_the_view_role_resolves_nothing(self):
        """No role means no scope, and no scope is an error, not everything."""
        with self.assertRaisesMessage(ScopeError, 'unresolved'):
            scope_for_actor(self.plain_user)

    def test_inactive_user_resolves_nothing(self):
        """Deactivation revokes the tenant scope even for a superuser."""
        self.superuser.is_active = False
        self.superuser.save()

        with self.assertRaisesMessage(ScopeError, 'unresolved'):
            scope_for_actor(self.superuser)

    def test_missing_client_row_resolves_nothing(self):
        """No tenant row means nobody is scoped, rather than everybody."""
        self.internal_client.delete()

        with self.assertRaisesMessage(ScopeError, 'unresolved'):
            scope_for_actor(self.superuser)

    def test_inactive_client_resolves_nothing(self):
        """A deactivated tenant stops granting, immediately."""
        self.internal_client.active = False
        self.internal_client.save()

        with self.assertRaisesMessage(ScopeError, 'unresolved'):
            scope_for_actor(self.superuser)

    @override_settings(AIMMS_SINGLE_SITE_CLIENT_CODE='plant-x')
    def test_client_code_override_is_respected(self):
        """The granted tenant is the configured one, not the default name."""
        override_client = Client.objects.create(
            name=f'Plant X {self.suffix}', code='plant-x'
        )

        scopes = scope_for_actor(self.superuser)

        self.assertEqual({scope.client_id for scope in scopes}, {override_client.pk})
