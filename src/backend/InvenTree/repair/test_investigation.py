"""Typed investigation findings and the versioned approved repair scope.

Two properties: an observation carries enough structure to be judged (category,
unit, verification, citation), and an approval freezes a version of the plan that
later regeneration cannot rewrite.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from assets.health_models import (
    HealthSource,
    MachineSignalBinding,
    MachineSignalState,
    SourceType,
)
from assets.models import AssetMachine
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.services.snapshots import capture_current_signal

from . import investigation
from .models import (
    ApprovedRepairScope,
    PacketStatus,
    RepairInvestigationFinding,
    RepairPacket,
)


class InvestigationFindingTest(TestCase):
    """Findings are typed, idempotent and refuse foreign evidence."""

    def setUp(self):
        """Create a machine, a packet and one captured evidence snapshot."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_superuser(
            username=f'finder-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name=f'Screen {suffix}')
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            fault_summary='Rake chain sag',
            status=PacketStatus.DIAGNOSED,
        )
        source = HealthSource.objects.create(
            name=f'SCADA {suffix}', source_type=SourceType.SCADA
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key=f'BS-{suffix}.TRQ',
            display_name='Rake drive torque',
            unit='%',
        )
        MachineSignalState.objects.create(
            binding=self.binding,
            value={'value': 88, 'unit': '%'},
            observed_at=timezone.now(),
        )
        self.snapshot = capture_current_signal(
            self.binding, reason='anomaly_repair', actor=self.actor
        )

    def test_finding_keeps_its_structure(self):
        """Category, value, unit and verification survive as fields."""
        finding, created = investigation.record_finding(
            self.packet,
            finding_key='F-01',
            observation='Drive torque peaked at 88% on a loaded cycle',
            category=RepairInvestigationFinding.Category.TELEMETRY,
            value=88.0,
            unit='%',
            snapshot=self.snapshot,
            evidence_source='Station SCADA',
            actor=self.actor,
        )

        self.assertTrue(created)
        self.assertEqual(finding.value, 88.0)
        self.assertEqual(finding.unit, '%')
        self.assertEqual(finding.snapshot_id, self.snapshot.id)
        self.assertEqual(
            finding.verification, RepairInvestigationFinding.Verification.UNVERIFIED
        )

    def test_recording_twice_updates_one_row(self):
        """A stable key makes a re-import idempotent."""
        investigation.record_finding(
            self.packet, finding_key='F-01', observation='First reading'
        )
        finding, created = investigation.record_finding(
            self.packet, finding_key='F-01', observation='Corrected reading'
        )

        self.assertFalse(created)
        self.assertEqual(self.packet.findings.count(), 1)
        self.assertEqual(finding.observation, 'Corrected reading')

    def test_snapshot_from_another_machine_is_refused(self):
        """Foreign telemetry can never be evidence for this fault."""
        other = AssetMachine.objects.create(name=f'Other {uuid.uuid4().hex[:6]}')
        other_packet = RepairPacket.objects.create(
            machine=other, fault_summary='Unrelated'
        )

        with self.assertRaisesMessage(
            investigation.InvestigationError, 'different machine'
        ):
            investigation.record_finding(
                other_packet,
                finding_key='F-01',
                observation='Borrowed evidence',
                snapshot=self.snapshot,
            )

    def test_empty_observation_and_unknown_category_are_refused(self):
        """A finding must say something, in a known category."""
        with self.assertRaises(investigation.InvestigationError):
            investigation.record_finding(
                self.packet, finding_key='F-02', observation='   '
            )
        with self.assertRaises(investigation.InvestigationError):
            investigation.record_finding(
                self.packet, finding_key='F-02', observation='x', category='hunch'
            )

    def test_finding_count_is_bounded(self):
        """A repair page is not an unbounded import target."""
        for index in range(investigation.MAX_FINDINGS_PER_PACKET):
            investigation.record_finding(
                self.packet, finding_key=f'F-{index:03d}', observation='reading'
            )

        with self.assertRaisesMessage(investigation.InvestigationError, 'at most'):
            investigation.record_finding(
                self.packet, finding_key='F-overflow', observation='one too many'
            )


class ApprovedScopeTest(TestCase):
    """Approving freezes a version that later work cannot rewrite."""

    def setUp(self):
        """Create a machine, an actor and a diagnosed packet."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_superuser(
            username=f'approver-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name=f'Blower {suffix}')
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            fault_summary='Bearing temperature rising',
            status=PacketStatus.DIAGNOSED,
        )

    def test_approval_records_an_ordered_scope(self):
        """Order is part of what was approved, so it is preserved."""
        scope = investigation.approve_repair_scope(
            self.packet,
            scope_lines=['Isolate and lock out', 'Replace drive-end bearing', 'Run in'],
            verified_cause='Drive-end bearing wear',
            failure_codes=['BRG-WEAR'],
            crew_size=2,
            planned_elapsed_minutes=240,
            actor=self.actor,
        )

        self.assertEqual(scope.version, 1)
        self.assertEqual(
            [line['action'] for line in scope.scope_lines],
            ['Isolate and lock out', 'Replace drive-end bearing', 'Run in'],
        )
        self.assertEqual([line['sequence'] for line in scope.scope_lines], [1, 2, 3])
        self.assertEqual(scope.approved_by, self.actor)
        self.assertTrue(scope.is_current)

    def test_reapproving_supersedes_rather_than_overwrites(self):
        """The earlier decision stays readable."""
        first = investigation.approve_repair_scope(
            self.packet, scope_lines=['Replace bearing'], actor=self.actor
        )
        second = investigation.approve_repair_scope(
            self.packet,
            scope_lines=['Replace bearing', 'Replace coupling'],
            actor=self.actor,
        )

        first.refresh_from_db()
        self.assertEqual(second.version, 2)
        self.assertIsNotNone(first.superseded_at)
        self.assertFalse(first.is_current)
        self.assertEqual(ApprovedRepairScope.objects.count(), 2)
        self.assertEqual(investigation.current_scope(self.packet).pk, second.pk)

    def test_an_approved_scope_cannot_be_edited(self):
        """Rewriting a decision in place would make it unreconstructable."""
        scope = investigation.approve_repair_scope(
            self.packet, scope_lines=['Replace bearing'], actor=self.actor
        )

        scope.verified_cause = 'something else'
        with self.assertRaisesMessage(ValueError, 'immutable'):
            scope.save()

    def test_empty_and_oversized_scopes_are_refused(self):
        """A scope needs content, and is bounded."""
        with self.assertRaises(investigation.InvestigationError):
            investigation.approve_repair_scope(self.packet, scope_lines=[])
        with self.assertRaises(investigation.InvestigationError):
            investigation.approve_repair_scope(
                self.packet, scope_lines=['x'] * (investigation.MAX_SCOPE_LINES + 1)
            )

    def test_approval_is_recorded_on_the_packet_timeline(self):
        """The decision leaves an audit trail on the aggregate."""
        investigation.approve_repair_scope(
            self.packet, scope_lines=['Replace bearing'], actor=self.actor
        )

        event = self.packet.events.order_by('-created_at').first()
        self.assertEqual(event.metadata['approved_scope_version'], 1)


class InvestigationApiTest(InvenTreeAPITestCase):
    """HTTP contract for findings and approved scope."""

    roles = ['work_order.view', 'work_order.change']

    def setUp(self):
        """Create a machine and a diagnosed packet."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name=f'Centrifuge {uuid.uuid4().hex[:6]}'
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            fault_summary='Bowl vibration',
            status=PacketStatus.DIAGNOSED,
        )

    def test_record_and_list_findings(self):
        """A recorded finding comes back with its structure intact."""
        created = self.post(
            f'/api/repair/packets/{self.packet.pk}/findings/',
            {
                'finding_key': 'F-01',
                'observation': 'Vibration measured 9.4 mm/s at the bowl bearing',
                'category': 'measurement',
                'value': 9.4,
                'unit': 'mm/s',
                'evidence_source': 'Handheld analyser',
            },
            expected_code=201,
        )
        self.assertEqual(created.data['value'], 9.4)
        self.assertEqual(created.data['category'], 'measurement')

        listed = self.get(
            f'/api/repair/packets/{self.packet.pk}/findings/', expected_code=200
        )
        self.assertEqual(listed.data['count'], 1)

    def test_re_recording_the_same_key_updates_in_place(self):
        """The endpoint is idempotent on the finding key."""
        url = f'/api/repair/packets/{self.packet.pk}/findings/'
        self.post(
            url, {'finding_key': 'F-01', 'observation': 'first'}, expected_code=201
        )
        self.post(
            url, {'finding_key': 'F-01', 'observation': 'second'}, expected_code=200
        )

        self.assertEqual(self.packet.findings.count(), 1)

    def test_malformed_finding_is_refused(self):
        """A finding without an observation is a client error, not a row."""
        response = self.post(
            f'/api/repair/packets/{self.packet.pk}/findings/',
            {'finding_key': 'F-01'},
            expected_code=400,
        )
        self.assertEqual(response.data['code'], 'INVESTIGATION_INVALID')

    def test_approve_scope_and_read_versions(self):
        """Approving twice leaves both versions readable."""
        url = f'/api/repair/packets/{self.packet.pk}/approved-scope/'

        self.post(url, {'scope_lines': ['Replace seal']}, expected_code=201)
        second = self.post(
            url,
            {
                'scope_lines': ['Replace seal', 'Replace wear ring'],
                'verified_cause': 'Seal face wear',
                'crew_size': 2,
            },
            expected_code=201,
        )

        self.assertEqual(second.data['version'], 2)
        self.assertTrue(second.data['is_current'])

        listed = self.get(url, expected_code=200)
        self.assertEqual(listed.data['count'], 2)
        self.assertFalse(listed.data['results'][1]['is_current'])

    def test_packet_detail_exposes_findings_and_current_scope(self):
        """The packet page can render both without extra requests."""
        self.post(
            f'/api/repair/packets/{self.packet.pk}/findings/',
            {'finding_key': 'F-01', 'observation': 'Vibration rising'},
            expected_code=201,
        )
        self.post(
            f'/api/repair/packets/{self.packet.pk}/approved-scope/',
            {'scope_lines': ['Replace bearing']},
            expected_code=201,
        )

        detail = self.get(f'/api/repair/packets/{self.packet.pk}/', expected_code=200)

        self.assertEqual(len(detail.data['findings']), 1)
        self.assertEqual(detail.data['approved_scope']['version'], 1)
