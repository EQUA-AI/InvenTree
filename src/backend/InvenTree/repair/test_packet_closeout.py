"""Tests for packet-owned repair finalization.

Standalone and Repair Packet-owned work must reach the *same* completion path.
These tests pin that: closing a packet that owns a work order cannot bypass
structured closeout, the terminal work-order transition or the machine
maintenance-history row, and the two aggregates commit together or not at all.
"""

import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from tasks.models import (
    KanbanColumn,
    WorkOrder,
    WorkOrderCloseout,
    WorkOrderLifecycle,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope, ScopeError
from tasks.services.closeout import CloseoutError

from assets.models import AssetMachine, AssetMaintenanceRecord, Client
from company.models import Company
from InvenTree.unit_test import InvenTreeAPITestCase

from . import services
from .models import (
    GateStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    RepairPacketEvent,
    RepairPacketGate,
)

VALID_CLOSEOUT = {
    'action': 'Replaced the mechanical seal and wear ring',
    'result': 'Vibration returned to baseline',
    'verification_summary': 'Two-hour stable run verified at rated flow',
    'cause': 'Seal face wear',
    'downtime_minutes': 180,
}

# The API request user is reloaded per request, so scope is supplied through the
# deployment resolver hook rather than an attribute on the in-memory actor.
_GRANTED_CUSTOMER_IDS: list[int] = []
_GRANTED_CLIENT_IDS: list[int] = []


def _scope_resolver(actor):
    """Return independent Work provenance and machine grants for this actor."""
    return {
        MaintenanceScope(customer_id=customer_id, site_key=None)
        for customer_id in _GRANTED_CUSTOMER_IDS
    } | {
        MaintenanceScope(customer_id=None, site_key=None, client_id=client_id)
        for client_id in _GRANTED_CLIENT_IDS
    }


class PacketCloseoutTest(TestCase):
    """The packet-owned finalization service."""

    def setUp(self):
        """Create a scoped actor, machine, work order and executing packet."""
        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(
            name=f'Packet closeout {suffix}', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username=f'packet-closer-{suffix}',
            email=f'{suffix}@example.com',
            password='pw',
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.tenant = Client.objects.create(
            name='Packet closeout client', code=f'packet-closeout-{suffix}'
        )
        self.actor.maintenance_scopes.add(
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        )
        self.machine = AssetMachine.objects.create(
            name=f'Pump {suffix}', client=self.tenant
        )
        self.work_order = WorkOrder.objects.create(
            title='Seal and wear-ring repair',
            status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_HIGH,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.CORRECTIVE,
            lifecycle_status=WorkOrderLifecycle.VERIFYING,
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            fault_summary='Rising vibration on the drive end',
            status=PacketStatus.EXECUTING,
        )

    def close(self, key='close-1', closeout=None):
        """Finalize the packet through the shared closeout service."""
        return services.close_repair_packet(
            self.packet,
            actor=self.actor,
            closeout=closeout or VALID_CLOSEOUT,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_closing_writes_one_closeout_and_one_history_row(self):
        """One command closes the packet and completes its work order."""
        result = self.close()

        self.assertEqual(result.lifecycle_status, WorkOrderLifecycle.COMPLETED)

        self.packet.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.CLOSED)
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.COMPLETED)
        self.assertEqual(self.work_order.status, KanbanColumn.terminal_key())
        self.assertIsNotNone(self.work_order.actual_completed_at)

        closeout = WorkOrderCloseout.objects.get(work_order=self.work_order)
        self.assertEqual(closeout.downtime_minutes, 180)

        record = AssetMaintenanceRecord.objects.get(work_order=self.work_order)
        self.assertEqual(record.machine, self.machine)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 1)

        self.assertTrue(
            self.packet.events.filter(event_type='return_to_service').exists()
        )

    def test_replay_creates_no_second_record(self):
        """Retrying finalization returns the original result and writes nothing."""
        first = self.close(key='same')
        replay = self.close(key='same')

        self.assertEqual(first.event_id, replay.event_id)
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 1)
        self.assertEqual(
            self.packet.events.filter(
                event_type=RepairPacketEvent.EventType.RETURN_TO_SERVICE
            ).count(),
            1,
        )

    def test_shared_work_cannot_authorize_close_or_readiness_after_machine_revocation(
        self,
    ):
        """Packet completion and replay still require current machine scope."""
        self.close(key='same')
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        with self.assertRaises(ScopeError):
            self.close(key='same')
        with self.assertRaises(ScopeError):
            services.packet_close_readiness(self.packet, actor=self.actor)
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 1)

    def test_safety_approval_requires_machine_scope_beyond_shared_work(self):
        """An approval cannot turn shared customer Work into physical authority."""
        from approvals.executors import SafetyGateExecutor

        gate = RepairPacketGate.objects.create(
            packet=self.packet, name='O24 permit', gate_type='permit'
        )
        approval = SimpleNamespace(payload={'gate_id': gate.pk, 'action': 'confirm'})
        execution = SimpleNamespace(idempotency_key='o24-safety')
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        with self.assertRaises(ScopeError):
            SafetyGateExecutor().execute_for_approval(
                approval, actor=self.actor, execution=execution
            )
        gate.refresh_from_db()
        self.assertEqual(gate.status, GateStatus.PENDING)
        self.assertFalse(self.packet.events.exists())
        self.actor.maintenance_scopes.add(
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        )
        result = SafetyGateExecutor().execute_for_approval(
            approval, actor=self.actor, execution=execution
        )
        self.assertTrue(result.success)
        gate.refresh_from_db()
        self.assertEqual(gate.status, GateStatus.CONFIRMED)

    def test_advance_cannot_close_a_packet_that_owns_a_work_order(self):
        """The old direct transition no longer bypasses structured closeout."""
        ok, detail = services.advance_packet(
            self.packet, PacketStatus.CLOSED, self.actor
        )

        self.assertFalse(ok)
        self.assertIn('owns a work order', detail)

        self.packet.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.EXECUTING)
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.VERIFYING)
        self.assertFalse(AssetMaintenanceRecord.objects.exists())

    def test_unrestored_lockout_blocks_finalization(self):
        """Safety keeps precedence: an active lockout point blocks the close."""
        gate = RepairPacketGate.objects.create(
            packet=self.packet,
            name='LOTO',
            gate_type='loto',
            status=GateStatus.CONFIRMED,
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='MCC-1 bucket',
            status=LockoutPoint.PointStatus.VERIFIED,
        )

        with self.assertRaisesMessage(services.RepairCloseoutError, 'not restored'):
            self.close()

        self.packet.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.EXECUTING)
        self.assertFalse(WorkOrderCloseout.objects.exists())

    def test_incomplete_closeout_leaves_both_aggregates_untouched(self):
        """A rejected closeout rolls back the packet transition too."""
        with self.assertRaises(CloseoutError):
            self.close(closeout={'action': 'only an action'})

        self.packet.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.EXECUTING)
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.VERIFYING)
        self.assertFalse(AssetMaintenanceRecord.objects.exists())

    def test_packet_without_a_work_order_cannot_be_finalized(self):
        """A packet with no work order cannot create maintenance history."""
        orphan = RepairPacket.objects.create(
            machine=self.machine,
            fault_summary='No linked work order',
            status=PacketStatus.EXECUTING,
        )

        with self.assertRaisesMessage(
            services.RepairCloseoutError, 'has no work order'
        ):
            services.close_repair_packet(
                orphan,
                actor=self.actor,
                closeout=VALID_CLOSEOUT,
                expected_version=1,
                idempotency_key=uuid.uuid4().hex,
            )

    def test_history_write_failure_rolls_back_the_packet_transition(self):
        """Nothing is committed if the maintenance-history write fails."""
        with mock.patch(
            'tasks.services.closeout.AssetMaintenanceRecord.objects.update_or_create',
            side_effect=RuntimeError('history unavailable'),
        ):
            with self.assertRaises(RuntimeError):
                self.close()

        self.packet.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.EXECUTING)
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.VERIFYING)
        self.assertFalse(WorkOrderCloseout.objects.exists())
        self.assertFalse(AssetMaintenanceRecord.objects.exists())


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_scope_resolver)
class PacketCloseoutApiTest(InvenTreeAPITestCase):
    """HTTP contract for the packet close endpoint."""

    roles = ['work_order.view', 'work_order.change']
    superuser = True

    def setUp(self):
        """Create a machine, work order and executing packet for the actor."""
        super().setUp()
        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(
            name=f'Packet API {suffix}', is_customer=True
        )
        _GRANTED_CUSTOMER_IDS[:] = [self.customer.pk]
        self.addCleanup(_GRANTED_CUSTOMER_IDS.clear)
        self.tenant = Client.objects.create(
            name='Packet API client', code=f'packet-api-{suffix}'
        )
        _GRANTED_CLIENT_IDS[:] = [self.tenant.pk]
        self.addCleanup(_GRANTED_CLIENT_IDS.clear)
        self.machine = AssetMachine.objects.create(
            name=f'Blower {suffix}', client=self.tenant
        )
        self.work_order = WorkOrder.objects.create(
            title='Bearing replacement',
            status=WorkOrder.STATUS_REVIEW,
            priority=WorkOrder.PRIORITY_HIGH,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.user,
            work_order_type=WorkOrderType.CORRECTIVE,
            lifecycle_status=WorkOrderLifecycle.VERIFYING,
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            fault_summary='Bearing temperature rising',
            status=PacketStatus.EXECUTING,
        )
        self.url = f'/api/repair/packets/{self.packet.pk}/close/'

    def test_close_requires_an_expected_version(self):
        """Finalization is version-checked like any other governed transition."""
        response = self.post(self.url, {'closeout': VALID_CLOSEOUT}, expected_code=400)
        self.assertEqual(response.data['code'], 'EXPECTED_VERSION_REQUIRED')

    def test_close_completes_the_work_order(self):
        """A valid close returns the terminal state and links the work order."""
        response = self.post(
            self.url,
            {
                'expected_version': self.work_order.lifecycle_version,
                'closeout': VALID_CLOSEOUT,
            },
            expected_code=200,
        )

        self.assertTrue(response.data['ok'])
        self.assertEqual(response.data['status'], PacketStatus.CLOSED)
        self.assertEqual(response.data['work_order_id'], self.work_order.pk)
        self.assertEqual(
            response.data['lifecycle_status'], WorkOrderLifecycle.COMPLETED
        )
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(work_order=self.work_order).exists()
        )
