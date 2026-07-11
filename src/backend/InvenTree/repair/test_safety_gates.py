"""Tests for Safety Gates & LOTO S2 backend slice."""

from django.test import TestCase
from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase

from approvals.executors import SafetyGateExecutor
from approvals.models import ActionType, Approval

from . import services
from .models import (
    GateStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    RepairPacketApprovalLink,
    RepairPacketGate,
    RepairPacketEvent,
    SafetyEvidenceProof,
    SafetyGateTemplate,
)


class SafetyTemplateResolutionTest(TestCase):
    """Template applicability and gate materialisation."""

    def test_resolve_templates_creates_matching_gate_once(self):
        packet = RepairPacket.objects.create(
            fault_summary='Motor contactor has no voltage',
            status=PacketStatus.DIAGNOSED,
        )
        created = services.resolve_safety_gates(packet)
        created_again = services.resolve_safety_gates(packet)

        self.assertGreaterEqual(created, 1)
        self.assertEqual(created_again, 0)
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())
        self.assertTrue(
            packet.events.filter(
                event_type=RepairPacketEvent.EventType.GATES_RESOLVED
            ).exists()
        )

    def test_pending_blocking_template_gate_blocks_approval(self):
        packet = RepairPacket.objects.create(
            fault_summary='Motor breaker fault',
            status=PacketStatus.DIAGNOSED,
        )
        services.resolve_safety_gates(packet)
        ok, detail = services.advance_packet(packet, PacketStatus.APPROVED)

        self.assertFalse(ok)
        self.assertIn('Safety gate', detail)


class SafetyGateActionTest(TestCase):
    """Gate confirm / verify / waive behaviour."""

    def test_required_photo_blocks_confirm_until_proof_exists(self):
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        gate = RepairPacketGate.objects.create(
            packet=packet,
            name='Photo gate',
            requires_photo=True,
        )

        ok, detail = services.confirm_gate(gate)
        self.assertFalse(ok)
        self.assertIn('photo', detail.lower())

        services.add_gate_proof(gate, SafetyEvidenceProof.ProofType.PHOTO)
        ok, detail = services.confirm_gate(gate)
        gate.refresh_from_db()
        self.assertTrue(ok, detail)
        self.assertEqual(gate.status, GateStatus.CONFIRMED)

    def test_loto_gate_blocks_confirm_until_points_verified(self):
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        gate = RepairPacketGate.objects.create(
            packet=packet,
            name='LOTO',
            gate_type='loto',
        )
        point = LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='MCC-1 bucket',
            status=LockoutPoint.PointStatus.LOCKED,
        )

        ok, detail = services.confirm_gate(gate)
        self.assertFalse(ok)
        self.assertIn('lockout', detail.lower())

        point.status = LockoutPoint.PointStatus.VERIFIED
        point.save(update_fields=['status'])
        ok, detail = services.confirm_gate(gate)
        self.assertTrue(ok, detail)

    def test_second_person_verifier_must_differ(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username='loto-user')
        packet = RepairPacket.objects.create(fault_summary='x')
        gate = RepairPacketGate.objects.create(
            packet=packet,
            name='Two person gate',
            requires_second_person=True,
        )
        gate.confirm(user=user)

        ok, detail = services.verify_gate(gate, user=user)
        self.assertFalse(ok)
        self.assertIn('different', detail.lower())

    def test_high_risk_waiver_creates_safety_approval(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        template = SafetyGateTemplate.objects.create(
            name='High risk safety gate',
            gate_type='loto',
            risk_tier=3,
        )
        gate = RepairPacketGate.objects.create(
            packet=packet,
            template=template,
            name=template.name,
            gate_type=template.gate_type,
        )

        ok, detail = services.waive_gate(
            gate,
            reason='controlled exception',
            authority='EHS supervisor',
        )
        self.assertFalse(ok)
        self.assertIn('Safety approval required', detail)
        approval = Approval.objects.get(action_type=ActionType.SAFETY_GATE)
        self.assertTrue(
            RepairPacketApprovalLink.objects.filter(
                packet=packet, approval=approval, purpose='safety'
            ).exists()
        )

    def test_safety_gate_executor_waives_gate(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        gate = RepairPacketGate.objects.create(packet=packet, name='Executor gate')
        result = SafetyGateExecutor().execute(
            {
                'gate_id': gate.pk,
                'action': 'waive',
                'reason': 'approved waiver',
                'authority': 'EHS',
            },
            idempotency_key='safety-test-key',
        )
        gate.refresh_from_db()
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(gate.status, GateStatus.WAIVED)
        self.assertEqual(gate.waiver_authority, 'EHS')


class SafetyGateAPITest(InvenTreeAPITestCase):
    """API behaviour for safety gates."""

    roles = 'all'

    def setUp(self):
        super().setUp()
        RepairPacket.objects.all().delete()

    def test_resolve_gates_endpoint(self):
        packet = RepairPacket.objects.create(
            fault_summary='Motor coil voltage fault',
            status=PacketStatus.DIAGNOSED,
        )
        url = reverse('repair-packet-resolve-gates', kwargs={'pk': packet.pk})
        response = self.post(url, {}, expected_code=200)

        self.assertGreaterEqual(response.data['created'], 1)
        self.assertGreaterEqual(len(response.data['gates']), 1)

    def test_proof_then_confirm_endpoint(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        gate = RepairPacketGate.objects.create(
            packet=packet,
            name='Photo gate',
            requires_photo=True,
        )
        proof_url = reverse(
            'repair-packet-gate-proof',
            kwargs={'pk': packet.pk, 'gate_pk': gate.pk},
        )
        self.post(
            proof_url,
            {'proof_type': SafetyEvidenceProof.ProofType.PHOTO, 'value': {'ok': True}},
            expected_code=201,
        )
        confirm_url = reverse(
            'repair-packet-gate-confirm',
            kwargs={'pk': packet.pk, 'gate_pk': gate.pk},
        )
        response = self.post(confirm_url, {'note': 'photo captured'}, expected_code=200)
        self.assertTrue(response.data['ok'])
        self.assertEqual(response.data['status'], GateStatus.CONFIRMED)

    def test_lockout_endpoint_blocks_close_until_restored(self):
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.EXECUTING
        )
        gate = RepairPacketGate.objects.create(
            packet=packet,
            name='LOTO',
            gate_type='loto',
            status=GateStatus.CONFIRMED,
        )
        lockout_url = reverse(
            'repair-packet-gate-lockout',
            kwargs={'pk': packet.pk, 'gate_pk': gate.pk},
        )
        point = self.post(
            lockout_url,
            {
                'energy_source': LockoutPoint.EnergySource.ELECTRICAL,
                'isolation_device': 'MCC-1 bucket',
                'status': LockoutPoint.PointStatus.VERIFIED,
            },
            expected_code=200,
        )
        advance_url = reverse('repair-packet-advance', kwargs={'pk': packet.pk})
        blocked = self.post(advance_url, {'to': PacketStatus.CLOSED}, expected_code=400)
        self.assertFalse(blocked.data['ok'])
        self.assertIn('not restored', blocked.data['detail'])

        self.post(
            lockout_url,
            {
                'pk': point.data['pk'],
                'status': LockoutPoint.PointStatus.RESTORED,
                'isolation_device': 'MCC-1 bucket',
            },
            expected_code=200,
        )
        closed = self.post(advance_url, {'to': PacketStatus.CLOSED}, expected_code=200)
        self.assertTrue(closed.data['ok'])
