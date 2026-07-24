"""Tests for the parts-reconcile PUT endpoint (S4, fixes doc bugs 6.2/6.3).

The client used to save a card's parts with a per-part POST loop that had two
data-loss bugs: it never deleted a removed part, and re-POSTing an existing part
hit the ``unique_together`` constraint and 500ed (swallowed), so quantity edits
were dropped. The reconcile PUT replaces that loop: the body is the desired full
set, applied atomically.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from part.models import Part
from stock.models import StockItem
from tasks.models import KanbanCard, KanbanCardPart


class KanbanPartsReconcileTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='parts-sup', email='p@example.com', password='pw'
        )
        self.client.force_login(self.user)

        self.card = KanbanCard.objects.create(
            title='Parts WO', status='backlog', priority='low'
        )
        self.bearing = Part.objects.create(
            name='Bearing', description='b', component=True
        )
        self.seal = Part.objects.create(
            name='Seal', description='s', component=True
        )
        self.belt = Part.objects.create(
            name='Belt', description='belt', component=True
        )
        StockItem.objects.create(part=self.bearing, quantity=Decimal('100'))
        StockItem.objects.create(part=self.seal, quantity=Decimal('100'))
        StockItem.objects.create(part=self.belt, quantity=Decimal('100'))

        self.url = reverse('kanban-card-part-list', kwargs={'card_pk': self.card.pk})

    def _put(self, parts):
        return self.client.put(self.url, data=parts, content_type='application/json')

    def _quantities(self):
        return {
            cp.part_id: cp.quantity
            for cp in KanbanCardPart.objects.filter(card=self.card)
        }

    def test_reconcile_creates_parts_from_empty(self):
        response = self._put([
            {'part': self.bearing.pk, 'quantity': 2},
            {'part': self.seal.pk, 'quantity': 1},
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._quantities(),
            {self.bearing.pk: Decimal('2'), self.seal.pk: Decimal('1')},
        )

    def test_reconcile_updates_an_existing_quantity(self):
        """Doc bug 6.3: this used to be silently dropped."""
        KanbanCardPart.objects.create(
            card=self.card, part=self.bearing, quantity=Decimal('2')
        )

        response = self._put([{'part': self.bearing.pk, 'quantity': 5}])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._quantities(), {self.bearing.pk: Decimal('5')})

    def test_reconcile_deletes_a_removed_part(self):
        """Doc bug 6.2: removed parts were never deleted server-side."""
        KanbanCardPart.objects.create(
            card=self.card, part=self.bearing, quantity=Decimal('2')
        )
        KanbanCardPart.objects.create(
            card=self.card, part=self.seal, quantity=Decimal('1')
        )

        # Desired set omits the seal.
        response = self._put([{'part': self.bearing.pk, 'quantity': 2}])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(self._quantities()), [self.bearing.pk])

    def test_reconcile_does_all_three_at_once(self):
        KanbanCardPart.objects.create(
            card=self.card, part=self.bearing, quantity=Decimal('2')
        )
        KanbanCardPart.objects.create(
            card=self.card, part=self.seal, quantity=Decimal('1')
        )

        # keep+change bearing, delete seal, add belt.
        response = self._put([
            {'part': self.bearing.pk, 'quantity': 4},
            {'part': self.belt.pk, 'quantity': 3},
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._quantities(),
            {self.bearing.pk: Decimal('4'), self.belt.pk: Decimal('3')},
        )

    def test_reconcile_to_empty_clears_all_parts(self):
        KanbanCardPart.objects.create(
            card=self.card, part=self.bearing, quantity=Decimal('2')
        )

        response = self._put([])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(KanbanCardPart.objects.filter(card=self.card).count(), 0)

    def test_response_reports_allocation(self):
        response = self._put([{'part': self.bearing.pk, 'quantity': 2}])

        body = response.json()
        self.assertIn('parts', body)
        self.assertIn('warnings', body)
        self.assertTrue(body['all_allocated'])
        self.assertEqual(body['parts'][0]['allocation_status'], 'full')

    def test_insufficient_stock_produces_a_warning(self):
        low = Part.objects.create(name='Rare', description='r', component=True)
        StockItem.objects.create(part=low, quantity=Decimal('1'))

        response = self._put([{'part': low.pk, 'quantity': 10}])

        body = response.json()
        self.assertFalse(body['all_allocated'])
        self.assertEqual(len(body['warnings']), 1)

    def test_duplicate_part_is_rejected(self):
        response = self._put([
            {'part': self.bearing.pk, 'quantity': 2},
            {'part': self.bearing.pk, 'quantity': 3},
        ])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(KanbanCardPart.objects.filter(card=self.card).count(), 0)

    def test_unknown_part_is_rejected(self):
        response = self._put([{'part': 999999, 'quantity': 1}])

        self.assertEqual(response.status_code, 400)

    def test_non_list_payload_is_rejected(self):
        response = self.client.put(
            self.url,
            data={'part': self.bearing.pk, 'quantity': 1},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_reconcile_is_atomic_on_a_bad_item(self):
        """A validation failure must leave the existing set untouched."""
        KanbanCardPart.objects.create(
            card=self.card, part=self.bearing, quantity=Decimal('2')
        )

        # Second item is invalid; the whole request must roll back / not apply.
        response = self._put([
            {'part': self.seal.pk, 'quantity': 1},
            {'part': 999999, 'quantity': 1},
        ])

        self.assertEqual(response.status_code, 400)
        # The pre-existing bearing is intact and no seal was added.
        self.assertEqual(self._quantities(), {self.bearing.pk: Decimal('2')})

    def test_reconcile_requires_change_permission(self):
        from django.contrib.auth.models import Group

        from users.models import RuleSet
        from users.ruleset import RuleSetEnum

        group = Group.objects.create(name='parts-viewers')
        viewer = get_user_model().objects.create_user(
            username='parts-viewer', email='pv@example.com', password='pw'
        )
        viewer.groups.add(group)
        ruleset = RuleSet.objects.get(group=group, name=RuleSetEnum.WORK_ORDER)
        ruleset.can_view = True
        ruleset.can_change = False
        ruleset.save()

        self.client.force_login(viewer)
        response = self._put([{'part': self.bearing.pk, 'quantity': 1}])

        self.assertEqual(response.status_code, 403)


class KanbanAllocatePartsStillWorksTest(TestCase):
    """The allocate endpoint was refactored onto the shared helper; pin it."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='alloc-sup', email='a@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.card = KanbanCard.objects.create(
            title='Alloc WO', status='backlog', priority='low'
        )
        self.part = Part.objects.create(name='P', description='p', component=True)
        StockItem.objects.create(part=self.part, quantity=Decimal('50'))
        KanbanCardPart.objects.create(
            card=self.card, part=self.part, quantity=Decimal('5')
        )

    def test_allocate_reports_full_allocation(self):
        url = reverse('kanban-card-allocate', kwargs={'card_pk': self.card.pk})

        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['all_allocated'])
        self.assertEqual(body['parts'][0]['allocation_status'], 'full')
