"""API contract tests for Job Kit planning."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from assets.models import AssetMachine, Client
from company.models import Company
from part.models import Part
from stock.models import StockLocation
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitLine,
    Procedure,
    ProcedureResourceKind,
    ProcedureResourceRequirement,
    ProcedureRevision,
    ProcedureRevisionStatus,
    WorkOrder,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.procedure_execution import apply_procedure_revision
from users.models import RuleSet


@override_settings(AIMMS_JOB_KITS_ENABLED=True)
class JobKitAPITest(TestCase):
    """Exercise scoped Job Kit build, manual lines, shortages, and events."""

    def setUp(self):
        """Apply one published procedure with a real resource requirement."""
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
        self.work_order = WorkOrder.objects.create(
            title='Plan kit', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM, customer=self.customer,
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
        """Build the fixture Work's kit with its current lifecycle token."""
        return self.client.post(
            f'{self.base}/build/',
            {'expected_version': self.work_order.lifecycle_version,
             'idempotency_key': key},
            format='json',
        )

    def test_kit_is_404_before_build(self):
        """A readable Work has no kit resource before an explicit build."""
        response = self.client.get(f'{self.base}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_build_creates_kit_with_lines(self):
        """Build derives the requirement from the applied procedure snapshot."""
        response = self.build()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['lines']), 1)
        self.assertEqual(response.data['lines'][0]['source'], 'procedure')

        detail = self.client.get(f'{self.base}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(len(detail.data['lines']), 1)

    def test_add_update_and_remove_manual_line(self):
        """Authorized planning commands preserve the manual line lifecycle."""
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
        """Manual edits cannot rewrite the procedure's pinned requirements."""
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
        """A successful build retains its native planning audit event."""
        self.build()
        response = self.client.get(f'{self.base}/events/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event_types = {row['event_type'] for row in response.data['results']}
        self.assertIn('JOB_KIT_BUILT', event_types)

    def test_shortages_empty_at_planning(self):
        """Planning alone records no stock shortage."""
        self.build()
        response = self.client.get(f'{self.base}/shortages/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    @override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
    def test_customer_work_build_is_shared_but_foreign_machine_work_is_not_found(self):
        """O24 shares Work planning without granting its action or machine roles."""
        # The already-applied snapshot belongs to Work even after its customer
        # changes; reading/building it does not authorize the procedure catalog.
        self.work_order.customer = self.other_customer
        self.work_order.save(update_fields=['customer'])
        tenant = Client.objects.create(name='Kit viewer client', code='kit-viewer')
        reader = get_user_model().objects.create_user(username='kit-shared-viewer')
        group = Group.objects.create(name='Kit shared Work viewers')
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        reader.groups.add(group)
        reader.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=tenant.pk)
        }
        self.client.force_authenticate(reader)
        denied = self.build('shared-viewer-cannot-build')
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(JobKit.objects.filter(work_order=self.work_order).exists())

        reader.user_permissions.add(Permission.objects.get(
            content_type__app_label='tasks', codename='manage_jobkit'
        ))
        # Reload instead of carrying has_perm's negative cache across the grant.
        reader = get_user_model().objects.get(pk=reader.pk)
        reader.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=tenant.pk)
        }
        self.client.force_authenticate(reader)
        built = self.build('shared-authorized-build')
        self.assertEqual(built.status_code, status.HTTP_201_CREATED)
        self.assertEqual(built.data['work_order'], self.work_order.pk)
        self.assertEqual(len(built.data['lines']), 1)
        self.assertEqual(built.data['lines'][0]['requested_part'], self.part.pk)

        foreign_client = Client.objects.create(name='Kit foreign client', code='kit-foreign')
        foreign = WorkOrder.objects.create(
            title='Foreign', status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=AssetMachine.objects.create(name='Foreign kit pump', client=foreign_client),
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        response = self.client.post(
            f'/api/tasks/work-orders/{foreign.pk}/job-kit/build/',
            {'expected_version': foreign.lifecycle_version, 'idempotency_key': 'x'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
    def test_shared_kit_staging_location_requires_machine_scope_and_stock_view(self):
        """Work visibility alone cannot reveal the kit's staging stock location."""
        built = self.build()
        self.assertEqual(built.status_code, status.HTTP_201_CREATED)
        tenant = Client.objects.create(name='Kit stock client', code='kit-stock')
        self.work_order.machine = AssetMachine.objects.create(
            name='Kit staging pump', client=tenant
        )
        self.work_order.save(update_fields=['machine'])
        location = StockLocation.objects.create(name='Kit staging location')
        kit = JobKit.objects.get(work_order=self.work_order)
        kit.staging_location = location
        kit.save(update_fields=['staging_location'])

        group = Group.objects.create(name='Kit staging viewers')
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        stock_group = Group.objects.create(name='Kit stock viewers')
        RuleSet.objects.update_or_create(
            group=stock_group, name='stock', defaults={'can_view': True}
        )
        reader = get_user_model().objects.create_user(username='kit-staging-viewer')
        reader.groups.add(group, stock_group)
        reader.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=tenant.pk)
        }
        self.client.force_authenticate(reader)
        own = self.client.get(f'{self.base}/')
        self.assertEqual(own.status_code, status.HTTP_200_OK)
        self.assertEqual(own.data['staging_location'], location.pk)

        reader.maintenance_scopes = set()
        revoked = self.client.get(f'{self.base}/')
        self.assertEqual(revoked.status_code, status.HTTP_200_OK)
        self.assertIsNone(revoked.data['staging_location'])
        self.assertEqual(len(revoked.data['lines']), 1)

        reader.groups.remove(stock_group)
        reader = get_user_model().objects.get(pk=reader.pk)
        reader.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=tenant.pk)
        }
        self.client.force_authenticate(reader)
        without_stock = self.client.get(f'{self.base}/')
        self.assertEqual(without_stock.status_code, status.HTTP_200_OK)
        self.assertIsNone(without_stock.data['staging_location'])

    @override_settings(AIMMS_JOB_KITS_ENABLED=False)
    def test_disabled_flag_hides_api(self):
        """Disabled Job Kits remain unavailable even to authorized readers."""
        response = self.client.get(f'{self.base}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
