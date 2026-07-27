"""Tests for the canonical repair work-package draft and create command.

Every maintenance intake path converges on ``create_repair_work_package``, so
these tests pin the contract the manual button, the anomaly action and the AI
proposal all inherit: the draft is validated server-side, the work order and
packet always share one machine, creation is atomic and replay-safe, and the
command plans work rather than starting it.
"""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import WorkOrder, WorkOrderLifecycle, WorkOrderPart, WorkOrderType

from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part

from .models import PacketStatus, RepairPacket
from .work_packages import (
    DRAFT_SCHEMA_VERSION,
    MAX_PARTS,
    UnknownMachine,
    UnknownPart,
    WorkPackageError,
    create_repair_work_package,
    validate_draft,
)


def _draft(**overrides):
    """Return a minimally valid work-package draft."""
    draft = {
        'schema_version': DRAFT_SCHEMA_VERSION,
        'machine_id': 1,
        'title': 'Investigate high motor vibration',
        'origin': 'manual',
    }
    draft.update(overrides)
    return draft


class DraftValidationTest(TestCase):
    """Server-side validation of the versioned draft schema."""

    def test_unsupported_schema_version_is_rejected(self):
        """A draft from an unknown schema version never reaches the command."""
        with self.assertRaisesMessage(WorkPackageError, 'Unsupported work package'):
            validate_draft(_draft(schema_version=99))

    def test_machine_and_title_are_required(self):
        """A work package is always anchored to a machine and has a title."""
        with self.assertRaisesMessage(WorkPackageError, 'integer machine_id'):
            validate_draft(_draft(machine_id=None))
        with self.assertRaisesMessage(WorkPackageError, 'requires a title'):
            validate_draft(_draft(title='   '))

    def test_unknown_enum_values_are_rejected(self):
        """Priority, type, origin and criticality come from closed vocabularies."""
        with self.assertRaises(WorkPackageError):
            validate_draft(_draft(priority='urgent'))
        with self.assertRaises(WorkPackageError):
            validate_draft(_draft(work_order_type='emergency'))
        with self.assertRaises(WorkPackageError):
            validate_draft(_draft(origin='api'))
        with self.assertRaises(WorkPackageError):
            validate_draft(_draft(fault={'criticality': 'catastrophic'}))

    def test_packet_defaults_follow_work_order_type(self):
        """Corrective work gets a packet; planning work does not."""
        self.assertTrue(validate_draft(_draft())['create_repair_packet'])
        self.assertFalse(
            validate_draft(_draft(work_order_type='preventive'))['create_repair_packet']
        )
        # An explicit choice always wins over the default.
        self.assertTrue(
            validate_draft(
                _draft(work_order_type='preventive', create_repair_packet=True)
            )['create_repair_packet']
        )

    def test_part_lines_are_bounded_and_deduplicated(self):
        """Part lines are positive, unique and capped."""
        with self.assertRaisesMessage(WorkPackageError, 'must be positive'):
            validate_draft(_draft(parts=[{'part_id': 3, 'quantity': 0}]))
        with self.assertRaisesMessage(WorkPackageError, 'more than once'):
            validate_draft(
                _draft(parts=[{'part_id': 3}, {'part_id': 3, 'quantity': 2}])
            )
        with self.assertRaisesMessage(WorkPackageError, 'at most'):
            validate_draft(_draft(parts=[{'part_id': n} for n in range(MAX_PARTS + 1)]))

    def test_valid_draft_is_normalized(self):
        """A valid draft normalizes into the canonical shape."""
        result = validate_draft(
            _draft(
                description='  Operator-visible scope  ',
                fault={'summary': 'Vibration rising', 'criticality': 'high'},
                parts=[{'part_id': 7, 'quantity': '2.5', 'reason': 'seal kit'}],
                planning={'assignee': 'J. Rivera', 'estimated_minutes': 240},
            )
        )

        self.assertEqual(result['description'], 'Operator-visible scope')
        self.assertEqual(result['fault']['criticality'], 'high')
        self.assertEqual(result['parts'][0]['quantity'], Decimal('2.5'))
        self.assertEqual(result['planning']['estimated_minutes'], 240)
        self.assertEqual(result['work_order_type'], WorkOrderType.CORRECTIVE)


class CreateWorkPackageTest(TestCase):
    """The atomic create command."""

    def setUp(self):
        """Create an actor, a machine and a stockable part."""
        self.actor = get_user_model().objects.create_superuser(
            username=f'planner-{uuid.uuid4().hex[:8]}',
            email='planner@example.com',
            password='test-password',
        )
        self.machine = AssetMachine.objects.create(
            name=f'Blower-{uuid.uuid4().hex[:6]}'
        )
        self.part = Part.objects.create(
            name=f'Seal-{uuid.uuid4().hex[:6]}',
            description='Mechanical seal',
            component=True,
            purchaseable=True,
        )

    def _create(self, **overrides):
        return create_repair_work_package(
            actor=self.actor,
            draft=_draft(machine_id=self.machine.pk, **overrides),
            idempotency_key=overrides.pop('idempotency_key', uuid.uuid4().hex),
        )

    def test_creates_linked_work_order_and_packet(self):
        """One command produces a machine-linked work order plus packet."""
        result = self._create(
            fault={
                'summary': 'Vibration above alert limit',
                'symptom': 'Rising 1x amplitude',
                'criticality': 'high',
            },
            parts=[{'part_id': self.part.pk, 'quantity': 2}],
        )

        work_order = WorkOrder.objects.get(pk=result.work_order_id)
        packet = RepairPacket.objects.get(pk=result.repair_packet_id)

        self.assertEqual(work_order.machine_id, self.machine.pk)
        self.assertEqual(packet.machine_id, self.machine.pk)
        self.assertEqual(packet.work_order_id, work_order.pk)
        self.assertEqual(packet.status, PacketStatus.DRAFT)
        self.assertEqual(packet.criticality, 'high')
        self.assertTrue(result.work_order_reference)
        self.assertTrue(result.repair_packet_reference.startswith('RP-'))
        self.assertEqual(
            WorkOrderPart.objects.filter(work_order=work_order, part=self.part).count(),
            1,
        )
        self.assertTrue(work_order.events.filter(event_type='CREATED').exists())

    def test_creates_a_planned_work_order_not_a_started_one(self):
        """Create repair plans work; starting it is a separate transition."""
        result = self._create()
        work_order = WorkOrder.objects.get(pk=result.work_order_id)

        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(work_order.status, WorkOrder.STATUS_BACKLOG)
        self.assertIsNone(work_order.actual_started_at)

    def test_replay_returns_the_same_aggregate(self):
        """A retry with the same idempotency key creates nothing new."""
        key = uuid.uuid4().hex
        draft = _draft(machine_id=self.machine.pk)

        first = create_repair_work_package(
            actor=self.actor, draft=draft, idempotency_key=key
        )
        second = create_repair_work_package(
            actor=self.actor, draft=draft, idempotency_key=key
        )

        self.assertEqual(first.work_order_id, second.work_order_id)
        self.assertTrue(second.replayed)
        self.assertEqual(WorkOrder.objects.filter(machine=self.machine).count(), 1)
        self.assertEqual(RepairPacket.objects.filter(machine=self.machine).count(), 1)
        self.assertIn('already existed', ' '.join(second.warnings))

    def test_unknown_machine_creates_nothing(self):
        """An unknown machine fails before any write."""
        before = WorkOrder.objects.count()
        with self.assertRaises(UnknownMachine):
            create_repair_work_package(
                actor=self.actor,
                draft=_draft(machine_id=987654),
                idempotency_key=uuid.uuid4().hex,
            )
        self.assertEqual(WorkOrder.objects.count(), before)

    def test_unknown_part_rolls_back_the_whole_package(self):
        """A bad part line leaves no work order and no packet behind."""
        before_cards = WorkOrder.objects.count()
        before_packets = RepairPacket.objects.count()

        with self.assertRaises(UnknownPart):
            self._create(parts=[{'part_id': 987654, 'quantity': 1}])

        self.assertEqual(WorkOrder.objects.count(), before_cards)
        self.assertEqual(RepairPacket.objects.count(), before_packets)

    def test_no_packet_when_not_requested(self):
        """Planning work can be created without a fault-to-fix aggregate."""
        result = self._create(work_order_type='preventive')

        self.assertIsNone(result.repair_packet_id)
        self.assertEqual(result.repair_packet_reference, '')
        self.assertFalse(
            RepairPacket.objects.filter(work_order_id=result.work_order_id).exists()
        )


class WorkPackageApiTest(InvenTreeAPITestCase):
    """HTTP contract for the maintenance work-package endpoint."""

    # Creating a work package requires work-order add authority, not merely
    # read access to the workspace.
    roles = ['work_order.view', 'work_order.add']
    url = '/api/maintenance/work-packages/create/'

    def setUp(self):
        """Create the machine the drafts refer to."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name=f'Clarifier-{uuid.uuid4().hex[:6]}'
        )

    def test_create_returns_both_references_and_replay_flag(self):
        """The endpoint returns the created aggregate and its replay state."""
        key = uuid.uuid4().hex
        payload = {
            'machine_id': self.machine.pk,
            'title': 'Scraper drive overload',
            'idempotency_key': key,
            'fault': {'summary': 'Torque alarm at 82%', 'criticality': 'high'},
        }

        created = self.post(self.url, payload, expected_code=201).data
        self.assertTrue(created['work_order_reference'])
        self.assertTrue(created['repair_packet_reference'])
        self.assertFalse(created['replayed'])

        replayed = self.post(self.url, payload, expected_code=201).data
        self.assertEqual(replayed['work_order_id'], created['work_order_id'])
        self.assertTrue(replayed['replayed'])

    def test_invalid_draft_returns_a_stable_error_code(self):
        """A rejected draft reports why, with a machine-readable code."""
        response = self.post(
            self.url, {'machine_id': self.machine.pk}, expected_code=400
        )
        self.assertEqual(response.data['code'], 'WORK_PACKAGE_INVALID')

    def test_unknown_machine_returns_its_own_code(self):
        """An unresolvable machine is distinguishable from a malformed draft."""
        response = self.post(
            self.url, {'machine_id': 987654, 'title': 'Ghost asset'}, expected_code=400
        )
        self.assertEqual(response.data['code'], 'UNKNOWN_MACHINE')


class WorkPackagePermissionTest(InvenTreeAPITestCase):
    """Read access to the workspace does not authorize creating work."""

    roles = ['work_order.view']
    url = '/api/maintenance/work-packages/create/'

    def test_view_only_actor_cannot_create_a_work_package(self):
        """Without work_order.add the request is refused and nothing is written."""
        machine = AssetMachine.objects.create(name=f'Screen-{uuid.uuid4().hex[:6]}')
        before = WorkOrder.objects.count()

        self.post(
            self.url,
            {'machine_id': machine.pk, 'title': 'Unauthorized intake'},
            expected_code=403,
        )

        self.assertEqual(WorkOrder.objects.count(), before)
