"""Tests for AI generation, provenance/idempotency, audit trail and schema.

These exercise the ``wf7`` seam (``repair.generation``) end-to-end on the Django
side: the heuristic fallback, a mocked AI-service provider (parts + gates), the
auto fallback path, failure recording, idempotency, the audit timeline and the
new cancel / generation-status API endpoints - all against real Postgres.
"""

import os
import uuid
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase

from .generation import (
    AIServiceUnavailable,
    GeneratedPartLine,
    GeneratedSafetyGate,
    GenerationResult,
)
from .models import (
    GenerationStatus,
    PacketStatus,
    RepairPacket,
    RepairPacketEvent,
    RepairPacketGenerationRun,
)
from .schema import (
    DIAGNOSIS_SCHEMA_VERSION,
    coerce_diagnosis,
    empty_diagnosis,
    validate_diagnosis,
)
from . import services


def _stub_generator(result=None, *, raises=None, name='ai_service'):
    """Build a stand-in generator returning ``result`` or raising ``raises``."""
    gen = mock.Mock()
    gen.name = name
    if raises is not None:
        gen.generate.side_effect = raises
    else:
        gen.generate.return_value = result
    return gen


class DiagnosisSchemaTest(TestCase):
    """Unit tests for the versioned diagnosis schema."""

    def test_empty_diagnosis_is_valid(self):
        validate_diagnosis(empty_diagnosis())

    def test_coerce_clamps_confidence_and_versions(self):
        d = coerce_diagnosis({'likely_cause': 'x', 'confidence': 5})
        self.assertEqual(d['confidence'], 1.0)
        self.assertEqual(d['schema_version'], DIAGNOSIS_SCHEMA_VERSION)
        self.assertEqual(d['confidence_label'], 'high')

    def test_validate_rejects_bad_shapes(self):
        with self.assertRaises(ValidationError):
            validate_diagnosis({'likely_cause': 'x'})  # missing keys
        with self.assertRaises(ValidationError):
            validate_diagnosis('not a dict')


class HeuristicGenerationTest(TestCase):
    """The offline heuristic provider produces a valid, persisted diagnosis."""

    def test_generate_populates_and_advances(self):
        packet = RepairPacket.objects.create(fault_summary='Bearing vibration high')
        services.run_repair_packet_workflow(packet, {'generator': 'heuristic'})
        packet.refresh_from_db()

        self.assertEqual(packet.status, PacketStatus.DIAGNOSED)
        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        self.assertEqual(packet.diagnosis_schema_version, DIAGNOSIS_SCHEMA_VERSION)
        validate_diagnosis(packet.diagnosis)
        self.assertTrue(packet.agent_run_id)

        run = packet.generation_runs.first()
        self.assertEqual(run.status, RepairPacketGenerationRun.RunStatus.SUCCEEDED)
        self.assertEqual(run.provider, 'heuristic')
        self.assertTrue(
            packet.events.filter(
                event_type=RepairPacketEvent.EventType.GENERATED
            ).exists()
        )

    def test_electrical_fault_creates_loto_gate(self):
        packet = RepairPacket.objects.create(
            fault_summary='Motor contactor coil failed, no voltage'
        )
        services.run_repair_packet_workflow(packet, {'generator': 'heuristic'})
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())


class GenerationIdempotencyTest(TestCase):
    """Re-running generation with the same agent_run_id is a no-op."""

    def test_same_run_id_does_not_duplicate(self):
        packet = RepairPacket.objects.create(fault_summary='pump seal leak')
        run_id = uuid.uuid4().hex
        services.run_repair_packet_workflow(
            packet, {'generator': 'heuristic', 'agent_run_id': run_id}
        )
        services.run_repair_packet_workflow(
            packet, {'generator': 'heuristic', 'agent_run_id': run_id}
        )
        self.assertEqual(
            RepairPacketGenerationRun.objects.filter(agent_run_id=run_id).count(), 1
        )


class AIProviderGenerationTest(TestCase):
    """A (mocked) AI provider result materialises parts + gates onto the packet."""

    def test_ai_result_creates_work_order_parts_and_gates(self):
        from part.models import Part

        part = Part.objects.create(
            name=f'Contactor-{uuid.uuid4().hex[:6]}',
            description='test',
            component=True,
            purchaseable=True,
        )
        result = GenerationResult(
            diagnosis=coerce_diagnosis(
                {'likely_cause': 'Contactor failed', 'confidence': 0.9}
            ),
            parts=[GeneratedPartLine(name=part.name, part_id=part.pk, quantity=2)],
            safety_gates=[
                GeneratedSafetyGate(name='LOTO', gate_type='loto', requires_photo=True)
            ],
            confidence=0.9,
            provider='ai_service',
        )
        packet = RepairPacket.objects.create(fault_summary='contactor fault')
        with mock.patch.object(
            services, 'get_generator', return_value=_stub_generator(result)
        ):
            services.run_repair_packet_workflow(packet, {})
        packet.refresh_from_db()

        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        self.assertIsNotNone(packet.work_order_id)
        self.assertEqual(packet.work_order.card_parts.count(), 1)
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())
        self.assertEqual(packet.generation_runs.first().provider, 'ai_service')


class GenerationFallbackTest(TestCase):
    """Auto mode falls back to the heuristic provider when the AI service errors."""

    def test_auto_falls_back_to_heuristic(self):
        packet = RepairPacket.objects.create(fault_summary='fan not starting')
        with mock.patch(
            'repair.generation.AIServiceGenerator.generate',
            side_effect=AIServiceUnavailable('down'),
        ):
            services.run_repair_packet_workflow(packet, {'generator': 'auto'})
        packet.refresh_from_db()
        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        self.assertEqual(packet.generation_runs.first().provider, 'heuristic')


class GenerationFailureTest(TestCase):
    """Generation failures are recorded and leave the packet re-generatable."""

    def test_generator_exception_records_failure(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        with mock.patch.object(
            services,
            'get_generator',
            return_value=_stub_generator(raises=RuntimeError('boom')),
        ):
            services.run_repair_packet_workflow(packet, {})
        packet.refresh_from_db()
        self.assertEqual(packet.generation_status, GenerationStatus.FAILED)
        self.assertEqual(packet.status, PacketStatus.DRAFT)  # unchanged
        run = packet.generation_runs.first()
        self.assertEqual(run.status, RepairPacketGenerationRun.RunStatus.FAILED)
        self.assertIn('boom', run.error)
        self.assertTrue(
            packet.events.filter(
                event_type=RepairPacketEvent.EventType.GENERATION_FAILED
            ).exists()
        )

    def test_invalid_diagnosis_is_rejected(self):
        bad = GenerationResult(diagnosis={'nonsense': True}, provider='ai_service')
        packet = RepairPacket.objects.create(fault_summary='x')
        with mock.patch.object(
            services, 'get_generator', return_value=_stub_generator(bad)
        ):
            services.run_repair_packet_workflow(packet, {})
        packet.refresh_from_db()
        self.assertEqual(packet.generation_status, GenerationStatus.FAILED)


class AdvanceAuditTest(TestCase):
    """Lifecycle transitions record an audit event with actor + reason."""

    def test_advance_records_event(self):
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        ok, _ = services.advance_packet(
            packet, PacketStatus.APPROVED, reason='looks good'
        )
        self.assertTrue(ok)
        event = packet.events.filter(
            event_type=RepairPacketEvent.EventType.ADVANCED
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.from_status, PacketStatus.DIAGNOSED)
        self.assertEqual(event.to_status, PacketStatus.APPROVED)
        self.assertEqual(event.reason, 'looks good')

    def test_cancel_records_canceled_event(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        ok, _ = services.advance_packet(
            packet, PacketStatus.CANCELED, reason='duplicate'
        )
        self.assertTrue(ok)
        self.assertTrue(
            packet.events.filter(
                event_type=RepairPacketEvent.EventType.CANCELED
            ).exists()
        )


class RepairPacketNewEndpointsTest(InvenTreeAPITestCase):
    """New API endpoints: cancel, generation-status, and events in the payload."""

    roles = 'all'

    def setUp(self):
        super().setUp()
        RepairPacket.objects.all().delete()

    def test_cancel_endpoint(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        url = reverse('repair-packet-cancel', kwargs={'pk': packet.pk})
        resp = self.post(url, {'reason': 'not needed'}, expected_code=200)
        self.assertTrue(resp.data['ok'])
        self.assertEqual(resp.data['status'], PacketStatus.CANCELED)

    def test_generation_status_endpoint(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        services.run_repair_packet_workflow(packet, {'generator': 'heuristic'})
        url = reverse('repair-packet-generation-status', kwargs={'pk': packet.pk})
        resp = self.get(url, expected_code=200)
        self.assertEqual(resp.data['generation_status'], GenerationStatus.SUCCEEDED)
        self.assertIsNotNone(resp.data['latest_generation_run'])

    def test_detail_exposes_events_and_generation_fields(self):
        packet = RepairPacket.objects.create(fault_summary='x')
        services.run_repair_packet_workflow(packet, {'generator': 'heuristic'})
        url = reverse('repair-packet-detail', kwargs={'pk': packet.pk})
        resp = self.get(url, expected_code=200)
        self.assertIn('events', resp.data)
        self.assertIn('generation_status', resp.data)
        self.assertIn('latest_generation_run', resp.data)
        self.assertGreaterEqual(len(resp.data['events']), 1)

    def test_generate_via_api_uses_heuristic_when_ai_down(self):
        packet = RepairPacket.objects.create(fault_summary='overheating gearbox')
        url = reverse('repair-packet-generate', kwargs={'pk': packet.pk})
        with mock.patch.dict(os.environ, {'AIMMS_REPAIR_GENERATOR': 'heuristic'}):
            resp = self.post(url, {}, expected_code=200)
        self.assertEqual(resp.data['generation_status'], GenerationStatus.SUCCEEDED)
        self.assertEqual(resp.data['status'], PacketStatus.DIAGNOSED)
