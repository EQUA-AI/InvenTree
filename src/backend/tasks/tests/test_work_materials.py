"""R6 P22: a job's material readout keeps each fact and its recorder apart."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets.registry_models import AssetComponent
from company.models import Company
from part.models import Part
from stock.models import StockItem, StockLocation
from tasks.models import WorkOrderPart
from tasks.scope import MaintenanceScope
from tasks.tests.closeout_fixtures import CloseoutEnvMixin

FLAGS = {'AIMMS_WORK_ORDERS_ENABLED': True}
KITS = {**FLAGS, 'AIMMS_JOB_KITS_ENABLED': True}


class WorkMaterialsTests(CloseoutEnvMixin, TestCase):
    """Real kit, reservation and custody services; synthetic records."""

    def setUp(self):
        """One scoped job on one machine."""
        self.build_env(username='materials')

    def read(self, user=None, pk=None):
        """The readout as the Work Parts panel reads it (scope kept on the actor)."""
        from rest_framework.test import APIRequestFactory, force_authenticate

        from tasks.materials_api import WorkOrderMaterials

        pk = pk or self.work_order.pk
        request = APIRequestFactory().get(f'/api/tasks/work-orders/{pk}/materials/')
        force_authenticate(request, user=user or self.actor)
        response = WorkOrderMaterials.as_view()(request, pk=pk)
        response.render()
        return response

    @override_settings(**FLAGS)
    def test_without_a_kit_a_stock_check_is_not_a_reservation(self):
        """Requirement rows carry their check; nothing is reserved."""
        part = Part.objects.create(name='Seal kit', component=True, units='ea')
        WorkOrderPart.objects.create(
            work_order=self.work_order,
            part=part,
            quantity=Decimal('2'),
            allocated_quantity=Decimal('2'),
            allocation_status=WorkOrderPart.ALLOCATION_FULL,
            allocation_note='Fully allocated: 2 available in stock',
        )
        body = self.read().data
        self.assertEqual(body['source'], 'requirements')
        (row,) = body['requirements']
        self.assertEqual(row['part']['name'], 'Seal kit')
        self.assertEqual(row['part']['units'], 'ea')
        self.assertEqual(row['quantity'], 2.0)
        self.assertIsNone(row['reservations'])
        self.assertEqual(row['availability_check']['status'], 'full')

    @override_settings(**KITS)
    def test_reserved_issued_and_used_stay_separate_with_their_recorders(self):
        """Reserve, issue and consume through the real services."""
        from tasks.services.job_kit_custody import consume_allocation, issue_allocation

        line = self.build_kit_line(quantity='3')
        store = StockLocation.objects.create(name='Store A')
        StockItem.objects.filter(part=line.requested_part).update(location=store)
        self.reserve_kit()
        allocation = line.allocations.get()

        body = self.read().data
        self.assertEqual(body['source'], 'job_kit')
        (row,) = body['requirements']
        self.assertIsNone(row['availability_check'])
        (reserved,) = row['reservations']
        self.assertEqual(reserved['status'], 'reserved')
        self.assertEqual(reserved['quantity'], 3.0)
        self.assertEqual(reserved['reserved']['by'], self.actor.username)
        self.assertIsNone(reserved['issued'])
        self.assertEqual(reserved['stock_item']['location'], 'Store A')

        issue_allocation(
            work_order_id=self.work_order.pk,
            allocation_id=allocation.pk,
            actor=self.actor,
        )
        (issued,) = self.read().data['requirements'][0]['reservations']
        self.assertEqual(issued['status'], 'issued')
        # Issue records when, not who: never filled in from the reserver.
        self.assertIsNotNone(issued['issued']['at'])
        self.assertIsNone(issued['issued']['by'])
        self.assertIsNone(issued['closed'])

        consume_allocation(
            work_order_id=self.work_order.pk,
            allocation_id=allocation.pk,
            actor=self.actor,
        )
        (used,) = self.read().data['requirements'][0]['reservations']
        self.assertEqual(used['closed']['status'], 'consumed')
        # Who used it comes from the native stock-tracking entry.
        self.assertEqual(used['closed']['by'], self.actor.username)
        self.assertIsNotNone(used['closed']['tracking_id'])

    @override_settings(**KITS)
    def test_stock_item_identity_is_only_for_stock_readers(self):
        """A Work reader without the stock role sees quantities, not items."""
        self.build_kit_line(quantity='1')
        self.reserve_kit()
        reader = self.make_scoped_user('materials-reader')
        (reserved,) = self.read(reader).data['requirements'][0]['reservations']
        self.assertEqual(reserved['quantity'], 1.0)
        self.assertEqual(reserved['stock_item'], {'pk': None, 'hidden': True})

    @override_settings(**KITS)
    def test_a_job_outside_the_readers_scope_is_not_found(self):
        """Scope first: another customer's job reads as 404."""
        other = Company.objects.create(name='Other co', is_customer=True)
        outsider = get_user_model().objects.create_user(
            username='outsider', email='outsider@example.com', password='pw'
        )
        outsider.maintenance_scopes = {
            MaintenanceScope(customer_id=other.pk, site_key=None)
        }
        self.assertEqual(self.read(outsider).status_code, 404)

    @override_settings(**FLAGS)
    def test_the_named_slot_links_to_the_machines_parts(self):
        """A slot the machine holds is linked; another is only named."""
        part = Part.objects.create(name='Bearing', component=True)
        slot = AssetComponent.objects.create(
            machine=self.machine, part=part, code='PS-MOTOR-DE-BRG:1', name='DE'
        )
        self.work_order.affected_component_ref = 'PS-MOTOR-DE-BRG:1'
        self.work_order.save(update_fields=['affected_component_ref'])
        self.assertEqual(self.read().data['component']['pk'], slot.pk)
        self.work_order.affected_component_ref = 'PS-UNKNOWN:1'
        self.work_order.save(update_fields=['affected_component_ref'])
        component = self.read().data['component']
        self.assertIsNone(component['pk'])
        self.assertEqual(component['ref'], 'PS-UNKNOWN:1')

    @override_settings(AIMMS_WORK_ORDERS_ENABLED=False)
    def test_hidden_with_the_work_order_api(self):
        """Disabled canonical Work API: the readout does not exist either."""
        from django.http import Http404

        with self.assertRaises(Http404):
            self.read()
