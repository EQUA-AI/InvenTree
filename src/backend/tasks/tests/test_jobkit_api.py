"""API contract tests for Job Kit planning."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from company.models import Company
from part.models import Part
from tasks.models import (
    FulfillmentMode,
    JobKitLine,
    KanbanCard,
    Procedure,
    ProcedureResourceKind,
    ProcedureResourceRequirement,
    ProcedureRevision,
    ProcedureRevisionStatus,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.procedure_execution import apply_procedure_revision


@override_settings(AIMMS_JOB_KITS_ENABLED=True)
class JobKitAPITest(TestCase):
    """Exercise scoped Job Kit build, manual lines, shortages, and events."""

    def setUp(self):
        self.customer = Company.objects.create(
            name='Kit API Customer', is_customer=True
        )
        self.other_customer = Company.objects.create(
            name='Other Kit Customer', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username='kit-api-supervisor', email='kit-api@example.com',
            password='test-password',
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.work_order = KanbanCard.objects.create(
            title='Plan kit', status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.procedure = Procedure.objects.create(
            code='KIT-API', name='Kit API Procedure', customer=self.customer,
            created_by=self.actor,
        )
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure, revision=1,
            status=ProcedureRevisionStatus.PUBLISHED,
            work_order_type=WorkOrderType.PREVENTIVE, created_by=self.actor,
            published_by=self.actor, published_at=timezone.now(),
        )
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])
        self.part = Part.objects.create(name='Filter', description='Filter part')
        self.manual_part = Part.objects.create(name='Rag', description='Wipe rag')
        ProcedureResourceRequirement.objects.create(
            revision=self.revision, sequence=1, kind=ProcedureResourceKind.PART,
            part=self.part, quantity=Decimal('2'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME,
        )
        # Apply the procedure through the service so the kit has a source.
        apply_procedure_revision(
            work_order_id=self.work_order.pk, revision_id=self.revision.pk,
            actor=self.actor, expected_version=self.work_order.lifecycle_version,
            idempotency_key='kit-api-apply',
        )
        self.base = f'/api/tasks/work-orders/{self.work_order.pk}/job-kit'

    def build(self, key='build-1'):
        return self.client.post(
            f'{self.base}/build/',
            {'expected_version': self.work_order.lifecycle_version,
             'idempotency_key': key},
            format='json',
        )

    def test_kit_is_404_before_build(self):
        response = self.client.get(f'{self.base}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_build_creates_kit_with_lines(self):
        response = self.build()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['lines']), 1)
        self.assertEqual(response.data['lines'][0]['source'], 'procedure')

        detail = self.client.get(f'{self.base}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(len(detail.data['lines']), 1)

    def test_add_update_and_remove_manual_line(self):
        self.build()
        add = self.client.post(
            f'{self.base}/lines/',
            {'kind': ProcedureResourceKind.CONSUMABLE, 'part_id': self.manual_part.pk,
             'required_quantity': '3', 'fulfillment_mode': FulfillmentMode.RESERVE_CONSUME},
            format='json',
        )
        self.assertEqual(add.status_code, status.HTTP_201_CREATED)
        self.assertEqual(add.data['source'], 'manual')
        line_id = add.data['id']

        patch = self.client.patch(
            f'{self.base}/lines/{line_id}/', {'required_quantity': '7'},
            format='json',
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(patch.data['required_quantity']), Decimal('7'))

        delete = self.client.delete(f'{self.base}/lines/{line_id}/')
        self.assertEqual(delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(JobKitLine.objects.filter(pk=line_id).exists())

    def test_cannot_edit_procedure_line(self):
        self.build()
        procedure_line = JobKitLine.objects.get(
            kit__work_order=self.work_order, source='procedure'
        )
        response = self.client.patch(
            f'{self.base}/lines/{procedure_line.pk}/', {'required_quantity': '9'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_events_include_build(self):
        self.build()
        response = self.client.get(f'{self.base}/events/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event_types = {row['event_type'] for row in response.data['results']}
        self.assertIn('JOB_KIT_BUILT', event_types)

    def test_shortages_empty_at_planning(self):
        self.build()
        response = self.client.get(f'{self.base}/shortages/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_cross_scope_build_is_not_found(self):
        foreign = KanbanCard.objects.create(
            title='Foreign', status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.other_customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        response = self.client.post(
            f'/api/tasks/work-orders/{foreign.pk}/job-kit/build/',
            {'expected_version': foreign.lifecycle_version, 'idempotency_key': 'x'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(AIMMS_JOB_KITS_ENABLED=False)
    def test_disabled_flag_hides_api(self):
        response = self.client.get(f'{self.base}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
