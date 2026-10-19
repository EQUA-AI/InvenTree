"""P42: corrections preserve original attributed observations and unaffected facts."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import WorkOrder
from tasks.work_execution.investigation import project

from assets.models import AssetMachine, get_default_client
from repair.investigation import record_finding
from repair.models import RepairFindingRevision, RepairPacket


class FindingRevisionTests(TestCase):
    """Exercise the existing writer and the Diagnose projection together."""

    def setUp(self):
        """Two actual authors and one synthetic packet-owned Work."""
        self.original = get_user_model().objects.create_user(username='original-tech')
        self.corrector = get_user_model().objects.create_user(
            username='correcting-tech'
        )
        machine = AssetMachine.objects.create(
            name='Synthetic revision pump', client=get_default_client()
        )
        self.work = WorkOrder.objects.create(
            title='Synthetic investigation', machine=machine
        )
        self.packet = RepairPacket.objects.create(
            machine=machine, work_order=self.work, fault_summary='Synthetic noise'
        )

    def test_original_disputed_and_corrected_records_remain_separate(self):
        """No correction can erase the prior value, author, source or dispute."""
        finding, _ = record_finding(
            self.packet,
            finding_key='F-1',
            observation='Original reading',
            value=0,
            unit='g',
            evidence_source='Original instrument',
            verification='disputed',
            actor=self.original,
        )
        unaffected, _ = record_finding(
            self.packet,
            finding_key='F-2',
            observation='Unaffected verified observation',
            verification='verified',
            actor=self.original,
        )
        corrected, created = record_finding(
            self.packet,
            finding_key='F-1',
            observation='Corrected reading',
            value=2.4,
            unit='g',
            evidence_source='Calibrated instrument',
            actor=self.corrector,
        )
        self.assertFalse(created)
        self.assertEqual(corrected.pk, finding.pk)
        revision = RepairFindingRevision.objects.get()
        self.assertEqual(revision.snapshot['value'], 0)
        self.assertEqual(revision.snapshot['verification'], 'disputed')
        self.assertEqual(revision.snapshot['recorded_by_id'], self.original.pk)
        self.assertEqual(revision.replaced_by, self.corrector)
        unaffected.refresh_from_db()
        self.assertEqual(unaffected.verification, 'verified')
        projection = project(self.work)
        self.assertEqual(projection['finding_history'], 'preserved_revisions')
        self.assertEqual(
            projection['finding_revisions'][0]['snapshot']['observation'],
            'Original reading',
        )
        with self.assertRaises(ValueError):
            revision.save()

    def test_exact_repeat_does_not_add_history_or_change_sequence(self):
        """Idempotent import retries preserve their existing identity/order."""
        first, _ = record_finding(
            self.packet, finding_key='F-1', observation='Original', actor=self.original
        )
        record_finding(
            self.packet, finding_key='F-2', observation='Other', actor=self.original
        )
        repeated, created = record_finding(
            self.packet, finding_key='F-1', observation='Original', actor=self.original
        )
        self.assertFalse(created)
        self.assertEqual(repeated.sequence, first.sequence)
        self.assertEqual(repeated.updated_at, first.updated_at)
        self.assertFalse(RepairFindingRevision.objects.exists())

    def test_repeated_corrections_have_no_round_limit(self):
        """Every earlier observation remains available after repeated correction."""
        for i in range(5):
            record_finding(
                self.packet,
                finding_key='F-1',
                observation=f'Reading {i}',
                actor=self.original if i == 0 else self.corrector,
            )
        self.assertEqual(RepairFindingRevision.objects.count(), 4)
        self.assertEqual(self.packet.findings.count(), 1)

    def test_repeated_iso_observation_time_is_not_a_correction(self):
        """Native JSON retries normalize time before comparing saved facts."""
        for index, observed_at in enumerate((
            '2026-10-19T07:00:00Z',
            '2026-10-19T07:00:00',
        )):
            for _ in range(2):
                record_finding(
                    self.packet,
                    finding_key=f'F-TIME-{index}',
                    observation='Same observed fact',
                    observed_at=observed_at,
                    actor=self.original,
                )
        self.assertFalse(RepairFindingRevision.objects.exists())
