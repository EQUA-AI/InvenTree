"""API contract tests for Job Kit custody (stage/issue/consume/return)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rest_framework import status
from rest_framework.test import APIClient

from company.models import Company
from part.models import Part
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitAllocationStatus,
    JobKitLine,
    KanbanCard,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kits import reserve_job_kit


@override_settings(AIMMS_JOB_KITS_ENABLED=True)
class JobKitCustodyAPITest(TestCase):
    """Exercise the scoped custody transition endpoints."""

    def setUp(self):
        self.customer = Company.objects.create(name='Custody API', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='custody-api', email='custody-api@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.work_order = KanbanCard.objects.create(
            title='Custody API WO', status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.actor
        )
        self.part = Part.objects.create(name='Filter', description='f', component=True)
        self.stock = StockItem.objects.create(part=self.part, quantity=Decimal('10'))
        self.line = JobKitLine.objects.create(
            kit=self.kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=self.part, selected_part=self.part,
            required_quantity=Decimal('4'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )
        reserve_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='reserve',
        )
        self.allocation = JobKitAllocation.objects.get(line=self.line)
        self.base = (
            f'/api/tasks/work-orders/{self.work_order.pk}/job-kit/'
            f'allocations/{self.allocation.pk}'
        )

    def test_stage_issue_consume_flow(self):
        stage = self.client.post(f'{self.base}/stage/', {}, format='json')
        self.assertEqual(stage.status_code, status.HTTP_200_OK)
        self.assertEqual(stage.data['status'], JobKitAllocationStatus.STAGED)

        issue = self.client.post(f'{self.base}/issue/', {}, format='json')
        self.assertEqual(issue.status_code, status.HTTP_200_OK)
        self.assertEqual(issue.data['status'], JobKitAllocationStatus.ISSUED)

        consume = self.client.post(f'{self.base}/consume/', {}, format='json')
        self.assertEqual(consume.status_code, status.HTTP_200_OK)
        self.assertEqual(consume.data['status'], JobKitAllocationStatus.CONSUMED)
        self.assertIsNotNone(consume.data['stock_tracking_id'])

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('6'))

    def test_consume_after_consume_conflicts(self):
        self.client.post(f'{self.base}/consume/', {}, format='json')
        again = self.client.post(f'{self.base}/consume/', {}, format='json')
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)

    def test_return_endpoint(self):
        response = self.client.post(f'{self.base}/return/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], JobKitAllocationStatus.RETURNED)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('10'))

    def test_stage_scan_required(self):
        self.line.requires_scan = True
        self.line.save(update_fields=['requires_scan'])
        blocked = self.client.post(f'{self.base}/stage/', {}, format='json')
        self.assertEqual(blocked.status_code, status.HTTP_409_CONFLICT)
        ok = self.client.post(
            f'{self.base}/stage/', {'scan_proof': {'barcode': 'X1'}}, format='json'
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)

    @override_settings(AIMMS_JOB_KITS_ENABLED=False)
    def test_custody_hidden_when_flag_disabled(self):
        response = self.client.post(f'{self.base}/consume/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
