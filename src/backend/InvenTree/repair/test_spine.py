"""End-to-end tests for the Repair Packet (spine) implementation.

Complements ``tests.py`` (which covers the FSM helper, reference generation and
the advance service in isolation) by exercising the full stack against real
Postgres objects: the HTTP API, the complete lifecycle, safety-gate gating, the
approvals integration and pre-execution revalidation with real parts + stock.
"""

import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from tasks.models import WorkOrder, WorkOrderPart

from approvals.models import ActionType, Approval, ApprovalStatus
from InvenTree.unit_test import InvenTreeAPITestCase

from .models import (
    GateStatus,
    PacketStatus,
    RepairPacket,
    RepairPacketApprovalLink,
    RepairPacketGate,
)
from .services import advance_packet, revalidate


def _make_approval(status=ApprovalStatus.PENDING, **overrides):
    """Create an Approval directly via the ORM for linking tests."""
    suffix = uuid.uuid4().hex[:8]
    data = {
        'action_type': ActionType.PURCHASE_ORDER,
        'summary': 'Spend approval for repair',
        'payload': {'line_items': []},
        'agent_run_id': f'ar-{suffix}',
        'agent_checkpoint_id': f'cp-{suffix}',
        'tool_call_id': f'tc-{suffix}',
        'idempotency_key': f'key-{suffix}',
        'status': status,
    }
    data.update(overrides)
    return Approval.objects.create(**data)


class RepairPacketAPITest(InvenTreeAPITestCase):
    """HTTP API behaviour and the full lifecycle for repair packets."""

    roles = 'all'

    def setUp(self):
        """Start each test from a clean slate."""
        super().setUp()
        RepairPacket.objects.all().delete()

    def test_create_packet_via_api(self):
        """A packet can be created through the API and gets a reference."""
        url = reverse('repair-packet-list')
        resp = self.post(
            url,
            {
                'fault_summary': 'Pump tripping on overload',
                'symptom': 'overload trip',
                'criticality': 'high',
            },
            expected_code=201,
        )
        self.assertEqual(resp.data['status'], PacketStatus.DRAFT)
        self.assertTrue(resp.data['reference'].startswith('RP-'))
        self.assertEqual(resp.data['criticality'], 'high')

    def test_generate_moves_to_diagnosed(self):
        """The generate endpoint populates diagnosis and advances to diagnosed."""
        packet = RepairPacket.objects.create(fault_summary='bearing noise')
        url = reverse('repair-packet-generate', kwargs={'pk': packet.pk})
        resp = self.post(url, {}, expected_code=200)
        self.assertEqual(resp.data['status'], PacketStatus.DIAGNOSED)
        self.assertIn('generated_from', resp.data['diagnosis'])

    def test_full_lifecycle_via_api(self):
        """Drive a packet through diagnosed -> approved -> executing -> closed."""
        packet = RepairPacket.objects.create(fault_summary='seal leak')
        gen = reverse('repair-packet-generate', kwargs={'pk': packet.pk})
        adv = reverse('repair-packet-advance', kwargs={'pk': packet.pk})

        self.post(gen, {}, expected_code=200)

        r1 = self.post(adv, {'to': PacketStatus.APPROVED}, expected_code=200)
        self.assertTrue(r1.data['ok'])
        self.assertEqual(r1.data['status'], PacketStatus.APPROVED)

        r2 = self.post(adv, {'to': PacketStatus.EXECUTING}, expected_code=200)
        self.assertTrue(r2.data['ok'])
        self.assertEqual(r2.data['status'], PacketStatus.EXECUTING)

        r3 = self.post(adv, {'to': PacketStatus.CLOSED}, expected_code=200)
        self.assertTrue(r3.data['ok'])
        self.assertEqual(r3.data['status'], PacketStatus.CLOSED)

    def test_illegal_transition_returns_400(self):
        """An illegal transition is rejected with a 400 and ok=False."""
        packet = RepairPacket.objects.create(fault_summary='x')
        adv = reverse('repair-packet-advance', kwargs={'pk': packet.pk})
        resp = self.post(adv, {'to': PacketStatus.EXECUTING}, expected_code=400)
        self.assertFalse(resp.data['ok'])
        self.assertIn('Illegal transition', resp.data['detail'])

    def test_pending_gate_blocks_approval_via_api(self):
        """A pending safety gate blocks the diagnosed -> approved transition."""
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        RepairPacketGate.objects.create(
            packet=packet, name='LOTO', status=GateStatus.PENDING
        )
        adv = reverse('repair-packet-advance', kwargs={'pk': packet.pk})
        resp = self.post(adv, {'to': PacketStatus.APPROVED}, expected_code=400)
        self.assertFalse(resp.data['ok'])
        self.assertIn('gate', resp.data['detail'].lower())

    def test_detail_includes_nested_sections(self):
        """The detail serializer embeds gates, parts and approvals sections."""
        packet = RepairPacket.objects.create(fault_summary='x')
        RepairPacketGate.objects.create(packet=packet, name='LOTO')
        url = reverse('repair-packet-detail', kwargs={'pk': packet.pk})
        resp = self.get(url, expected_code=200)
        self.assertIn('gates', resp.data)
        self.assertIn('parts', resp.data)
        self.assertIn('approvals', resp.data)
        self.assertEqual(len(resp.data['gates']), 1)
        self.assertEqual(resp.data['parts'], [])

    def test_search_by_reference(self):
        """The list endpoint is searchable by reference."""
        packet = RepairPacket.objects.create(fault_summary='searchable fault')
        url = reverse('repair-packet-list')
        resp = self.get(url, {'search': packet.reference}, expected_code=200)
        refs = [row['reference'] for row in resp.data]
        self.assertIn(packet.reference, refs)


class RepairPacketApprovalsGateTest(TestCase):
    """The approvals integration gates the diagnosed -> approved transition."""

    def test_pending_approval_blocks_approval(self):
        """A linked, unresolved approval blocks progression to approved."""
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        approval = _make_approval(status=ApprovalStatus.PENDING)
        RepairPacketApprovalLink.objects.create(
            packet=packet, approval=approval, purpose='spend'
        )
        ok, detail = advance_packet(packet, PacketStatus.APPROVED)
        self.assertFalse(ok)
        self.assertIn('approval', detail.lower())

    def test_approved_approval_allows_approval(self):
        """Once the linked approval is approved, progression is allowed."""
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        approval = _make_approval(status=ApprovalStatus.APPROVED)
        RepairPacketApprovalLink.objects.create(
            packet=packet, approval=approval, purpose='spend'
        )
        ok, _ = advance_packet(packet, PacketStatus.APPROVED)
        self.assertTrue(ok)
        self.assertEqual(packet.status, PacketStatus.APPROVED)


class RepairPacketRevalidationTest(TestCase):
    """Pre-execution revalidation (Drift Protection) against real parts/stock."""

    def _packet_with_part(self, stock_qty, needed):
        """Build an APPROVED packet whose work order needs a part."""
        from part.models import Part
        from stock.models import StockItem

        part = Part.objects.create(
            name=f'Bearing-{stock_qty}-{needed}-{uuid.uuid4().hex[:6]}',
            description='test part',
            component=True,
            purchaseable=True,
        )
        if stock_qty > 0:
            StockItem.objects.create(part=part, quantity=Decimal(stock_qty))

        work_order = WorkOrder.objects.create(
            title='WO',
            status=WorkOrder.STATUS_IN_PROGRESS,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )
        WorkOrderPart.objects.create(
            work_order=work_order, part=part, quantity=Decimal(needed)
        )
        return RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.APPROVED, work_order=work_order
        )

    def test_revalidation_passes_with_sufficient_stock(self):
        """Enough stock -> revalidation passes and execution starts."""
        packet = self._packet_with_part(stock_qty=10, needed=2)
        ok, detail = revalidate(packet)
        self.assertTrue(ok, detail)

        ok2, _ = advance_packet(packet, PacketStatus.EXECUTING)
        self.assertTrue(ok2)
        self.assertEqual(packet.status, PacketStatus.EXECUTING)

    def test_revalidation_fails_when_part_short(self):
        """No stock -> revalidation fails closed and status is unchanged."""
        packet = self._packet_with_part(stock_qty=0, needed=5)
        ok, detail = revalidate(packet)
        self.assertFalse(ok)
        self.assertIn('no longer', detail.lower())

        ok2, _ = advance_packet(packet, PacketStatus.EXECUTING)
        self.assertFalse(ok2)
        packet.refresh_from_db()
        self.assertEqual(packet.status, PacketStatus.APPROVED)

    def test_revalidation_without_work_order_passes(self):
        """A packet with no work order has nothing to revalidate."""
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.APPROVED
        )
        ok, _ = revalidate(packet)
        self.assertTrue(ok)
