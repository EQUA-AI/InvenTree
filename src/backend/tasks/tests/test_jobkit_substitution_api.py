"""API contract tests for governed Job Kit substitution."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rest_framework import status
from rest_framework.test import APIClient

from company.models import Company
from part.models import Part
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitLine,
    JobKitSubstitutionStatus,
    WorkOrder,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope


@override_settings(AIMMS_JOB_KITS_ENABLED=True)
class JobKitSubstitutionAPITest(TestCase):
    """Exercise the propose and decide endpoints."""

    def setUp(self):
        self.customer = Company.objects.create(name='Sub API', is_customer=True)
        self.proposer = get_user_model().objects.create_superuser(
            username='sub-proposer', email='sp@example.com', password='pw'
        )
        self.decider = get_user_model().objects.create_superuser(
            username='sub-decider', email='sd@example.com', password='pw'
        )
        for user in (self.proposer, self.decider):
            user.maintenance_scopes = {
                MaintenanceScope(customer_id=self.customer.pk, site_key=None)
            }
        self.work_order = WorkOrder.objects.create(
            title='Sub API WO', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.proposer
        )
        self.requested = Part.objects.create(name='OEM', description='o', component=True)
        self.alternate = Part.objects.create(name='Alt', description='a', component=True)
        self.line = JobKitLine.objects.create(
            kit=self.kit, sequence=1, kind=ProcedureResourceKind.PART,
            requested_part=self.requested, selected_part=self.requested,
            required_quantity=Decimal('2'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
            substitution_policy='supervisor',
        )
        self.base = f'/api/tasks/work-orders/{self.work_order.pk}/job-kit'

    def test_propose_then_approve_flow(self):
        client = APIClient()
        client.force_authenticate(self.proposer)
        propose = client.post(
            f'{self.base}/lines/{self.line.pk}/substitutions/',
            {'proposed_part_id': self.alternate.pk, 'basis': {'note': 'ok'}},
            format='json',
        )
        self.assertEqual(propose.status_code, status.HTTP_201_CREATED)
        self.assertEqual(propose.data['status'], JobKitSubstitutionStatus.PROPOSED)
        sub_id = propose.data['id']

        decider_client = APIClient()
        decider_client.force_authenticate(self.decider)
        decide = decider_client.post(
            f'{self.base}/substitutions/{sub_id}/decide/',
            {'approve': True}, format='json',
        )
        self.assertEqual(decide.status_code, status.HTTP_200_OK)
        self.assertEqual(decide.data['status'], JobKitSubstitutionStatus.APPROVED)

        self.line.refresh_from_db()
        self.assertEqual(self.line.selected_part_id, self.alternate.pk)

    def test_proposer_cannot_decide_own_via_api(self):
        client = APIClient()
        client.force_authenticate(self.proposer)
        propose = client.post(
            f'{self.base}/lines/{self.line.pk}/substitutions/',
            {'proposed_part_id': self.alternate.pk}, format='json',
        )
        sub_id = propose.data['id']
        decide = client.post(
            f'{self.base}/substitutions/{sub_id}/decide/',
            {'approve': True}, format='json',
        )
        self.assertEqual(decide.status_code, status.HTTP_400_BAD_REQUEST)
        self.line.refresh_from_db()
        self.assertEqual(self.line.selected_part_id, self.requested.pk)
