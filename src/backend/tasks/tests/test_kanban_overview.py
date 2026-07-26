"""Complete work-order overview API tests."""

import datetime
import uuid
from decimal import Decimal

from django.urls import reverse

from assets.models import AssetMachine, AssetMaintenanceRecord
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part
from repair.models import RepairPacket, RepairPacketGate
from tasks.models import KanbanCard, KanbanCardDependency, KanbanCardPart
from tasks.workorder_models import WorkOrderEvent


class KanbanCardOverviewTest(InvenTreeAPITestCase):
    """Verify the stable overview surface used by work-order details."""

    roles = 'all'

    def setUp(self):
        """Create a parent work order and its complete related context."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name='Overview Machine', location='Plant / Bay 4'
        )
        self.parent = KanbanCard.objects.create(
            reference='WO-OVERVIEW-001',
            title='Parent repair',
            description='Replace a leaking process seal.',
            status=KanbanCard.STATUS_IN_PROGRESS,
            priority=KanbanCard.PRIORITY_HIGH,
            machine=self.machine,
        )
        self.child = KanbanCard.objects.create(
            reference='WO-OVERVIEW-002',
            title='Procure seal kit',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
            machine=self.machine,
            parent=self.parent,
            card_kind=KanbanCard.KIND_PROCUREMENT,
        )
        part = Part.objects.create(name='Seal kit', IPN='OV-SEAL')
        KanbanCardPart.objects.create(
            card=self.parent,
            part=part,
            quantity=Decimal('2'),
            allocation_status=KanbanCardPart.ALLOCATION_INSUFFICIENT,
        )
        KanbanCardDependency.objects.create(from_card=self.child, to_card=self.parent)
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.parent,
            fault_summary='Seal leakage',
            symptom='Visible process-water leak',
            criticality='high',
        )
        RepairPacketGate.objects.create(
            packet=self.packet, name='Electrical isolation', gate_type='loto'
        )
        AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            work_order=self.parent,
            date=datetime.date(2026, 7, 26),
            summary='Repair completed',
            details='Seal replaced and leak checked.',
            performed_by='Jordan Example',
        )
        WorkOrderEvent.objects.create(
            work_order=self.parent,
            event_type='CREATED',
            from_status='',
            to_status='planned',
            correlation_id=uuid.uuid4(),
        )

    def test_overview_includes_complete_context(self):
        """Overview returns hierarchy, parts, repair, history, and audit data."""
        response = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.parent.pk}),
            expected_code=200,
        )

        self.assertEqual(response.data['id'], self.parent.pk)
        self.assertEqual(response.data['machine_name'], self.machine.name)
        self.assertEqual(response.data['children'][0]['id'], self.child.pk)
        self.assertEqual(response.data['children'][0]['status'], self.child.status)
        self.assertEqual(response.data['parts'][0]['part_ipn'], 'OV-SEAL')
        self.assertEqual(response.data['parts'][0]['allocation_status'], 'insufficient')
        self.assertEqual(response.data['dependencies'][0]['card']['id'], self.child.pk)
        self.assertEqual(response.data['repair_packet']['id'], self.packet.pk)
        self.assertEqual(
            response.data['repair_packet']['gates'][0]['status'], 'pending'
        )
        self.assertEqual(
            response.data['maintenance_record']['summary'], 'Repair completed'
        )
        self.assertEqual(response.data['events'][0]['event_type'], 'CREATED')

    def test_child_overview_includes_parent(self):
        """A child task links back to its parent work order."""
        response = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.child.pk}),
            expected_code=200,
        )

        self.assertEqual(response.data['parent_detail']['id'], self.parent.pk)
        self.assertEqual(response.data['parent_detail']['reference'], 'WO-OVERVIEW-001')
