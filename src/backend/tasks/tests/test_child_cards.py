"""Child-card composition: subtasks, procurement, closeout blocking (S6d, §5.10)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from company.models import Company
from part.models import Part
from stock.models import StockItem
from tasks.models import WorkOrder, WorkOrderPart, WorkOrderLifecycle
from tasks.services import scheduling


class CreateChildTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='child-actor', email='c@example.com', password='pw'
        )
        self.customer = Company.objects.create(name='Child Cust', is_customer=True)
        self.machine = AssetMachine.objects.create(
            name='Child Press', customer=self.customer
        )
        self.parent = WorkOrder.objects.create(
            title='Parent WO', status='backlog', priority='high',
            machine=self.machine, customer=self.customer,
        )

    def _create_child(self, **kw):
        return scheduling.create_child(
            parent_id=self.parent.pk,
            actor=self.actor,
            idempotency_key=kw.pop('idempotency_key', 'ch1'),
            title=kw.pop('title', 'Child'),
            **kw,
        )

    def test_child_inherits_machine_and_customer(self):
        result = self._create_child()
        child = WorkOrder.objects.get(pk=result.work_order_id)

        self.assertEqual(child.parent_id, self.parent.pk)
        self.assertEqual(child.machine_id, self.machine.pk)
        self.assertEqual(child.customer_id, self.customer.pk)
        self.assertEqual(child.card_kind, WorkOrder.KIND_SUBTASK)

    def test_depth_is_limited_to_one(self):
        child = WorkOrder.objects.get(pk=self._create_child().work_order_id)
        with self.assertRaises(scheduling.InvalidChild):
            scheduling.create_child(
                parent_id=child.pk,
                actor=self.actor,
                idempotency_key='ch2',
                title='Grandchild',
            )

    def test_unknown_parent_is_rejected(self):
        with self.assertRaises(scheduling.UnknownWorkOrder):
            scheduling.create_child(
                parent_id=999999, actor=self.actor,
                idempotency_key='ch3', title='Orphan',
            )

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(scheduling.InvalidChild):
            self._create_child(card_kind='nonsense')

    def test_create_child_is_idempotent(self):
        first = self._create_child(idempotency_key='ch4')
        second = self._create_child(idempotency_key='ch4')
        self.assertEqual(first.work_order_id, second.work_order_id)
        self.assertEqual(self.parent.children.count(), 1)

    def test_child_cannot_diverge_machine_on_update(self):
        child = WorkOrder.objects.get(pk=self._create_child().work_order_id)
        other = AssetMachine.objects.create(name='Other Child Machine')

        with self.assertRaises(scheduling.InvalidChild):
            scheduling.update_work_order_plan(
                work_order_id=child.pk,
                actor=self.actor,
                expected_version=child.lifecycle_version,
                idempotency_key='ch5',
                fields={'machine_id': other.pk},
            )


class ProcurementChildTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='proc-actor', email='p@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name='Proc Press')
        self.parent = WorkOrder.objects.create(
            title='Needs parts', status='backlog', priority='low',
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

    def _add_part(self, part, qty):
        cp = WorkOrderPart.objects.create(
            work_order=self.parent, part=part, quantity=Decimal(qty)
        )
        cp.check_and_allocate()
        return cp

    def test_no_shortfall_generates_nothing(self):
        self._add_part(self.ok_part, 5)
        result = scheduling.generate_procurement_child(
            parent_id=self.parent.pk, actor=self.actor
        )
        self.assertIsNone(result)

    def test_shortfall_generates_a_procurement_child(self):
        self._add_part(self.short_part, 10)  # only 1 in stock

        child = scheduling.generate_procurement_child(
            parent_id=self.parent.pk, actor=self.actor
        )

        self.assertIsNotNone(child)
        self.assertEqual(child.card_kind, WorkOrder.KIND_PROCUREMENT)
        self.assertEqual(child.parent_id, self.parent.pk)
        self.assertEqual(child.machine_id, self.machine.pk)
        # The child carries the shortfall (need 10, have 1 -> 9).
        line = child.work_order_parts.get(part=self.short_part)
        self.assertEqual(line.quantity, Decimal('9'))

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
            self.parent.children.filter(
                card_kind=WorkOrder.KIND_PROCUREMENT
            ).count(),
            1,
        )


class CloseoutBlockedByChildTest(TestCase):
    """A parent cannot close out while a child is still open."""

    def test_incomplete_children_helper(self):
        machine = AssetMachine.objects.create(name='CB Press')
        parent = WorkOrder.objects.create(
            title='P', status='backlog', priority='low', machine=machine
        )
        WorkOrder.objects.create(
            title='open child', status='backlog', priority='low',
            machine=machine, parent=parent,
        )
        WorkOrder.objects.create(
            title='done child', status='done', priority='low',
            machine=machine, parent=parent,
            lifecycle_status=WorkOrderLifecycle.COMPLETED,
        )

        incomplete = scheduling.incomplete_children(parent.pk)
        self.assertEqual(incomplete.count(), 1)


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
        self.assertEqual(body['parent'], self.parent.pk)
        self.assertEqual(body['card_kind'], 'subtask')

    def test_generate_procurement_via_api_with_no_shortfall(self):
        response = self.client.post(
            reverse(
                'kanban-command-generate-procurement',
                kwargs={'pk': self.parent.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['generated'])
