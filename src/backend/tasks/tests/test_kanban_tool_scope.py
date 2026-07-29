"""Kanban AI read tools apply the maintenance scope, not a global read.

The kanban tools historically read ``WorkOrder.objects.all()`` behind a global
``work_order:view`` grant. Every read tool now starts from
``work_order_scope_filter`` keyed on the principal that the AI boundary put in
``ai.core.auth.principal_context``. These tests pin the fail-closed shape of
that path through the tools themselves:

* a client-granted actor sees exactly their client's cards, never a
  neighbour's;
* a foreign card and a missing card produce the *same* error, so existence is
  not disclosed across the boundary;
* no principal, a stale principal, or a grant that resolves to nothing all
  yield an empty board rather than the whole plant's.
"""

import uuid
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from asgiref.sync import async_to_sync
from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

from ai.core.auth import AIPrincipal, principal_context
from ai.core.integrations.kanban_tools import (
    check_kanban_card_stock,
    get_kanban_card,
    get_kanban_summary,
    list_kanban_cards,
)
from assets.models import AssetMachine, Client

# The tools reload the acting user from the database by primary key, so an
# in-memory ``maintenance_scopes`` attribute never reaches them. Grants are
# served the way production serves them: through the configured resolver.
_GRANTS: dict[str, set[MaintenanceScope]] = {}


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f'user:{user.pk}',
        actor=f'user:{user.pk}',
        user_pk=str(user.pk),
        username=user.get_username(),
        authentication_method='session',
        scope='chat',
        policy_version='test',
        is_staff=bool(user.is_staff),
        is_superuser=bool(user.is_superuser),
    )


@contextmanager
def _acting_as(principal: AIPrincipal | None):
    """Bind (or clear) the boundary principal for the duration of a call."""
    token = principal_context.set(principal)
    try:
        yield
    finally:
        principal_context.reset(token)


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=f'{__name__}._grant_resolver')
class KanbanToolScopeTest(TestCase):
    """The read tools start from the actor's scope, and only from it."""

    def setUp(self):
        """Two clients, one machine and one card each, an actor on client A."""
        suffix = uuid.uuid4().hex[:6]
        self.client_a = Client.objects.create(
            name=f'Client A {suffix}', code=f'client-a-{suffix}'
        )
        self.client_b = Client.objects.create(
            name=f'Client B {suffix}', code=f'client-b-{suffix}'
        )
        self.machine_a = AssetMachine.objects.create(
            name=f'A pump {suffix}', client=self.client_a
        )
        self.machine_b = AssetMachine.objects.create(
            name=f'B pump {suffix}', client=self.client_b
        )
        self.card_a = self._card(self.machine_a, title='Card A')
        self.card_b = self._card(self.machine_b, title='Card B')
        self.actor = get_user_model().objects.create_user(
            username=f'kanban-scoped-{suffix}',
            email=f'{suffix}@example.com',
            password='pw',
        )
        _GRANTS.clear()
        self.addCleanup(_GRANTS.clear)
        _GRANTS[self.actor.get_username()] = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_a.pk
            )
        }

    def _card(self, machine, **overrides):
        values = {
            'title': 'Work',
            'status': WorkOrder.STATUS_BACKLOG,
            'priority': WorkOrder.PRIORITY_MEDIUM,
            'machine': machine,
        }
        values.update(overrides)
        return WorkOrder.objects.create(**values)

    def _missing_id(self) -> int:
        return self.card_a.pk + self.card_b.pk + 1000

    def _call(self, tool, principal='actor', /, **kwargs):
        bound = self._principal() if principal == 'actor' else principal
        with _acting_as(bound):
            return async_to_sync(tool)(**kwargs)

    def _principal(self) -> AIPrincipal:
        return _principal(self.actor)

    # -- client grant reaches its own client's cards only --------------------

    def test_list_shows_only_the_actors_clients_cards(self):
        """The board is the actor's board, not the plant's."""
        result = self._call(list_kanban_cards)

        self.assertEqual(result['count'], 1)
        self.assertEqual([c['id'] for c in result['cards']], [self.card_a.pk])

    def test_summary_counts_only_the_actors_clients_cards(self):
        """Aggregates leak just as loudly as rows; they are scoped too."""
        result = self._call(get_kanban_summary)

        self.assertEqual(result['total_active'], 1)
        self.assertEqual(
            result['status_counts'], {WorkOrder.STATUS_BACKLOG: 1}
        )

    def test_get_card_returns_the_actors_own_card(self):
        """Scoping must not break the happy path."""
        result = self._call(get_kanban_card, work_order_id=self.card_a.pk)

        self.assertEqual(result.get('id'), self.card_a.pk)
        self.assertEqual(result.get('title'), 'Card A')

    def test_check_stock_reaches_the_actors_own_card(self):
        """The stock re-check works inside the boundary."""
        result = self._call(check_kanban_card_stock, work_order_id=self.card_a.pk)

        self.assertNotIn('error', result)
        self.assertEqual(result['card_id'], self.card_a.pk)

    # -- foreign and missing are indistinguishable ---------------------------

    def test_foreign_card_and_missing_card_give_the_same_error(self):
        """A denial must not confirm that the other tenant's card exists."""
        foreign = self._call(get_kanban_card, work_order_id=self.card_b.pk)
        missing = self._call(get_kanban_card, work_order_id=self._missing_id())

        self.assertIn('error', foreign)
        self.assertIn('error', missing)
        self.assertEqual(set(foreign), set(missing))
        self.assertEqual(
            foreign['error'].replace(str(self.card_b.pk), '<id>'),
            missing['error'].replace(str(self._missing_id()), '<id>'),
        )

    def test_check_stock_foreign_card_matches_the_missing_error(self):
        """The stock tool honours the same boundary with the same message."""
        foreign = self._call(check_kanban_card_stock, work_order_id=self.card_b.pk)
        missing = self._call(
            check_kanban_card_stock, work_order_id=self._missing_id()
        )

        self.assertIn('error', foreign)
        self.assertEqual(set(foreign), set(missing))
        self.assertEqual(
            foreign['error'].replace(str(self.card_b.pk), '<id>'),
            missing['error'].replace(str(self._missing_id()), '<id>'),
        )

    # -- absent or broken identity fails closed ------------------------------

    def test_no_principal_sees_an_empty_board(self):
        """Without a boundary identity there is nothing to show."""
        listed = self._call(list_kanban_cards, None)
        summary = self._call(get_kanban_summary, None)

        self.assertEqual(listed, {'count': 0, 'cards': []})
        self.assertEqual(summary['total_active'], 0)

    def test_no_principal_gets_the_not_found_error_for_a_real_card(self):
        """Unauthenticated reads are refused, not resolved."""
        fetched = self._call(get_kanban_card, None, work_order_id=self.card_a.pk)
        stock = self._call(
            check_kanban_card_stock, None, work_order_id=self.card_a.pk
        )

        self.assertIn('error', fetched)
        self.assertIn('error', stock)

    def test_principal_for_a_deleted_user_sees_nothing(self):
        """A principal whose user row is gone authorizes nothing."""
        stale = self._principal()
        self.actor.delete()

        result = self._call(list_kanban_cards, stale)

        self.assertEqual(result, {'count': 0, 'cards': []})

    def test_ungranted_actor_sees_an_empty_board_not_everything(self):
        """A resolver returning no scopes is a denial, not a wildcard."""
        _GRANTS[self.actor.get_username()] = set()

        listed = self._call(list_kanban_cards)
        fetched = self._call(get_kanban_card, work_order_id=self.card_a.pk)

        self.assertEqual(listed, {'count': 0, 'cards': []})
        self.assertIn('error', fetched)

    def test_site_qualified_grant_contributes_no_rows(self):
        """A site-keyed grant never widens the board.

        ``work_order_scope_filter`` skips site-qualified scopes because a
        resolved work-order scope never carries a site key; the base predicate
        selects nothing. The tool must surface that as an empty board.
        """
        _GRANTS[self.actor.get_username()] = {
            MaintenanceScope(
                customer_id=None, site_key='plant-x', client_id=self.client_b.pk
            )
        }

        result = self._call(list_kanban_cards)

        self.assertEqual(result, {'count': 0, 'cards': []})
