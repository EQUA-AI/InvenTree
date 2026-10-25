"""API contract test for structured work-order completion."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rest_framework import status
from rest_framework.test import APIClient

from assets.models import AssetMachine, AssetMaintenanceRecord
from company.models import Company
from tasks.models import (
    KanbanColumn,
    WorkOrder,
    WorkOrderCloseout,
    WorkOrderLifecycle,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope


@override_settings(AIMMS_WORK_ORDERS_ENABLED=True)
class WorkOrderCompleteAPITest(TestCase):
    """Exercise the scoped completion endpoint end to end."""

    def setUp(self):
        KanbanColumn.objects.update_or_create(key='done', defaults={'label': 'Done', 'is_terminal': True})
        self.customer = Company.objects.create(name='Complete API', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='complete-api', email='complete@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.machine = AssetMachine.objects.create(name='Compressor')
        self.work_order = WorkOrder.objects.create(
            title='Finish work', status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            machine=self.machine, assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.VERIFYING,
        )

    def test_complete_endpoint_writes_records(self):
        from tasks.models import KanbanCard

        independent = KanbanCard.objects.create(
            work_order=self.work_order,
            card_kind=KanbanCard.KIND_SUBTASK,
            title='Archived separately tracked piece', status=WorkOrder.STATUS_BACKLOG,
            is_active=False,
        )
        other_work = WorkOrder.objects.create(
            title='Unrelated open work', customer=self.customer, machine=self.machine,
            status=WorkOrder.STATUS_BACKLOG,
        )
        other_card = other_work.primary_card
        other_status = other_card.status
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/complete/',
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'complete-api-1',
                'action': 'Serviced unit', 'result': 'Nominal',
                'verification_summary': 'Pressure verified',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['lifecycle_status'], WorkOrderLifecycle.COMPLETED
        )
        self.work_order.refresh_from_db()
        terminal = KanbanColumn.terminal_key()
        self.assertIsNotNone(terminal)
        self.assertEqual(self.work_order.status, terminal)
        self.assertEqual(self.work_order.primary_card.status, terminal)
        independent.refresh_from_db()
        self.assertEqual(independent.status, WorkOrder.STATUS_BACKLOG)
        other_work.refresh_from_db()
        other_card.refresh_from_db()
        self.assertEqual(other_work.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(other_card.status, other_status)
        self.assertTrue(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).exists()
        )
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(work_order=self.work_order).exists()
        )

    def test_missing_closeout_field_is_400(self):
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/complete/',
            {
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': 'complete-api-2',
                'action': 'x', 'result': 'y',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
