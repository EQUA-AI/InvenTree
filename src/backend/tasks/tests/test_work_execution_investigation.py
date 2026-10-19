"""P20: Diagnose projects the saved investigation without inventing certainty."""

import json

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from assets.models import AssetMachine, get_default_client
from InvenTree.unit_test import InvenTreeAPITestCase
from repair.models import ApprovedRepairScope, RepairInvestigationFinding, RepairPacket
from repair.schema import coerce_diagnosis
from tasks.models import WorkOrder

SCOPE = 'tasks.scope.single_site_scope_resolver'

DIAGNOSIS = {
    'schema_version': 2,
    'likely_cause': 'Drive-end bearing wear',
    'confidence': 0.87,
    'confidence_label': 'high',
    'failure_mode': 'bearing_wear',
    'alternatives': ['Misalignment', {'cause': 'Soft foot'}],
    'evidence': [
        {
            'snapshot_id': '6f1c2d4e-0000-4000-8000-000000000001',
            'observation': 'DE vibration rose to 7.1 mm/s',
            'relation': 'supports',
            'signal_label': 'DE vibration',
            'value': 7.1,
            'unit': 'mm/s',
        },
        {
            'snapshot_id': None,
            'observation': 'Bearing temperature unchanged',
            'relation': 'contradicts',
        },
        'Operator heard noise',
    ],
    'confirm_tests': ['Measure DE envelope acceleration', ''],
    'status': 'available',
    'authority': 'derived',
    'data_window': {
        'start': '2026-10-18T06:00:00Z',
        'end': '2026-10-18T12:00:00Z',
        'snapshot_count': 12,
    },
    'freshness': {'stale': False, 'stale_signal_count': 1},
    'quality': {'summary': 'good', 'bad_signal_count': 0},
    'provider': 'wf7',
    'model_or_rule_version': 'wf7-2026.10',
    'generated_at': '2026-10-18T12:05:00Z',
    'verified_by_user': False,
    'verified_at': None,
    'verified_by': None,
    'amendments': [
        {
            'text': 'Noise is from the coupling guard',
            'author': 'tech',
            'at': '2026-10-18T13:00:00Z',
        }
    ],
}


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=SCOPE)
class InvestigationProjectionTest(InvenTreeAPITestCase):
    """``investigation.v1`` keeps qualification, relations and gaps explicit."""

    roles = 'all'

    def setUp(self):
        """One packet-owned work order with a preliminary diagnosis."""
        super().setUp()
        self.machine = AssetMachine.objects.create(
            name='Diagnose machine', client=get_default_client()
        )
        self.work_order = WorkOrder.objects.create(
            reference='WO-DIAG-1', title='Diagnose work', machine=self.machine
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            fault_summary='Noise',
            diagnosis=DIAGNOSIS, diagnosis_schema_version=2,
        )
        self.tech = get_user_model().objects.create_user(
            username='diag-tech', first_name='Sam', last_name='Reed'
        )

    def investigation(self, work_order=None):
        """Read the overview projection Diagnose renders."""
        return self.get(
            reverse(
                'kanban-card-overview',
                kwargs={'pk': (work_order or self.work_order).pk},
            ),
            expected_code=200,
        ).data['investigation']

    def test_standalone_work_has_no_investigation_and_no_request_provider(self):
        """No packet means no saved investigation, said plainly."""
        other = WorkOrder.objects.create(
            reference='WO-DIAG-2', title='Standalone', machine=self.machine
        )
        investigation = self.investigation(other)
        self.assertEqual(investigation['rule'], 'investigation.v1')
        self.assertIsNone(investigation['packet'])
        self.assertIsNone(investigation['explanation'])
        self.assertEqual(
            investigation['requests'], {'available': False, 'reason': 'no_provider'}
        )

    def test_preliminary_explanation_keeps_relations_and_drops_confidence(self):
        """Evidence relation, proposed checks and amendments survive; no score."""
        investigation = self.investigation()
        explanation = investigation['explanation']
        self.assertTrue(explanation['preliminary'])
        self.assertEqual(explanation['likely_cause'], 'Drive-end bearing wear')
        self.assertEqual(
            [item['relation'] for item in explanation['evidence']],
            ['supports', 'contradicts', 'unknown'],
        )
        self.assertEqual(explanation['evidence'][0]['unit'], 'mm/s')
        # A v1 prose item is kept as an observation, never promoted to support.
        self.assertEqual(
            explanation['evidence'][2]['observation'], 'Operator heard noise'
        )
        self.assertEqual(explanation['alternatives'], ['Misalignment', 'Soft foot'])
        self.assertEqual(
            explanation['proposed_checks'], ['Measure DE envelope acceleration']
        )
        self.assertEqual(
            explanation['amendments'],
            [
                {
                    'text': 'Noise is from the coupling guard',
                    'author': 'tech',
                    'recorded_at': '2026-10-18T13:00:00Z',
                    'reason': None,
                }
            ],
        )
        self.assertEqual(explanation['data_window']['snapshot_count'], 12)
        self.assertEqual(explanation['stale_signal_count'], 1)
        # Confidence omission applies to this projection, not the legacy raw blob.
        serialized = json.dumps(investigation)
        self.assertNotIn('confidence', serialized)
        self.assertNotIn('0.87', serialized)
        self.assertEqual(
            investigation['requests'], {'available': False, 'reason': 'no_provider'}
        )

    def test_verified_diagnosis_names_the_verifier(self):
        """Only a recorded human verification removes the preliminary label."""
        self.packet.diagnosis = {
            **DIAGNOSIS,
            'verified_by_user': True,
            'verified_by': 'diag-tech',
            'verified_at': '2026-10-18T14:00:00Z',
        }
        self.packet.save()
        explanation = self.investigation()['explanation']
        self.assertFalse(explanation['preliminary'])
        self.assertEqual(explanation['verified_by'], 'diag-tech')
        self.assertEqual(explanation['verified_at'], '2026-10-18T14:00:00Z')

    def test_findings_carry_attribution_and_the_current_intervention_basis(self):
        """Disputed findings stay; the current scope names its approver."""
        RepairInvestigationFinding.objects.create(
            packet=self.packet,
            finding_key='F-1',
            category='measurement',
            observation='Envelope acceleration high',
            value=0.0,
            unit='g',
            verification='disputed',
            recorded_by=self.tech,
            observed_at=timezone.now(),
        )
        ApprovedRepairScope.objects.create(
            packet=self.packet,
            version=1,
            verified_cause='First basis',
            scope_lines=[{'sequence': 1, 'action': 'Inspect'}],
            superseded_at=timezone.now(),
        )
        ApprovedRepairScope.objects.create(
            packet=self.packet,
            version=2,
            verified_cause='Bearing replacement basis',
            scope_lines=[
                {'sequence': 1, 'action': 'Isolate'},
                {'sequence': 2, 'action': 'Replace bearing'},
            ],
            failure_codes=['BRG'],
            approved_by=self.tech,
        )
        investigation = self.investigation()
        finding = investigation['findings'][0]
        self.assertEqual(finding['verification'], 'disputed')
        self.assertEqual(finding['recorded_by'], 'Sam Reed')
        self.assertEqual(finding['value'], 0.0)
        self.assertEqual(investigation['finding_history'], 'overwritten_in_place')
        intervention = investigation['intervention']
        self.assertEqual(intervention['version'], 2)
        self.assertEqual(intervention['scope_line_count'], 2)
        self.assertEqual(intervention['approved_by'], 'Sam Reed')
        self.assertEqual(intervention['superseded_versions'], 1)

    def test_no_generated_diagnosis_has_no_explanation_or_basis(self):
        """Neither an empty packet nor an ungenerated blob establishes telemetry facts."""
        for diagnosis, version in (({}, 0), ({}, 2), (DIAGNOSIS, 0)):
            with self.subTest(diagnosis=diagnosis, version=version):
                self.packet.diagnosis = diagnosis
                self.packet.diagnosis_schema_version = version
                self.packet.save()
                self.assertIsNone(self.investigation()['explanation'])

    def test_wf7_missing_provenance_is_unknown_even_after_schema_defaults_persisted(self):
        """Cited records are not zero snapshots or evidence of threshold authority."""
        raw = {
            'schema_version': 2, 'likely_cause': 'Cited explanation', 'status': 'available',
            'authority': 'derived', 'generator': 'wf7', 'provider': 'azure_foundry_luna',
            'evidence': [{'snapshot_id': 'work_order_closeout:41@7', 'observation': 'Prior repair'}],
        }
        for diagnosis in (raw, coerce_diagnosis(raw)):
            with self.subTest(coerced='data_window' in diagnosis):
                self.packet.diagnosis = diagnosis
                self.packet.save()
                basis = self.investigation()['explanation']
                self.assertEqual(basis['analysis_status'], 'available')
                self.assertIsNone(basis['authority'])
                self.assertEqual(basis['data_window'], {'start': None, 'end': None, 'snapshot_count': None})
                self.assertIsNone(basis['stale_signal_count'])
                self.assertIsNone(basis['quality_summary'])
                self.assertEqual(basis['evidence'][0]['snapshot_id'], 'work_order_closeout:41@7')

    def test_heuristic_prose_does_not_establish_available_evidence_or_threshold(self):
        """The offline text heuristic records neither threshold authority nor telemetry."""
        self.packet.diagnosis = coerce_diagnosis({
            'likely_cause': 'Keyword fallback', 'generator': 'heuristic', 'evidence': []
        })
        self.packet.save()
        explanation = self.investigation()['explanation']
        self.assertIsNone(explanation['analysis_status'])
        self.assertIsNone(explanation['authority'])
        self.assertIsNone(explanation['data_window']['snapshot_count'])
        self.assertIsNone(explanation['stale_signal_count'])

    def test_recorded_zero_and_named_authority_remain_distinct_from_missing(self):
        """A bounded recorded window can retain real zero counts and a named rule."""
        self.packet.diagnosis = {
            **DIAGNOSIS, 'authority_source': 'Recorded configured rule',
            'data_window': {**DIAGNOSIS['data_window'], 'snapshot_count': 0},
            'freshness': {'stale_signal_count': 0},
        }
        self.packet.save()
        explanation = self.investigation()['explanation']
        self.assertEqual(explanation['data_window']['snapshot_count'], 0)
        self.assertEqual(explanation['stale_signal_count'], 0)
        self.assertEqual(explanation['authority'], 'derived')
