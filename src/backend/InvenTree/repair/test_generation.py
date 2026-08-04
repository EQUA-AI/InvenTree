"""Tests for AI generation, provenance/idempotency, audit trail and schema.

These exercise the ``wf7`` seam (``repair.generation``) end-to-end on the Django
side: the heuristic fallback, a mocked AI-service provider (parts + gates), the
auto fallback path, failure recording, idempotency, the audit timeline and the
new cancel / generation-status API endpoints - all against real Postgres.
"""

import json
import os
import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from tasks.models import WorkOrder, WorkOrderLifecycle, WorkOrderType

from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase

from . import generation, services
from .generation import (
    AIServiceUnavailableError,
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
        """An empty diagnosis payload passes schema validation."""
        validate_diagnosis(empty_diagnosis())

    def test_coerce_clamps_confidence_and_versions(self):
        """Coercion clamps confidence to [0, 1] and stamps the schema version."""
        d = coerce_diagnosis({'likely_cause': 'x', 'confidence': 5})
        self.assertEqual(d['confidence'], 1.0)
        self.assertEqual(d['schema_version'], DIAGNOSIS_SCHEMA_VERSION)
        self.assertEqual(d['confidence_label'], 'high')

    def test_validate_rejects_bad_shapes(self):
        """Validation rejects payloads with missing keys or wrong types."""
        with self.assertRaises(ValidationError):
            validate_diagnosis({'likely_cause': 'x'})  # missing keys
        with self.assertRaises(ValidationError):
            validate_diagnosis('not a dict')


class HeuristicGenerationTest(TestCase):
    """The offline heuristic provider produces a valid, persisted diagnosis."""

    def test_generate_populates_and_advances(self):
        """Heuristic generation stores a valid diagnosis and advances the packet."""
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
        """An electrical fault summary yields a LOTO safety gate."""
        packet = RepairPacket.objects.create(
            fault_summary='Motor contactor coil failed, no voltage'
        )
        services.run_repair_packet_workflow(packet, {'generator': 'heuristic'})
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())


class GenerationIdempotencyTest(TestCase):
    """Re-running generation with the same agent_run_id is a no-op."""

    def test_same_run_id_does_not_duplicate(self):
        """Replaying the same agent_run_id creates only one generation run."""
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

    def _result(self, part):
        return GenerationResult(
            diagnosis=coerce_diagnosis({
                'likely_cause': 'Contactor failed',
                'confidence': 0.9,
            }),
            parts=[GeneratedPartLine(name=part.name, part_id=part.pk, quantity=2)],
            safety_gates=[
                GeneratedSafetyGate(name='LOTO', gate_type='loto', requires_photo=True)
            ],
            confidence=0.9,
            provider='ai_service',
        )

    @staticmethod
    def _part():
        from part.models import Part

        return Part.objects.create(
            name=f'Contactor-{uuid.uuid4().hex[:6]}',
            description='test',
            component=True,
            purchaseable=True,
        )

    def test_ai_result_creates_work_order_parts_and_gates(self):
        """An AI provider result creates the work order, part lines and gates."""
        part = self._part()
        machine = AssetMachine.objects.create(name=f'Panel-{uuid.uuid4().hex[:6]}')
        packet = RepairPacket.objects.create(
            fault_summary='contactor fault', machine=machine
        )

        with mock.patch.object(
            services, 'get_generator', return_value=_stub_generator(self._result(part))
        ):
            services.run_repair_packet_workflow(packet, {})
        packet.refresh_from_db()

        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        self.assertIsNotNone(packet.work_order_id)
        self.assertEqual(packet.work_order.work_order_parts.count(), 1)
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())
        self.assertEqual(packet.generation_runs.first().provider, 'ai_service')

    def test_packet_work_order_is_machine_linked_and_audited(self):
        """The packet's work order carries the packet machine and a CREATED event."""
        part = self._part()
        machine = AssetMachine.objects.create(name=f'Pump-{uuid.uuid4().hex[:6]}')
        packet = RepairPacket.objects.create(
            fault_summary='seal weeping', machine=machine, criticality='critical'
        )

        with mock.patch.object(
            services, 'get_generator', return_value=_stub_generator(self._result(part))
        ):
            services.run_repair_packet_workflow(packet, {})
        packet.refresh_from_db()

        work_order = packet.work_order
        self.assertEqual(work_order.machine_id, machine.pk)
        # Creating a repair packet plans work; it does not start it (plan 0.3).
        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(work_order.status, WorkOrder.STATUS_BACKLOG)
        self.assertEqual(work_order.work_order_type, WorkOrderType.CORRECTIVE)
        # 'critical' has no board equivalent and maps to the highest board step.
        self.assertEqual(work_order.priority, WorkOrder.PRIORITY_HIGH)
        self.assertTrue(work_order.events.filter(event_type='CREATED').exists())
        self.assertTrue(
            work_order.commands.filter(
                command='create',
                idempotency_key__startswith=f'repair-packet:{packet.pk}:work-order:',
            ).exists()
        )
        self.assertTrue(packet.events.filter(event_type='work_order_created').exists())

    def test_machineless_packet_creates_no_ungoverned_work_order(self):
        """Generation records the gap instead of fabricating an unscoped card."""
        part = self._part()
        packet = RepairPacket.objects.create(fault_summary='unknown asset fault')

        with mock.patch.object(
            services, 'get_generator', return_value=_stub_generator(self._result(part))
        ):
            services.run_repair_packet_workflow(packet, {})
        packet.refresh_from_db()

        # Diagnosis and gates still land; only the work order is withheld.
        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        self.assertIsNone(packet.work_order_id)
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())
        self.assertFalse(WorkOrder.objects.filter(machine__isnull=True).exists())

        event = packet.events.get(event_type='work_order_skipped')
        self.assertEqual(event.metadata['reason_code'], 'PACKET_HAS_NO_MACHINE')


class GenerationFallbackTest(TestCase):
    """Auto mode falls back to the heuristic provider when the AI service errors."""

    def test_auto_falls_back_to_heuristic(self):
        """Auto mode succeeds via the heuristic provider when the AI path fails."""
        packet = RepairPacket.objects.create(fault_summary='fan not starting')
        with mock.patch(
            'repair.generation.InProcessTurnGenerator.generate',
            side_effect=AIServiceUnavailableError('down'),
        ):
            services.run_repair_packet_workflow(packet, {'generator': 'auto'})
        packet.refresh_from_db()
        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        self.assertEqual(packet.generation_runs.first().provider, 'heuristic')


class _StubTurnService:
    """Records process() calls and answers with a fenced repair payload."""

    def __init__(self, payload=None, *, message=None):
        self.payload = payload
        self.message = message
        self.calls = []

    async def process(self, **kwargs):
        self.calls.append(kwargs)
        message = self.message
        if message is None:
            message = (
                'Repair packet diagnosis assembled.\n\n```json\n'
                + json.dumps(self.payload)
                + '\n```'
            )
        return SimpleNamespace(
            thread_id=kwargs['thread_id'], turn_id='turn-generated', message=message
        )


class InProcessGenerationTest(TestCase):
    """The in-process provider runs as the requesting user, or not at all."""

    _PAYLOAD = {
        'diagnosis': {'likely_cause': 'Contactor coil short', 'confidence': 0.72},
        'parts': [],
        'safety_gates': [{'name': 'LOTO', 'gate_type': 'loto', 'requires_photo': True}],
    }

    def setUp(self):
        """Pin the boundary policy key and reset the cached AI settings."""
        from ai.core.config import get_settings

        self._env = mock.patch.dict(
            os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'repair-test-site'}
        )
        self._env.start()
        get_settings.cache_clear()
        self.addCleanup(self._env.stop)
        self.addCleanup(get_settings.cache_clear)

    @staticmethod
    def _user(**kwargs):
        return get_user_model().objects.create_user(
            username=f'tech-{uuid.uuid4().hex[:8]}', password='x', **kwargs
        )

    def test_runs_as_the_requesting_user(self):
        """The turn executes under the caller's principal, pinned to wf7."""
        user = self._user()
        packet = RepairPacket.objects.create(fault_summary='motor breaker trips')
        stub = _StubTurnService(self._PAYLOAD)

        with mock.patch.object(generation, '_get_turn_service', return_value=stub):
            services.run_repair_packet_workflow(
                packet, {'generator': 'ai_service', 'user_id': user.pk}
            )
        packet.refresh_from_db()

        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        run = packet.generation_runs.first()
        self.assertEqual(run.provider, 'ai_service')

        call = stub.calls[0]
        self.assertEqual(call['actor'].user_pk, str(user.pk))
        self.assertEqual(call['actor'].authentication_method, 'in_process_generation')
        self.assertEqual(call['actor'].scope, 'repair-test-site')
        self.assertEqual(call['trusted_context'].server_policy_key, 'repair-test-site')
        self.assertEqual(call['server_pinned_workflow'], 'wf7')
        self.assertEqual(
            call['idempotency_key'], f'repair-gen:{packet.pk}:{packet.agent_run_id}'
        )
        # Turn provenance is recorded for the packet UI (S10).
        self.assertEqual(run.result_summary['thread_id'], call['thread_id'])
        self.assertEqual(run.result_summary['turn_id'], 'turn-generated')
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())

    def test_missing_user_never_reaches_the_turn_service(self):
        """No requesting user means no AI turn; auto mode degrades to heuristic."""
        packet = RepairPacket.objects.create(fault_summary='pump seized')
        with mock.patch.object(generation, '_get_turn_service') as svc:
            services.run_repair_packet_workflow(packet, {'generator': 'auto'})
        svc.assert_not_called()
        packet.refresh_from_db()
        self.assertEqual(packet.generation_status, GenerationStatus.SUCCEEDED)
        self.assertEqual(packet.generation_runs.first().provider, 'heuristic')

    def test_unknown_user_falls_back_to_heuristic(self):
        """A user id that matches no row is refused, not substituted."""
        packet = RepairPacket.objects.create(fault_summary='pump seized')
        with mock.patch.object(generation, '_get_turn_service') as svc:
            services.run_repair_packet_workflow(
                packet, {'generator': 'auto', 'user_id': 99999999}
            )
        svc.assert_not_called()
        self.assertEqual(packet.generation_runs.first().provider, 'heuristic')

    def test_inactive_user_falls_back_to_heuristic(self):
        """A deactivated account cannot anchor an in-process principal."""
        user = self._user(is_active=False)
        packet = RepairPacket.objects.create(fault_summary='pump seized')
        with mock.patch.object(generation, '_get_turn_service') as svc:
            services.run_repair_packet_workflow(
                packet, {'generator': 'auto', 'user_id': user.pk}
            )
        svc.assert_not_called()
        self.assertEqual(packet.generation_runs.first().provider, 'heuristic')

    def test_replay_uses_a_stable_idempotency_key(self):
        """The same agent_run_id always maps onto the same turn idempotency key."""
        user = self._user()
        stub = _StubTurnService(self._PAYLOAD)
        context = {
            'repair_packet_id': 41,
            'thread_id': 'repair-41',
            'user_id': user.pk,
            'agent_run_id': 'run-stable',
        }
        with mock.patch.object(generation, '_get_turn_service', return_value=stub):
            first = generation.InProcessTurnGenerator().generate(
                fault_summary='pump', context=context
            )
            generation.InProcessTurnGenerator().generate(
                fault_summary='pump', context=context
            )
        self.assertEqual(first.provider, 'ai_service')
        self.assertEqual(
            {call['idempotency_key'] for call in stub.calls},
            {'repair-gen:41:run-stable'},
        )

    def test_payloadless_turn_falls_back_to_heuristic(self):
        """A turn without a fenced repair payload is unusable, not trusted."""
        user = self._user()
        packet = RepairPacket.objects.create(fault_summary='drive fault')
        stub = _StubTurnService(message='I could not analyze this fault.')
        with mock.patch.object(generation, '_get_turn_service', return_value=stub):
            services.run_repair_packet_workflow(
                packet, {'generator': 'auto', 'user_id': user.pk}
            )
        self.assertEqual(packet.generation_runs.first().provider, 'heuristic')


class GenerationFailureTest(TestCase):
    """Generation failures are recorded and leave the packet re-generatable."""

    def test_generator_exception_records_failure(self):
        """A provider exception marks the run failed and leaves the packet in draft."""
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
        """A provider result with an invalid diagnosis fails generation."""
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
        """Advancing a packet records an ADVANCED event with the reason."""
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
        """Cancelling a packet records a CANCELED event."""
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
        """Reset repair packets before each test."""
        super().setUp()
        RepairPacket.objects.all().delete()

    def test_cancel_endpoint(self):
        """The cancel endpoint cancels the packet and reports ok."""
        packet = RepairPacket.objects.create(fault_summary='x')
        url = reverse('repair-packet-cancel', kwargs={'pk': packet.pk})
        resp = self.post(url, {'reason': 'not needed'}, expected_code=200)
        self.assertTrue(resp.data['ok'])
        self.assertEqual(resp.data['status'], PacketStatus.CANCELED)

    def test_generation_status_endpoint(self):
        """The generation-status endpoint reports status and the latest run."""
        packet = RepairPacket.objects.create(fault_summary='x')
        services.run_repair_packet_workflow(packet, {'generator': 'heuristic'})
        url = reverse('repair-packet-generation-status', kwargs={'pk': packet.pk})
        resp = self.get(url, expected_code=200)
        self.assertEqual(resp.data['generation_status'], GenerationStatus.SUCCEEDED)
        self.assertIsNotNone(resp.data['latest_generation_run'])

    def test_detail_exposes_events_and_generation_fields(self):
        """The detail payload includes events and generation fields."""
        packet = RepairPacket.objects.create(fault_summary='x')
        services.run_repair_packet_workflow(packet, {'generator': 'heuristic'})
        url = reverse('repair-packet-detail', kwargs={'pk': packet.pk})
        resp = self.get(url, expected_code=200)
        self.assertIn('events', resp.data)
        self.assertIn('generation_status', resp.data)
        self.assertIn('latest_generation_run', resp.data)
        self.assertGreaterEqual(len(resp.data['events']), 1)

    def test_generate_via_api_uses_heuristic_when_ai_down(self):
        """The generate endpoint succeeds with the heuristic provider forced."""
        packet = RepairPacket.objects.create(fault_summary='overheating gearbox')
        url = reverse('repair-packet-generate', kwargs={'pk': packet.pk})
        with mock.patch.dict(os.environ, {'AIMMS_REPAIR_GENERATOR': 'heuristic'}):
            resp = self.post(url, {}, expected_code=200)
        self.assertEqual(resp.data['generation_status'], GenerationStatus.SUCCEEDED)
        self.assertEqual(resp.data['status'], PacketStatus.DIAGNOSED)
