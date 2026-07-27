"""Tests for Start repair: a readiness-gated transition, never a board edit.

The rule these pin is that safety keeps precedence. Starting a repair moves the
packet and its work order together, and every readiness category can stop it -
there is no path from the machine page that reaches around the checks.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from tasks.models import KanbanCard, WorkOrderLifecycle, WorkOrderType
from tasks.scope import MaintenanceScope
from tasks.services.work_orders import IllegalTransition

from assets.models import AssetMachine
from company.models import Company
from InvenTree.unit_test import InvenTreeAPITestCase

from . import services
from .models import (
    GateStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    RepairPacketGate,
)

_GRANTED_CUSTOMER_IDS: list[int] = []


def _scope_resolver(actor):
    """Return the customer scopes granted to the current test actor."""
    return {
        MaintenanceScope(customer_id=customer_id, site_key=None)
        for customer_id in _GRANTED_CUSTOMER_IDS
    }


class StartRepairTest(TestCase):
    """The packet-owned start command."""

    def setUp(self):
        """Create a scoped actor, machine, ready work order and approved packet."""
        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(name=f'Start {suffix}', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username=f'starter-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.machine = AssetMachine.objects.create(
            name=f'Blower {suffix}', customer=self.customer
        )
        self.work_order = KanbanCard.objects.create(
            title='Bearing replacement',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_HIGH,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.CORRECTIVE,
            lifecycle_status=WorkOrderLifecycle.READY,
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            fault_summary='Bearing temperature rising',
            status=PacketStatus.APPROVED,
        )

    def start(self, key='start-1'):
        """Start the repair through the shared service."""
        return services.start_repair_packet(
            self.packet,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_start_moves_both_aggregates(self):
        """One command puts the packet and its work order into execution."""
        result = self.start()

        self.assertEqual(result.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS)

        self.packet.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.EXECUTING)
        self.assertEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )
        self.assertIsNotNone(self.work_order.actual_started_at)

    def test_replay_does_not_transition_twice(self):
        """A retry returns the original result and records one event."""
        first = self.start(key='same')
        replay = self.start(key='same')

        self.assertEqual(first.event_id, replay.event_id)
        self.assertEqual(self.packet.events.filter(event_type='advanced').count(), 1)

    def test_unrestored_lockout_blocks_the_start(self):
        """Safety keeps precedence over everything else."""
        gate = RepairPacketGate.objects.create(
            packet=self.packet,
            name='LOTO',
            gate_type='loto',
            status=GateStatus.PENDING,
            is_blocking=True,
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='MCC-2 bucket',
            status=LockoutPoint.PointStatus.VERIFIED,
        )

        with self.assertRaises(services.RepairStartError):
            self.start()

        self.packet.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.APPROVED)
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.READY)

    def test_work_order_that_is_not_ready_cannot_start(self):
        """A draft work order is reported, not silently advanced."""
        self.work_order.lifecycle_status = WorkOrderLifecycle.DRAFT
        self.work_order.save(update_fields=['lifecycle_status'])

        with self.assertRaises(IllegalTransition):
            self.start()

        self.packet.refresh_from_db()
        self.assertEqual(self.packet.status, PacketStatus.APPROVED)

    def test_packet_without_a_work_order_cannot_start(self):
        """There is nothing to transition, and no card is invented."""
        orphan = RepairPacket.objects.create(
            machine=self.machine, fault_summary='Unlinked', status=PacketStatus.APPROVED
        )

        with self.assertRaisesMessage(services.RepairStartError, 'no work order'):
            services.start_repair_packet(
                orphan,
                actor=self.actor,
                expected_version=1,
                idempotency_key=uuid.uuid4().hex,
            )

    def test_readiness_read_explains_each_blocker(self):
        """Review blockers shows why, not just that it is blocked."""
        self.work_order.lifecycle_status = WorkOrderLifecycle.DRAFT
        self.work_order.save(update_fields=['lifecycle_status'])

        readiness = services.repair_start_readiness(self.packet, actor=self.actor)

        self.assertFalse(readiness['ready'])
        codes = {blocker['code'] for blocker in readiness['blockers']}
        self.assertIn('WORK_ORDER_NOT_READY', codes)
        self.assertEqual(readiness['work_order_id'], self.work_order.pk)

    def test_ready_repair_reports_no_blockers(self):
        """A startable repair says so, with the version the caller must echo."""
        readiness = services.repair_start_readiness(self.packet, actor=self.actor)

        self.assertTrue(readiness['ready'])
        self.assertEqual(readiness['blockers'], [])
        self.assertEqual(
            readiness['lifecycle_version'], self.work_order.lifecycle_version
        )


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_scope_resolver)
class StartRepairApiTest(InvenTreeAPITestCase):
    """HTTP contract for start readiness, starting and the machine chooser."""

    roles = ['work_order.view', 'work_order.change']
    superuser = True

    def setUp(self):
        """Create a machine with one ready, startable repair."""
        super().setUp()
        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(
            name=f'Start API {suffix}', is_customer=True
        )
        _GRANTED_CUSTOMER_IDS[:] = [self.customer.pk]
        self.addCleanup(_GRANTED_CUSTOMER_IDS.clear)

        self.machine = AssetMachine.objects.create(
            name=f'Screen {suffix}', customer=self.customer
        )
        self.work_order = KanbanCard.objects.create(
            title='Chain replacement',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_HIGH,
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.user,
            work_order_type=WorkOrderType.CORRECTIVE,
            lifecycle_status=WorkOrderLifecycle.READY,
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            fault_summary='Rake chain sag',
            status=PacketStatus.APPROVED,
        )
        self.url = f'/api/repair/packets/{self.packet.pk}/start/'

    def test_readiness_endpoint_reports_startability(self):
        """The UI can ask before offering the action."""
        response = self.get(self.url, expected_code=200)

        self.assertTrue(response.data['ready'])
        self.assertEqual(response.data['work_order_id'], self.work_order.pk)

    def test_start_requires_an_expected_version(self):
        """Starting is version-checked like any governed transition."""
        response = self.post(self.url, {}, expected_code=400)
        self.assertEqual(response.data['code'], 'EXPECTED_VERSION_REQUIRED')

    def test_start_transitions_the_repair(self):
        """A ready repair starts and reports its new lifecycle state."""
        response = self.post(
            self.url,
            {'expected_version': self.work_order.lifecycle_version},
            expected_code=200,
        )

        self.assertTrue(response.data['ok'])
        self.assertEqual(response.data['status'], PacketStatus.EXECUTING)
        self.assertEqual(
            response.data['lifecycle_status'], WorkOrderLifecycle.IN_PROGRESS
        )

    def test_open_repairs_lists_candidates_with_readiness(self):
        """The chooser gets everything it needs to avoid guessing."""
        response = self.get(
            f'/api/repair/machines/{self.machine.pk}/open-repairs/', expected_code=200
        )

        self.assertEqual(response.data['count'], 1)
        [row] = response.data['results']
        self.assertEqual(row['packet_id'], self.packet.pk)
        self.assertEqual(row['work_order_title'], 'Chain replacement')
        self.assertTrue(row['ready'])

    def test_closed_repairs_are_not_offered(self):
        """Only work that can still be started appears in the chooser."""
        self.packet.status = PacketStatus.CLOSED
        self.packet.save(update_fields=['status'])

        response = self.get(
            f'/api/repair/machines/{self.machine.pk}/open-repairs/', expected_code=200
        )
        self.assertEqual(response.data['count'], 0)
