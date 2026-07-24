"""Tests for the Repair Packet application."""

from django.test import TestCase

from .models import (
    GateStatus,
    PacketStatus,
    RepairPacket,
    RepairPacketGate,
    is_valid_packet_transition,
)
from .services import advance_packet


class PacketFSMTest(TestCase):
    """Tests for the repair packet finite state machine."""

    def test_valid_transitions(self):
        """Each step of the happy-path lifecycle is a legal transition."""
        self.assertTrue(
            is_valid_packet_transition(PacketStatus.DRAFT, PacketStatus.DIAGNOSED)
        )
        self.assertTrue(
            is_valid_packet_transition(PacketStatus.DIAGNOSED, PacketStatus.APPROVED)
        )
        self.assertTrue(
            is_valid_packet_transition(PacketStatus.APPROVED, PacketStatus.EXECUTING)
        )
        self.assertTrue(
            is_valid_packet_transition(PacketStatus.EXECUTING, PacketStatus.CLOSED)
        )

    def test_illegal_transitions(self):
        """Skipping ahead or leaving a closed packet is rejected."""
        self.assertFalse(
            is_valid_packet_transition(PacketStatus.DRAFT, PacketStatus.EXECUTING)
        )
        self.assertFalse(
            is_valid_packet_transition(PacketStatus.CLOSED, PacketStatus.DRAFT)
        )


class PacketReferenceTest(TestCase):
    """Tests reference auto-generation."""

    def test_reference_generated_on_save(self):
        """Saving a packet assigns a zero-padded RP- reference from its pk."""
        packet = RepairPacket.objects.create(fault_summary='pump trip')
        self.assertTrue(packet.reference.startswith('RP-'))
        self.assertEqual(packet.reference, f'RP-{packet.pk:06d}')


class PacketAdvanceTest(TestCase):
    """Tests the advance service (FSM + gate enforcement)."""

    def test_advance_rejects_illegal(self):
        """Advancing along an illegal transition fails with a clear detail."""
        packet = RepairPacket.objects.create(fault_summary='pump trip')
        ok, detail = advance_packet(packet, PacketStatus.EXECUTING)
        self.assertFalse(ok)
        self.assertIn('Illegal transition', detail)

    def test_advance_draft_to_diagnosed(self):
        """A draft packet can be advanced to diagnosed."""
        packet = RepairPacket.objects.create(fault_summary='pump trip')
        ok, _ = advance_packet(packet, PacketStatus.DIAGNOSED)
        self.assertTrue(ok)
        self.assertEqual(packet.status, PacketStatus.DIAGNOSED)

    def test_pending_gate_blocks_approval(self):
        """A pending safety gate blocks approval of a diagnosed packet."""
        packet = RepairPacket.objects.create(
            fault_summary='pump trip', status=PacketStatus.DIAGNOSED
        )
        RepairPacketGate.objects.create(
            packet=packet, name='LOTO', status=GateStatus.PENDING
        )
        ok, detail = advance_packet(packet, PacketStatus.APPROVED)
        self.assertFalse(ok)
        self.assertIn('gate', detail.lower())

    def test_confirmed_gate_allows_approval(self):
        """A confirmed safety gate lets the packet advance to approved."""
        packet = RepairPacket.objects.create(
            fault_summary='pump trip', status=PacketStatus.DIAGNOSED
        )
        RepairPacketGate.objects.create(
            packet=packet, name='LOTO', status=GateStatus.CONFIRMED
        )
        ok, _ = advance_packet(packet, PacketStatus.APPROVED)
        self.assertTrue(ok)
        self.assertEqual(packet.status, PacketStatus.APPROVED)
