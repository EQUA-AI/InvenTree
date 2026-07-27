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


class OverviewDetailSectionsTest(InvenTreeAPITestCase):
    """The detail page renders every applicable section from one read.

    Also pins the labelling rule the plan is explicit about: an unverified
    diagnosis blob is reported as preliminary, and only explicit human
    verification flips it.
    """

    roles = 'all'

    def setUp(self):
        """Create a packet-owned work order raised from a health anomaly."""
        super().setUp()
        from machine_health.models import AnomalySeverity, HealthSource, SourceType
        from machine_health.services.anomalies import fingerprint_for, record_anomaly
        from repair import investigation

        self.machine = AssetMachine.objects.create(
            name=f'Section machine {uuid.uuid4().hex[:6]}'
        )
        self.work_order = KanbanCard.objects.create(
            reference=f'WO-SECT-{uuid.uuid4().hex[:6]}',
            title='Investigate vibration',
            status=KanbanCard.STATUS_IN_PROGRESS,
            priority=KanbanCard.PRIORITY_HIGH,
            machine=self.machine,
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            fault_summary='Vibration climbing across runs',
            symptom='1x amplitude rising',
            production_impact='Throughput reduced 15%',
            criticality='high',
            diagnosis={
                'likely_cause': 'Drive-end bearing wear',
                'confidence': 0.6,
                'alternatives': [],
                'evidence': [],
                'confirm_tests': [],
                'status': 'available',
                'data_window': {},
                'provider': 'test',
                'verified_by_user': False,
                'amendments': [],
                'schema_version': 2,
            },
        )
        RepairPacketGate.objects.create(
            packet=self.packet,
            name='LOTO',
            gate_type='loto',
            status='pending',
            is_blocking=True,
        )
        investigation.record_finding(
            self.packet,
            finding_key='F-01',
            observation='Vibration measured 9.4 mm/s at the drive end',
            category='measurement',
            value=9.4,
            unit='mm/s',
        )
        investigation.approve_repair_scope(
            self.packet,
            scope_lines=['Isolate', 'Replace bearing'],
            verified_cause='Drive-end bearing wear',
        )

        source = HealthSource.objects.create(
            name=f'SCADA {uuid.uuid4().hex[:6]}', source_type=SourceType.SCADA
        )
        anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('overview', 'vib'),
            title='Vibration above limit',
            severity=AnomalySeverity.CRITICAL,
            source=source,
            detector='threshold',
            detector_version='1',
            alarm_code='VIB-HI',
        )
        anomaly.work_order = self.work_order
        anomaly.save(update_fields=['work_order'])

        self.url = reverse(
            'kanban-card-overview', kwargs={'pk': self.work_order.pk}
        )

    def test_source_alert_is_projected(self):
        """The alert that raised the work order is on the page."""
        data = self.get(self.url, expected_code=200).data

        alert = data['source_alert']
        self.assertEqual(alert['title'], 'Vibration above limit')
        self.assertEqual(alert['alarm_code'], 'VIB-HI')
        self.assertEqual(alert['detector'], 'threshold')
        self.assertEqual(alert['machine_id'], self.machine.pk)

    def test_findings_and_approved_scope_are_projected(self):
        """Both render without a second request."""
        data = self.get(self.url, expected_code=200).data

        packet = data['repair_packet']
        [finding] = packet['findings']
        self.assertEqual(finding['finding_key'], 'F-01')
        self.assertEqual(finding['value'], 9.4)
        self.assertEqual(packet['approved_scope']['version'], 1)
        self.assertEqual(len(packet['approved_scope']['scope_lines']), 2)

    def test_unverified_diagnosis_is_reported_as_preliminary(self):
        """The page must not label an unverified model output a diagnosis."""
        data = self.get(self.url, expected_code=200).data

        self.assertTrue(data['repair_packet']['diagnosis_is_preliminary'])
        self.assertEqual(data['repair_packet']['diagnosis_status'], 'available')

    def test_human_verification_flips_the_label(self):
        """Only explicit verification promotes it to a diagnosis."""
        self.packet.diagnosis = {**self.packet.diagnosis, 'verified_by_user': True}
        self.packet.save(update_fields=['diagnosis'])

        data = self.get(self.url, expected_code=200).data

        self.assertFalse(data['repair_packet']['diagnosis_is_preliminary'])

    def test_blocking_gate_is_visible_on_the_page(self):
        """Safety state is readable from the work order, not just the packet."""
        data = self.get(self.url, expected_code=200).data

        [gate] = data['repair_packet']['gates']
        self.assertTrue(gate['is_blocking'])
        self.assertEqual(gate['status'], 'pending')

    def test_work_order_without_a_packet_has_no_alert_or_packet(self):
        """A plain work order renders without the repair-only sections."""
        plain = KanbanCard.objects.create(
            reference=f'WO-PLAIN-{uuid.uuid4().hex[:6]}',
            title='Routine inspection',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_LOW,
            machine=self.machine,
        )

        data = self.get(
            reverse('kanban-card-overview', kwargs={'pk': plain.pk}),
            expected_code=200,
        ).data

        self.assertIsNone(data['repair_packet'])
        self.assertIsNone(data['source_alert'])
