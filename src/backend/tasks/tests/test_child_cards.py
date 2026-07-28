"""Board-card composition: subtasks, procurement, and closeout blocking."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from company.models import Company
from part.models import Part
from stock.models import StockItem
from tasks.models import KanbanCard, KanbanColumn, WorkOrder, WorkOrderPart
from tasks.services import scheduling


class CreateChildTest(TestCase):
    """A child is a board card on one job, never another work order."""

    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='child-actor', email='c@example.com', password='pw'
        )
        self.customer = Company.objects.create(name='Child Cust', is_customer=True)
        self.machine = AssetMachine.objects.create(
            name='Child Press', customer=self.customer
        )
        self.parent = WorkOrder.objects.create(
            title='Parent WO',
            status='backlog',
            priority='high',
            machine=self.machine,
            customer=self.customer,
        )

    def _create_child(self, **kwargs):
        return scheduling.create_child(
            parent_id=self.parent.pk,
            actor=self.actor,
            idempotency_key=kwargs.pop('idempotency_key', 'ch1'),
            title=kwargs.pop('title', 'Child'),
            **kwargs,
        )

    def test_child_belongs_to_the_existing_work_order(self):
        before = WorkOrder.objects.count()
        result = self._create_child()
        child = KanbanCard.objects.get(pk=result.metadata['card_id'])

        self.assertEqual(WorkOrder.objects.count(), before)
        self.assertEqual(child.work_order_id, self.parent.pk)
        self.assertEqual(child.card_kind, KanbanCard.KIND_SUBTASK)
        self.assertEqual(child.work_order.machine_id, self.machine.pk)
        self.assertEqual(child.work_order.customer_id, self.customer.pk)

    def test_unknown_parent_is_rejected(self):
        with self.assertRaises(scheduling.UnknownWorkOrder):
            scheduling.create_child(
                parent_id=999999,
                actor=self.actor,
                idempotency_key='ch3',
                title='Orphan',
            )

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(scheduling.InvalidChild):
            self._create_child(card_kind='nonsense')

    def test_create_child_is_idempotent(self):
        first = self._create_child(idempotency_key='ch4')
        second = self._create_child(idempotency_key='ch4')

        self.assertEqual(first.metadata['card_id'], second.metadata['card_id'])
        self.assertEqual(
            self.parent.cards.filter(card_kind=KanbanCard.KIND_SUBTASK).count(), 1
        )


class ProcurementChildTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='proc-actor', email='p@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name='Proc Press')
        self.parent = WorkOrder.objects.create(
            title='Needs parts',
            status='backlog',
            priority='low',
            machine=self.machine,
        )
        self.short_part = Part.objects.create(
            name='Rare', description='r', component=True
        )
        StockItem.objects.create(part=self.short_part, quantity=Decimal('1'))
        self.ok_part = Part.objects.create(
            name='Common', description='c', component=True
        )
        StockItem.objects.create(part=self.ok_part, quantity=Decimal('100'))

    def _add_part(self, part, quantity):
        line = WorkOrderPart.objects.create(
            work_order=self.parent, part=part, quantity=Decimal(quantity)
        )
        line.check_and_allocate()
        return line

    def test_no_shortfall_generates_nothing(self):
        self._add_part(self.ok_part, 5)
        self.assertIsNone(
            scheduling.generate_procurement_child(
                parent_id=self.parent.pk, actor=self.actor
            )
        )

    def test_shortfall_generates_a_procurement_card(self):
        line = self._add_part(self.short_part, 10)
        child = scheduling.generate_procurement_child(
            parent_id=self.parent.pk, actor=self.actor
        )

        self.assertIsNotNone(child)
        self.assertEqual(child.card_kind, KanbanCard.KIND_PROCUREMENT)
        self.assertEqual(child.work_order_id, self.parent.pk)
        self.assertEqual(line.quantity, Decimal('10'))
        self.assertEqual(self.parent.work_order_parts.count(), 1)

    def test_generation_is_idempotent(self):
        self._add_part(self.short_part, 10)
        first = scheduling.generate_procurement_child(
            parent_id=self.parent.pk, actor=self.actor
        )
        second = scheduling.generate_procurement_child(
            parent_id=self.parent.pk, actor=self.actor
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            self.parent.cards.filter(
                card_kind=KanbanCard.KIND_PROCUREMENT
            ).count(),
            1,
        )


class CloseoutBlockedByChildTest(TestCase):
    """A work order cannot close while one of its child cards is open."""

    def test_incomplete_children_helper(self):
        machine = AssetMachine.objects.create(name='CB Press')
        work_order = WorkOrder.objects.create(
            title='P', status='backlog', priority='low', machine=machine
        )
        KanbanColumn.objects.update_or_create(
            key='done',
            defaults={
                'label': 'Done',
                'color': 'green',
                'order': 3,
                'is_terminal': True,
            },
        )
        KanbanCard.objects.create(
            work_order=work_order,
            title='open child',
            status='backlog',
            card_kind=KanbanCard.KIND_SUBTASK,
        )
        KanbanCard.objects.create(
            work_order=work_order,
            title='done child',
            status='done',
            card_kind=KanbanCard.KIND_SUBTASK,
        )

        incomplete = scheduling.incomplete_children(work_order.pk)
        self.assertEqual(incomplete.count(), 1)
        self.assertEqual(incomplete.get().title, 'open child')


class ChildCardApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='child-sup', email='cs@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Child API Press')
        self.parent = WorkOrder.objects.create(
            title='Parent', status='backlog', priority='low', machine=self.machine
        )

    def test_create_child_via_api(self):
        response = self.client.post(
            reverse('kanban-command-create-child', kwargs={'pk': self.parent.pk}),
            data={'title': 'Subtask', 'card_kind': 'subtask'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['work_order'], self.parent.pk)
        self.assertEqual(body['card_kind'], KanbanCard.KIND_SUBTASK)
        self.assertEqual(body['title'], 'Subtask')

    def test_generate_procurement_via_api_with_no_shortfall(self):
        response = self.client.post(
            reverse(
                'kanban-command-generate-procurement',
                kwargs={'pk': self.parent.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['generated'])
