"""API contract tests for Job Kit stock reservation."""

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
    JobKitLine,
    JobKitStatus,
    WorkOrder,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope


@override_settings(AIMMS_JOB_KITS_ENABLED=True)
class JobKitReservationAPITest(TestCase):
    """Exercise reserve, allocations list, and release endpoints."""

    def setUp(self):
        self.customer = Company.objects.create(name='Res API Cust', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='res-api', email='res-api@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.work_order = WorkOrder.objects.create(
            title='Reserve API WO', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.actor
        )
        self.part = Part.objects.create(name='Filter', description='f', component=True)
        StockItem.objects.create(part=self.part, quantity=Decimal('10'))
        self.line = JobKitLine.objects.create(
            kit=self.kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=self.part, selected_part=self.part,
            required_quantity=Decimal('4'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )
        self.base = f'/api/tasks/work-orders/{self.work_order.pk}/job-kit'

    def reserve(self, key='res-1'):
        return self.client.post(
            f'{self.base}/reserve/',
            {'expected_version': self.work_order.lifecycle_version,
             'idempotency_key': key},
            format='json',
        )

    def test_reserve_endpoint_makes_kit_ready(self):
        response = self.reserve()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], JobKitStatus.READY)

        allocations = self.client.get(f'{self.base}/allocations/')
        self.assertEqual(allocations.status_code, status.HTTP_200_OK)
        self.assertEqual(allocations.data['count'], 1)
        self.assertEqual(Decimal(allocations.data['results'][0]['quantity']), Decimal('4'))

    def test_reserve_then_release_frees_stock(self):
        self.reserve()
        allocation = JobKitAllocation.objects.get(line=self.line)

        release = self.client.post(
            f'{self.base}/allocations/{allocation.pk}/release/'
        )
        self.assertEqual(release.status_code, status.HTTP_200_OK)
        self.assertEqual(release.data['status'], 'released')

        allocation.refresh_from_db()
        self.assertEqual(allocation.status, 'released')
        # Line is required again -> kit should reflect the resulting shortage.
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.status, JobKitStatus.SHORT)

    def test_release_foreign_allocation_is_404(self):
        self.reserve()
        other_wo = WorkOrder.objects.create(
            title='Other', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        # Give the other work order its own kit so the failure is specifically
        # "this allocation does not belong to this work order" (404), not
        # "no kit" (409).
        JobKit.objects.create(work_order=other_wo, created_by=self.actor)
        allocation = JobKitAllocation.objects.get(line=self.line)
        response = self.client.post(
            f'/api/tasks/work-orders/{other_wo.pk}/job-kit/'
            f'allocations/{allocation.pk}/release/'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_reserve_is_idempotent(self):
        first = self.reserve(key='same')
        second = self.reserve(key='same')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(JobKitAllocation.objects.count(), 1)

    @override_settings(AIMMS_JOB_KITS_ENABLED=False)
    def test_reserve_hidden_when_flag_disabled(self):
        response = self.reserve()
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
