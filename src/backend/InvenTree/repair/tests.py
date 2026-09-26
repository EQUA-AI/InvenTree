"""Tests for the Repair Packet application."""

from datetime import datetime

from django.test import TestCase
from django.utils import timezone

from .models import (
    ApprovedRepairScope,
    GateStatus,
    PacketStatus,
    RepairInvestigationFinding,
    RepairPacket,
    RepairPacketEvent,
    RepairPacketGate,
    is_valid_packet_transition,
)
from .serializers import (
    ApprovedRepairScopeSerializer,
    RepairInvestigationFindingSerializer,
    RepairPacketEventSerializer,
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


class SerializedTimeFormatTest(TestCase):
    """The repair API's timestamps must not need a guess about their zone.

    The project-wide ``DATETIME_FORMAT`` renders ``2026-09-25 21:31`` - space
    separated, truncated to the minute, and with no offset. ``dayjs()`` and
    ``new Date()`` both read a string of that shape as *local* time, so every
    client outside UTC misplaces the instant by its own offset. The repair UI
    renders these three fields through exactly those two functions:
    :file:`RepairPacketDetail.tsx` the event time, and both
    :file:`InvestigationPanel.tsx` and :file:`InvestigationSection.tsx` the
    finding and approval times. On a UTC developer machine the defect is
    invisible, which is how it survived.

    Asserted by round-tripping through :func:`datetime.fromisoformat`, which
    accepts the offset when the deployment runs with time zones on and the bare
    ISO string when - as under these tests - it does not. Either way a space
    separator or a dropped seconds field fails.
    """

    def setUp(self):
        """Build one packet carrying an event, a finding and an approved scope."""
        self.packet = RepairPacket.objects.create(fault_summary='pump trip')

    def assertIsoRoundTrip(self, rendered, expected):
        """Assert ``rendered`` is ISO-8601 and still means ``expected``."""
        self.assertIn('T', rendered, f'{rendered!r} is not ISO-8601')
        self.assertEqual(datetime.fromisoformat(rendered), expected)

    def test_event_time_is_iso_8601(self):
        """The packet history table reads ``created_at`` with ``new Date()``."""
        event = RepairPacketEvent.objects.create(
            packet=self.packet, event_type=RepairPacketEvent.EventType.CREATED
        )
        self.assertIsoRoundTrip(
            RepairPacketEventSerializer(event).data['created_at'], event.created_at
        )

    def test_finding_time_is_iso_8601(self):
        """Both investigation panels read ``observed_at`` with ``dayjs()``."""
        observed_at = timezone.now()
        finding = RepairInvestigationFinding.objects.create(
            packet=self.packet,
            finding_key='vib-1',
            observation='bearing vibration rising',
            observed_at=observed_at,
        )
        self.assertIsoRoundTrip(
            RepairInvestigationFindingSerializer(finding).data['observed_at'],
            observed_at,
        )

    def test_approval_time_is_iso_8601(self):
        """Both investigation panels read ``approved_at`` with ``dayjs()``."""
        approved_at = timezone.now()
        scope = ApprovedRepairScope.objects.create(
            packet=self.packet, version=1, approved_at=approved_at
        )
        self.assertIsoRoundTrip(
            ApprovedRepairScopeSerializer(scope).data['approved_at'], approved_at
        )
