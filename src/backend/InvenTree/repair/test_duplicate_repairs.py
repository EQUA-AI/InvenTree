"""Tests for duplicate open-repair control on the work-package command.

Two work orders for one fault split the technician's attention, the parts
reservation and the audit trail. The server checks before creating; proceeding
anyway is possible but must be deliberate and attributed.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import WorkOrder

from assets.health_models import AnomalySeverity
from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.services.anomalies import fingerprint_for, record_anomaly

from .models import PacketStatus, RepairPacket, RepairPacketHealthEvidence
from .work_packages import (
    DuplicateRepairConflict,
    create_repair_work_package,
    find_duplicate_repairs,
)


def _draft(machine_id, **overrides):
    draft = {
        'machine_id': machine_id,
        'title': 'Investigate rising vibration',
        'origin': 'manual',
    }
    draft.update(overrides)
    return draft


class DuplicateDetectionTest(TestCase):
    """The command refuses a second open repair unless told otherwise."""

    def setUp(self):
        """Create an actor and a machine with one open repair."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_superuser(
            username=f'dup-planner-{suffix}',
            email=f'{suffix}@example.com',
            password='pw',
        )
        self.machine = AssetMachine.objects.create(name=f'Pump {suffix}')

        self.first = create_repair_work_package(
            actor=self.actor,
            draft=_draft(self.machine.pk),
            idempotency_key=uuid.uuid4().hex,
        )

    def test_second_repair_on_the_same_machine_is_refused(self):
        """A duplicate is a conflict, and it names what already exists."""
        with self.assertRaises(DuplicateRepairConflict) as caught:
            create_repair_work_package(
                actor=self.actor,
                draft=_draft(self.machine.pk, title='Same fault again'),
                idempotency_key=uuid.uuid4().hex,
            )

        conflict = caught.exception
        self.assertEqual(conflict.code, 'DUPLICATE_OPEN_REPAIR')
        self.assertTrue(conflict.duplicates)
        self.assertEqual(
            conflict.duplicates[0]['work_order_id'], self.first.work_order_id
        )

    def test_refusal_writes_nothing(self):
        """A refused duplicate leaves no half-created work behind."""
        before_cards = WorkOrder.objects.count()
        before_packets = RepairPacket.objects.count()

        with self.assertRaises(DuplicateRepairConflict):
            create_repair_work_package(
                actor=self.actor,
                draft=_draft(self.machine.pk, title='Same fault again'),
                idempotency_key=uuid.uuid4().hex,
            )

        self.assertEqual(WorkOrder.objects.count(), before_cards)
        self.assertEqual(RepairPacket.objects.count(), before_packets)

    def test_explicit_override_creates_and_records_the_reason(self):
        """An authorized user may proceed, and the reason is kept."""
        result = create_repair_work_package(
            actor=self.actor,
            draft=_draft(
                self.machine.pk,
                title='Second, unrelated fault',
                duplicate_override=True,
                duplicate_override_reason='Different subsystem, agreed with planner',
            ),
            idempotency_key=uuid.uuid4().hex,
        )

        self.assertNotEqual(result.work_order_id, self.first.work_order_id)
        self.assertTrue(
            any('Different subsystem' in warning for warning in result.warnings)
        )

    def test_closed_repair_does_not_block_a_new_one(self):
        """Only open work counts as a duplicate."""
        packet = RepairPacket.objects.get(pk=self.first.repair_packet_id)
        packet.status = PacketStatus.CLOSED
        packet.save(update_fields=['status'])

        result = create_repair_work_package(
            actor=self.actor,
            draft=_draft(self.machine.pk, title='New fault after closeout'),
            idempotency_key=uuid.uuid4().hex,
        )

        self.assertNotEqual(result.work_order_id, self.first.work_order_id)

    def test_other_machines_are_unaffected(self):
        """A busy machine does not block work on a different asset."""
        other = AssetMachine.objects.create(name=f'Blower {uuid.uuid4().hex[:6]}')

        self.assertEqual(find_duplicate_repairs(other), [])

        result = create_repair_work_package(
            actor=self.actor, draft=_draft(other.pk), idempotency_key=uuid.uuid4().hex
        )
        self.assertTrue(result.work_order_id)

    def test_replay_is_not_treated_as_a_duplicate(self):
        """Retrying the original request must still replay, not conflict."""
        key = uuid.uuid4().hex
        machine = AssetMachine.objects.create(name=f'Screen {uuid.uuid4().hex[:6]}')
        draft = _draft(machine.pk)

        first = create_repair_work_package(
            actor=self.actor, draft=draft, idempotency_key=key
        )
        # No override: a dropped response must be recoverable by retrying the
        # original request, not only by escalating past a conflict.
        replay = create_repair_work_package(
            actor=self.actor, draft=draft, idempotency_key=key
        )

        self.assertEqual(first.work_order_id, replay.work_order_id)
        self.assertTrue(replay.replayed)


class AnomalyEvidenceLinkTest(TestCase):
    """Creating from an anomaly leaves a typed, protected evidence trail."""

    def setUp(self):
        """Create an actor, a machine, an anomaly and its mapped signal."""
        from assets.health_models import (
            HealthSource,
            MachineSignalBinding,
            MachineSignalState,
            SourceType,
        )

        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_superuser(
            username=f'anom-planner-{suffix}',
            email=f'{suffix}@example.com',
            password='pw',
        )
        self.machine = AssetMachine.objects.create(name=f'Clarifier {suffix}')
        source = HealthSource.objects.create(
            name=f'SCADA {suffix}', source_type=SourceType.SCADA
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key=f'CL-{suffix}.TRQ',
            display_name='Scraper drive torque',
            unit='%',
            warn_max=70,
        )
        from django.utils import timezone

        MachineSignalState.objects.create(
            binding=self.binding,
            value={'value': 82, 'unit': '%'},
            observed_at=timezone.now(),
        )
        self.anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('link', 'torque'),
            title='Scraper torque above limit',
            severity=AnomalySeverity.CRITICAL,
            bindings=[self.binding],
        )

    def test_creation_links_the_anomaly_and_its_evidence(self):
        """The anomaly points at the new work order and packet, with citations."""
        result = create_repair_work_package(
            actor=self.actor,
            draft=_draft(self.machine.pk, source={'anomaly_id': self.anomaly.pk}),
            idempotency_key=uuid.uuid4().hex,
        )

        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.work_order_id, result.work_order_id)
        self.assertEqual(self.anomaly.repair_packet_id, result.repair_packet_id)

        links = RepairPacketHealthEvidence.objects.filter(
            packet_id=result.repair_packet_id
        )
        self.assertEqual(links.count(), 1)
        self.assertEqual(links.first().snapshot.binding_id, self.binding.pk)

    def test_evidence_link_is_immutable(self):
        """A citation records what was seen; it is not an editable note."""
        result = create_repair_work_package(
            actor=self.actor,
            draft=_draft(self.machine.pk, source={'anomaly_id': self.anomaly.pk}),
            idempotency_key=uuid.uuid4().hex,
        )
        link = RepairPacketHealthEvidence.objects.get(packet_id=result.repair_packet_id)

        link.observation = 'rewritten'
        with self.assertRaisesMessage(ValueError, 'immutable'):
            link.save()

    def test_cross_machine_anomaly_is_refused_with_a_warning(self):
        """Another asset's telemetry can never justify this repair."""
        other = AssetMachine.objects.create(name=f'Other {uuid.uuid4().hex[:6]}')
        foreign, _ = record_anomaly(
            machine=other,
            fingerprint=fingerprint_for('link', 'foreign'),
            title='Someone else',
            severity=AnomalySeverity.WARNING,
        )

        result = create_repair_work_package(
            actor=self.actor,
            draft=_draft(self.machine.pk, source={'anomaly_id': foreign.pk}),
            idempotency_key=uuid.uuid4().hex,
        )

        self.assertTrue(
            any('different machine' in warning for warning in result.warnings)
        )
        foreign.refresh_from_db()
        self.assertIsNone(foreign.work_order_id)


class DuplicateApiTest(InvenTreeAPITestCase):
    """The HTTP surface reports a duplicate as a conflict, with links."""

    roles = ['work_order.view', 'work_order.add']
    url = '/api/maintenance/work-packages/create/'

    def setUp(self):
        """Create a machine that already has one open repair."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name=f'Centrifuge {uuid.uuid4().hex[:6]}'
        )
        self.first = self.post(
            self.url,
            {'machine_id': self.machine.pk, 'title': 'First repair'},
            expected_code=201,
        ).data

    def test_duplicate_returns_409_with_the_existing_links(self):
        """A conflict tells the caller what is already open."""
        response = self.post(
            self.url,
            {'machine_id': self.machine.pk, 'title': 'Second repair'},
            expected_code=409,
        )

        self.assertEqual(response.data['code'], 'DUPLICATE_OPEN_REPAIR')
        self.assertEqual(
            response.data['duplicates'][0]['work_order_id'], self.first['work_order_id']
        )

    def test_override_is_accepted(self):
        """An explicit override creates the second repair."""
        response = self.post(
            self.url,
            {
                'machine_id': self.machine.pk,
                'title': 'Second repair',
                'duplicate_override': True,
                'duplicate_override_reason': 'Separate fault on the same asset',
            },
            expected_code=201,
        )

        self.assertNotEqual(response.data['work_order_id'], self.first['work_order_id'])
