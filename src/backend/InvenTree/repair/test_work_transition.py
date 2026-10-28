"""Packet-owned intermediate Work commands preserve canonical lifecycle gates."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from rest_framework.test import APIClient
from tasks.models import (
    Procedure,
    ProcedureRevision,
    WorkOrder,
    WorkOrderCommand,
    WorkOrderLifecycle,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope, ScopeError
from tasks.services.work_orders import CommandConflict, transition_work_order

from assets.models import AssetMachine, Client
from company.models import Company

from . import services
from .models import GateStatus, PacketStatus, RepairPacket, RepairPacketGate


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=None, AIMMS_WORK_ORDERS_ENABLED=True
)
class PacketWorkTransitionTest(TestCase):
    """Exercise the owning HTTP adapter with a native role-granted operator."""

    def setUp(self):
        """Prepare scoped Draft Work with real view/change/transition grants."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_user(
            username=f'packet-work-{suffix}', password='pw'
        )
        group = Group.objects.create(name=f'Packet operators {suffix}')
        self.ruleset = group.rule_sets.get(name='work_order')
        for field in (
            'can_view',
            'can_change',
            'can_transition_workorder',
            'can_execute_workorder',
        ):
            setattr(self.ruleset, field, True)
        self.ruleset.save()
        self.actor.groups.add(group)
        self.customer = Company.objects.create(
            name=f'Packet customer {suffix}', is_customer=True
        )
        self.tenant = Client.objects.create(
            name='Packet Work client', code=f'packet-work-{suffix}'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None),
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk),
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.machine = AssetMachine.objects.create(
            name='Software-only pump fixture', client=self.tenant
        )
        self.work_order = WorkOrder.objects.create(
            title='Packet-owned fixture Work',
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.CORRECTIVE,
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine, work_order=self.work_order
        )
        self.url = f'/api/repair/packets/{self.packet.pk}/work-transition/'

    def command(self, to_status=WorkOrderLifecycle.PLANNED, **overrides):
        """Construct one versioned command with a caller-owned operation key."""
        return {
            'to_status': to_status,
            'expected_version': self.work_order.lifecycle_version,
            'idempotency_key': uuid.uuid4().hex,
            **overrides,
        }

    def set_states(self, packet_status, work_status):
        """Place synthetic fixtures at a specific lifecycle boundary."""
        self.packet.status = packet_status
        self.packet.save(update_fields=['status'])
        self.work_order.lifecycle_status = work_status
        self.work_order.save(update_fields=['lifecycle_status'])

    def test_native_plan_ready_start_verify_flow(self):
        """A normal operator uses packet commands and the existing atomic start."""
        for action, target in (
            ('plan', WorkOrderLifecycle.PLANNED),
            ('mark_ready', WorkOrderLifecycle.READY),
        ):
            readiness = self.client.get(self.url, {'action': action})
            self.assertEqual(readiness.status_code, 200)
            self.assertTrue(readiness.data['ready'])
            self.assertEqual(readiness.data['blockers'], [])
            self.assertEqual(readiness.data['work_order_id'], self.work_order.pk)
            response = self.client.post(self.url, self.command(target), format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['lifecycle_status'], target)
            self.assertIn('event_id', response.data)
            self.work_order.refresh_from_db()
            self.packet.refresh_from_db()
            self.assertEqual(self.packet.status, PacketStatus.DRAFT)

        for packet_status in (PacketStatus.DIAGNOSED, PacketStatus.APPROVED):
            advanced, detail = services.advance_packet(
                self.packet, packet_status, self.actor
            )
            self.assertTrue(advanced, detail)
        start = self.client.post(
            f'/api/repair/packets/{self.packet.pk}/start/',
            self.command(WorkOrderLifecycle.IN_PROGRESS),
            format='json',
        )
        self.assertEqual(start.status_code, 200, start.data)
        self.work_order.refresh_from_db()
        readiness = self.client.get(self.url)
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(readiness.data['action'], 'verify')
        self.assertTrue(readiness.data['ready'])
        verified = self.client.post(
            self.url, self.command(WorkOrderLifecycle.VERIFYING), format='json'
        )
        self.assertEqual(verified.status_code, 200, verified.data)
        self.packet.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.EXECUTING)
        self.assertEqual(
            self.work_order.events.filter(event_type='TRANSITION').count(), 4
        )

    def test_readiness_defaults_to_current_intermediate_action(self):
        """Draft and Planned Work preview their corresponding next commands."""
        for work_status, action in (
            (WorkOrderLifecycle.DRAFT, 'plan'),
            (WorkOrderLifecycle.PLANNED, 'mark_ready'),
        ):
            self.set_states(PacketStatus.DIAGNOSED, work_status)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['action'], action)
            self.assertEqual(
                response.data['lifecycle_version'], self.work_order.lifecycle_version
            )
        self.assertFalse(
            WorkOrderCommand.objects.filter(work_order=self.work_order).exists()
        )

    def test_preparation_allowed_in_each_preexecution_packet_state(self):
        """Draft, Diagnosed and Approved packets retain native plan/ready edges."""
        for packet_status in (
            PacketStatus.DRAFT,
            PacketStatus.DIAGNOSED,
            PacketStatus.APPROVED,
        ):
            for work_status, target in (
                (WorkOrderLifecycle.DRAFT, WorkOrderLifecycle.PLANNED),
                (WorkOrderLifecycle.PLANNED, WorkOrderLifecycle.READY),
            ):
                with self.subTest(packet=packet_status, target=target):
                    self.set_states(packet_status, work_status)
                    response = self.client.post(
                        self.url, self.command(target), format='json'
                    )
                    self.assertEqual(response.status_code, 200, response.data)
                    self.work_order.refresh_from_db()

    def test_stale_version_and_illegal_work_edge_refused(self):
        """The adapter neither guesses a version nor skips Draft to Ready."""
        response = self.client.post(
            self.url,
            self.command(expected_version=self.work_order.lifecycle_version + 1),
            format='json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'STALE_VERSION')
        response = self.client.post(
            self.url, self.command(WorkOrderLifecycle.READY), format='json'
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'ILLEGAL_TRANSITION')
        self.assertFalse(self.work_order.events.exists())

    def test_required_command_fields_and_invalid_values(self):
        """Both concurrency tokens are mandatory and use native validation."""
        for field in ('expected_version', 'idempotency_key'):
            command = self.command()
            del command[field]
            response = self.client.post(self.url, command, format='json')
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.data)
        for field, value in (
            ('expected_version', True),
            ('expected_version', 0),
            ('idempotency_key', ''),
            ('idempotency_key', 'operation' * 20),
        ):
            response = self.client.post(
                self.url, self.command(**{field: value}), format='json'
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(self.work_order.events.exists())

    def test_exact_replay_after_later_work_transition(self):
        """Retries return the original native result without another event."""
        command = self.command()
        first = self.client.post(self.url, command, format='json')
        self.work_order.refresh_from_db()
        ready = self.client.post(
            self.url, self.command(WorkOrderLifecycle.READY), format='json'
        )
        self.assertEqual(ready.status_code, 200, ready.data)
        replay = self.client.post(self.url, command, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(first.data, replay.data)
        self.assertEqual(self.work_order.events.count(), 2)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.READY)

    def test_reused_key_with_changed_intent_conflicts(self):
        """Canonical idempotency rejects different intent under an existing key."""
        command = self.command()
        self.assertEqual(
            self.client.post(self.url, command, format='json').status_code, 200
        )
        command['reason'] = 'Different request'
        response = self.client.post(self.url, command, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.work_order.events.count(), 1)

    def test_transition_permission_required_before_write_or_replay(self):
        """Native change access alone cannot execute or replay Work transitions."""
        command = self.command()
        self.assertEqual(
            self.client.post(self.url, command, format='json').status_code, 200
        )
        scopes = self.actor.maintenance_scopes
        self.ruleset.can_transition_workorder = False
        self.ruleset.save()
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        self.actor.maintenance_scopes = scopes
        self.client.force_authenticate(self.actor)
        response = self.client.post(self.url, command, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        with self.assertRaises(PermissionDenied):
            services.transition_repair_packet_work(
                self.packet, actor=self.actor, **command
            )
        self.work_order.refresh_from_db()
        response = self.client.post(
            self.url, self.command(WorkOrderLifecycle.READY), format='json'
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.work_order.events.count(), 1)

    def test_machine_scope_revocation_blocks_read_write_and_replay(self):
        """Shared Work provenance cannot replace a packet's current machine grant."""
        command = self.command()
        self.assertEqual(
            self.client.post(self.url, command, format='json').status_code, 200
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.assertEqual(self.client.get(self.url, {'action': 'plan'}).status_code, 404)
        self.assertEqual(
            self.client.post(self.url, command, format='json').status_code, 404
        )
        with self.assertRaises(ScopeError):
            services.transition_repair_packet_work(
                self.packet, actor=self.actor, **command
            )
        self.assertEqual(self.work_order.events.count(), 1)

    def test_packet_work_machine_mismatch_blocks_replay(self):
        """Even previously successful commands require consistent current ownership."""
        command = self.command()
        self.assertEqual(
            self.client.post(self.url, command, format='json').status_code, 200
        )
        other_machine = AssetMachine.objects.create(
            name='Other fixture', client=self.tenant
        )
        RepairPacket.objects.filter(pk=self.packet.pk).update(machine=other_machine)
        self.assertEqual(self.client.get(self.url, {'action': 'plan'}).status_code, 404)
        self.assertEqual(
            self.client.post(self.url, command, format='json').status_code, 404
        )
        with self.assertRaises(ScopeError):
            services.transition_repair_packet_work(
                self.packet, actor=self.actor, **command
            )

    def test_packet_states_restrict_targets_and_readiness(self):
        """Verification requires Executing; preparation requires a preexecution packet."""
        for packet_status, work_status, action, target in (
            (
                PacketStatus.APPROVED,
                WorkOrderLifecycle.IN_PROGRESS,
                'verify',
                WorkOrderLifecycle.VERIFYING,
            ),
            (
                PacketStatus.EXECUTING,
                WorkOrderLifecycle.DRAFT,
                'plan',
                WorkOrderLifecycle.PLANNED,
            ),
            (
                PacketStatus.CLOSED,
                WorkOrderLifecycle.PLANNED,
                'mark_ready',
                WorkOrderLifecycle.READY,
            ),
            (
                PacketStatus.CANCELED,
                WorkOrderLifecycle.DRAFT,
                'plan',
                WorkOrderLifecycle.PLANNED,
            ),
        ):
            with self.subTest(packet=packet_status, action=action):
                self.set_states(packet_status, work_status)
                response = self.client.get(self.url, {'action': action})
                self.assertEqual(response.status_code, 400)
                response = self.client.post(
                    self.url, self.command(target), format='json'
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.data['code'], 'REPAIR_WORK_TRANSITION_INVALID'
                )
        self.assertFalse(self.work_order.events.exists())

    def test_adapter_cannot_start_complete_hold_cancel_or_claim_authority(self):
        """A supplied finalization value never expands the narrow target allowlist."""
        for target in (
            WorkOrderLifecycle.IN_PROGRESS,
            WorkOrderLifecycle.COMPLETED,
            WorkOrderLifecycle.ON_HOLD,
            WorkOrderLifecycle.CANCELED,
            WorkOrderLifecycle.DRAFT,
        ):
            response = self.client.post(
                self.url,
                self.command(target, packet_finalization={'packet_id': self.packet.pk}),
                format='json',
            )
            self.assertEqual(response.status_code, 400, response.data)
        for action in ('start', 'complete', 'cancel', 'unknown'):
            self.assertEqual(
                self.client.get(self.url, {'action': action}).status_code, 400
            )
        self.assertFalse(self.work_order.events.exists())

    def test_standalone_work_command_still_refuses_packet_ownership(self):
        """The owning adapter leaves standalone lifecycle authority unchanged."""
        with self.assertRaises(CommandConflict) as caught:
            transition_work_order(
                work_order_id=self.work_order.pk, actor=self.actor, **self.command()
            )
        self.assertEqual(caught.exception.code, 'PACKET_OWNS_LIFECYCLE')

    def test_request_cannot_redirect_work_or_supply_finalization_authority(self):
        """Only the scoped URL packet selects Work and mints its capability."""
        other_work = WorkOrder.objects.create(
            title='Other packet Work', customer=self.customer, machine=self.machine
        )
        other_packet = RepairPacket.objects.create(
            machine=self.machine, work_order=other_work
        )
        response = self.client.post(
            self.url,
            self.command(
                work_order_id=other_work.pk,
                packet_finalization={'packet_id': other_packet.pk},
            ),
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['work_order_id'], self.work_order.pk)
        other_work.refresh_from_db()
        self.assertEqual(other_work.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertFalse(other_work.events.exists())

    def test_unlinked_packet_has_no_transition(self):
        """An orphan packet cannot invent or select another Work order."""
        orphan = RepairPacket.objects.create(machine=self.machine)
        url = f'/api/repair/packets/{orphan.pk}/work-transition/'
        self.assertEqual(self.client.get(url).status_code, 400)
        response = self.client.post(url, self.command(), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.work_order.events.exists())

    def test_pending_safety_gate_still_blocks_native_start_and_legacy_advance(self):
        """Planning does not grant execution or satisfy any physical safety gate."""
        self.set_states(PacketStatus.APPROVED, WorkOrderLifecycle.READY)
        gate = RepairPacketGate.objects.create(
            packet=self.packet,
            name='Pending fixture review',
            gate_type='permit',
            status=GateStatus.PENDING,
            is_blocking=True,
        )
        readiness = services.repair_start_readiness(self.packet, actor=self.actor)
        self.assertFalse(readiness['ready'])
        self.assertIn(
            'SAFETY_GATE_BLOCKED', {item['code'] for item in readiness['blockers']}
        )
        with self.assertRaises(services.RepairStartError):
            services.start_repair_packet(
                self.packet,
                actor=self.actor,
                **{
                    key: value
                    for key, value in self.command().items()
                    if key != 'to_status'
                },
            )
        response = self.client.post(
            f'/api/repair/packets/{self.packet.pk}/advance/',
            {'to': PacketStatus.EXECUTING},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.packet.refresh_from_db()
        self.work_order.refresh_from_db()
        gate.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.APPROVED)
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.READY)
        self.assertEqual(gate.status, GateStatus.PENDING)

    def test_legacy_advance_cannot_diverge_even_without_gate_blockers(self):
        """The former bare execution path requires native Start repair instead."""
        self.set_states(PacketStatus.APPROVED, WorkOrderLifecycle.READY)
        advanced, detail = services.advance_packet(
            self.packet, PacketStatus.EXECUTING, self.actor
        )
        self.assertFalse(advanced)
        self.assertIn('Start repair', detail)
        self.packet.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.APPROVED)
        self.assertFalse(self.work_order.events.exists())

    def test_incomplete_procedure_still_blocks_verification(self):
        """The canonical procedure blocker survives the owning-packet capability."""
        self.set_states(PacketStatus.EXECUTING, WorkOrderLifecycle.IN_PROGRESS)
        procedure = Procedure.objects.create(
            code='packet-fixture', name='Required fixture checks', created_by=self.actor
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=1,
            work_order_type=WorkOrderType.CORRECTIVE,
            created_by=self.actor,
        )
        application = WorkOrderProcedureApplication.objects.create(
            work_order=self.work_order,
            revision=revision,
            snapshot={},
            snapshot_hash='fixture',
            applied_by=self.actor,
            idempotency_key='fixture',
        )
        WorkOrderStepExecution.objects.create(
            application=application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={'required': True},
        )
        preview = self.client.get(self.url, {'action': 'verify'})
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertFalse(preview.data['ready'])
        response = self.client.post(
            self.url, self.command(WorkOrderLifecycle.VERIFYING), format='json'
        )
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'READINESS_BLOCKED')
        self.assertEqual(preview.data['blockers'], response.data['blockers'])
        self.work_order.refresh_from_db()
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )
        self.assertFalse(self.work_order.events.exists())
