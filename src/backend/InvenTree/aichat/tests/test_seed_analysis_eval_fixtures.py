"""S14 (WP-A6): the synthetic analysis corpus seeder.

Everything derives from corpus.yaml (the one declared source): machine
ownership, work-order counts with their deliberate date defects,
non-implying maintenance stages, and the controlled-document lifecycle
pair. Registry-only by default (the attachment note needs the RAG flag;
Azure content indexing is the operator's runbook step).
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import F
from django.test import TestCase

from aimms_testing import requires_postgres

SCOPE_KEY = 'eval-analysis-boundary'


def _seed(*args, scope_key: str = SCOPE_KEY):
    out = StringIO()
    with mock.patch(
        'aichat.services.eval_fixtures.restamp_fixture_scope', return_value=[]
    ):
        call_command(
            'seed_analysis_eval_fixtures', f'--scope-key={scope_key}', *args, stdout=out
        )
    return out.getvalue()


class SeedAnalysisFixturesTests(TestCase):
    """The declared corpus lands exactly as corpus.yaml says."""

    def test_production_is_refused_without_break_glass(self):
        """Tests run with DEBUG off — exactly the posture that must refuse."""
        with self.assertRaises(CommandError) as caught:
            _seed()
        self.assertIn('break-glass', str(caught.exception))

    def test_dry_run_writes_nothing(self):
        """Preview mode reports without creating any row."""
        from tasks.models import WorkOrder

        from aichat.models import ControlledDocument
        from assets.models import AssetMachine

        output = _seed('--dry-run')
        self.assertIn('DRY RUN', output)
        self.assertEqual(AssetMachine.objects.count(), 0)
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(ControlledDocument.objects.count(), 0)

    @requires_postgres
    def test_full_seed_matches_the_declared_corpus(self):
        """Counts, defects, lifecycle states land exactly as declared."""
        from tasks.models import WorkOrder

        from aichat.models import ControlledDocument
        from aichat.services.eval_fixtures import EVAL_FIXTURES_CODE, OFFLIMITS_CODE
        from assets.models import AssetMachine, AssetMaintenanceRecord

        output = _seed('--break-glass')

        # Machines: five on eval-fixtures, the test bench on eval-offlimits.
        eval_machines = AssetMachine.objects.filter(client__code=EVAL_FIXTURES_CODE)
        self.assertEqual(eval_machines.count(), 5)
        bench = AssetMachine.objects.get(serial='EVAL-TB1')
        self.assertEqual(bench.client.code, OFFLIMITS_CODE)

        # Work orders: 28 + 31 declared plus the two special comparison WOs.
        solar_a = AssetMachine.objects.get(serial='EVAL-SI3000-A')
        solar_b = AssetMachine.objects.get(serial='EVAL-SI3000-B')
        a_wos = WorkOrder.objects.filter(machine=solar_a).exclude(
            reference__in=('WO-EVAL-SI3000A-OPEN', 'WO-EVAL-SI3000A-DONE')
        )
        self.assertEqual(a_wos.count(), 28)
        self.assertEqual(WorkOrder.objects.filter(machine=solar_b).count(), 31)

        # Deliberate date defects, exactly as declared for solar_a:
        self.assertEqual(
            a_wos.filter(
                actual_started_at__isnull=True, actual_completed_at__isnull=True
            ).count(),
            4,  # created_only
        )
        self.assertEqual(
            a_wos.filter(
                actual_started_at__isnull=False, actual_completed_at__isnull=True
            ).count(),
            6,  # missing_completion
        )
        self.assertEqual(
            a_wos.filter(actual_completed_at__lt=F('actual_started_at')).count(),
            2,  # conflicting_dates
        )

        # Maintenance records ride completed WOs and never imply closure.
        records = AssetMaintenanceRecord.objects.filter(machine=solar_a)
        self.assertGreater(records.count(), 0)
        sample = records.first()
        self.assertIn('Symptom:', sample.details)
        self.assertIn('Action:', sample.details)
        self.assertIn('Outcome:', sample.details)

        # Comparison-gate pair.
        open_wo = WorkOrder.objects.get(reference='WO-EVAL-SI3000A-OPEN')
        done_wo = WorkOrder.objects.get(reference='WO-EVAL-SI3000A-DONE')
        self.assertIsNone(open_wo.actual_completed_at)
        self.assertFalse(
            hasattr(open_wo, 'maintenance_record') and open_wo.maintenance_record
        )
        self.assertIsNotNone(done_wo.actual_completed_at)

        # Controlled documents: superseded/current pair + supplement + bulletin.
        docs = ControlledDocument.objects.filter(scope_key=SCOPE_KEY)
        self.assertEqual(docs.count(), 4)
        rev_a = docs.get(document_id='SI3000-SM', revision='A')
        rev_b = docs.get(document_id='SI3000-SM', revision='B')
        self.assertFalse(rev_a.is_current)
        self.assertEqual(rev_a.state, 'superseded')
        self.assertTrue(rev_b.is_current)
        self.assertEqual(rev_b.state, 'indexed')
        supplement = docs.get(document_id='SI3000-A-SUP')
        self.assertEqual(supplement.asset_id, 'EVAL-SI3000-A')
        bulletin = docs.get(document_id='FLEET-BULLETIN-7')
        self.assertEqual(bulletin.asset_id, '')
        for doc in docs:
            self.assertTrue(doc.source_sha256)

        # The operator's real-ingestion runbook lines are printed.
        self.assertIn('ingest_controlled_document', output)
        # RAG flag off (default): the uncontrolled note is skipped loudly.
        self.assertIn('attachment note SKIPPED', output)

    @requires_postgres
    def test_seeding_is_idempotent(self):
        """A second run creates nothing new."""
        from tasks.models import WorkOrder

        from aichat.models import ControlledDocument

        _seed('--break-glass')
        wo_count = WorkOrder.objects.count()
        doc_count = ControlledDocument.objects.count()
        _seed('--break-glass')
        self.assertEqual(WorkOrder.objects.count(), wo_count)
        self.assertEqual(ControlledDocument.objects.count(), doc_count)

    @requires_postgres
    def test_audit_covers_the_new_machines_by_client(self):
        """The client-driven audit needs no per-set extension."""
        from io import StringIO as _StringIO

        _seed('--break-glass')
        out = _StringIO()
        call_command('audit_eval_fixture_index', stdout=out)
        self.assertIn('machines', out.getvalue())
