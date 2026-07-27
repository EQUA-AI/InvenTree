"""Detail-profile enrichment for dataset-owned demo work orders.

The properties these pin: a profile must match the record's lifecycle and card
kind, enrichment is idempotent, only owned records are touched, and the coverage
report tells the truth about what is still unauthored.
"""

import uuid
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase

from tasks.models import KanbanCard, WorkOrderLifecycle, WorkOrderType

from assets.demo_enrichment import (
    CLASS_ACTIVE_CORRECTIVE,
    CLASS_HISTORICAL_INSPECTION,
    CLASS_PROCUREMENT,
    CoverageReport,
    EnrichmentError,
    apply_profile,
    validate_profile,
)
from assets.models import AssetMachine
from repair.models import PacketStatus, RepairPacket


def _profile(**overrides):
    profile = {
        'profile_version': 1,
        'class': CLASS_ACTIVE_CORRECTIVE,
        'affected_component': {'name': 'Pump 2', 'external_id': 'PU-102'},
        'findings': [],
        'approved_scope': None,
    }
    profile.update(overrides)
    return profile


class ProfileValidationTest(TestCase):
    """A profile must be applicable to the record it is written for."""

    def test_unknown_class_is_refused(self):
        """Classes come from the plan's closed profile matrix."""
        with self.assertRaisesMessage(EnrichmentError, 'unknown profile class'):
            validate_profile(
                _profile(**{'class': 'guesswork'}),
                reference='WO-X',
                card_kind='work_order',
                is_terminal=False,
            )

    def test_unsupported_version_is_refused(self):
        """A newer profile format is not silently reinterpreted."""
        with self.assertRaisesMessage(EnrichmentError, 'unsupported profile_version'):
            validate_profile(
                _profile(profile_version=9),
                reference='WO-X',
                card_kind='work_order',
                is_terminal=False,
            )

    def test_active_profile_cannot_be_applied_to_completed_work(self):
        """Completed work never shows active-repair state."""
        with self.assertRaisesMessage(EnrichmentError, 'cannot be applied'):
            validate_profile(
                _profile(), reference='WO-X', card_kind='work_order', is_terminal=True
            )

    def test_historical_profile_cannot_be_applied_to_open_work(self):
        """Open work never shows facts only closeout can establish."""
        with self.assertRaisesMessage(EnrichmentError, 'still open'):
            validate_profile(
                _profile(**{'class': CLASS_HISTORICAL_INSPECTION}),
                reference='WO-X',
                card_kind='work_order',
                is_terminal=False,
            )

    def test_procurement_profile_requires_a_procurement_card(self):
        """A sourcing profile on a repair parent would misdescribe the record."""
        with self.assertRaisesMessage(EnrichmentError, 'procurement card'):
            validate_profile(
                _profile(**{'class': CLASS_PROCUREMENT}),
                reference='WO-X',
                card_kind='work_order',
                is_terminal=False,
            )

    def test_procurement_may_not_carry_machine_findings(self):
        """A procurement child records sourcing, not machine observations."""
        with self.assertRaisesMessage(EnrichmentError, 'may not carry'):
            validate_profile(
                _profile(
                    **{'class': CLASS_PROCUREMENT},
                    findings=[{'key': 'F-01', 'observation': 'x'}],
                ),
                reference='WO-X',
                card_kind='procurement',
                is_terminal=False,
            )

    def test_inspection_may_not_carry_an_approved_repair_scope(self):
        """An inspection has no repair scope to approve."""
        with self.assertRaisesMessage(EnrichmentError, 'may not carry an approved'):
            validate_profile(
                _profile(
                    **{'class': CLASS_HISTORICAL_INSPECTION},
                    approved_scope={'lines': ['x']},
                ),
                reference='WO-X',
                card_kind='work_order',
                is_terminal=True,
            )

    def test_findings_need_stable_unique_keys(self):
        """Keys are what make a rerun update rather than duplicate."""
        with self.assertRaisesMessage(EnrichmentError, 'stable key'):
            validate_profile(
                _profile(findings=[{'observation': 'x'}]),
                reference='WO-X',
                card_kind='work_order',
                is_terminal=False,
            )
        with self.assertRaisesMessage(EnrichmentError, 'repeated'):
            validate_profile(
                _profile(
                    findings=[
                        {'key': 'F-01', 'observation': 'x'},
                        {'key': 'F-01', 'observation': 'y'},
                    ]
                ),
                reference='WO-X',
                card_kind='work_order',
                is_terminal=False,
            )


class ApplyProfileTest(TestCase):
    """Applying a profile writes only inside the enrichment boundary."""

    def setUp(self):
        """Create an owned active repair with a packet."""
        suffix = uuid.uuid4().hex[:6]
        self.machine = AssetMachine.objects.create(name=f'Pump station {suffix}')
        self.card = KanbanCard.objects.create(
            reference=f'WO-WW-R-{suffix}',
            title='Seal leakage',
            status=KanbanCard.STATUS_IN_PROGRESS,
            priority=KanbanCard.PRIORITY_HIGH,
            machine=self.machine,
            work_order_type=WorkOrderType.CORRECTIVE,
            assignee='Route crew',
            tags=['demo', 'water_wastewater', 'water_workflow_demo'],
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.card,
            fault_summary='Seal leakage',
            status=PacketStatus.DIAGNOSED,
        )

    def _apply(self, profile=None, report=None):
        report = report or CoverageReport()
        validated = validate_profile(
            profile or _profile(),
            reference=self.card.reference,
            card_kind=self.card.card_kind,
            is_terminal=False,
        )
        apply_profile(
            self.card, validated, dataset='water_workflow_demo', report=report
        )
        return report

    def test_component_is_recorded_on_the_card(self):
        """The affected component is a field, not buried in the description."""
        self._apply()

        self.card.refresh_from_db()
        self.assertEqual(self.card.affected_component, 'Pump 2')
        self.assertEqual(self.card.affected_component_ref, 'PU-102')

    def test_findings_and_scope_land_on_the_packet(self):
        """Investigation content goes to the fault-to-fix aggregate."""
        self._apply(
            _profile(
                findings=[
                    {
                        'key': 'F-01',
                        'category': 'telemetry',
                        'observation': 'Running current reached 139 A',
                        'value': 139.0,
                        'unit': 'A',
                    }
                ],
                approved_scope={
                    'verified_cause': 'Seal wear',
                    'lines': ['Isolate', 'Replace seal'],
                    'crew_size': 2,
                },
            )
        )

        [finding] = self.packet.findings.all()
        self.assertEqual(finding.value, 139.0)
        scope = self.packet.approved_scopes.get()
        self.assertEqual(scope.version, 1)
        self.assertEqual(len(scope.scope_lines), 2)

    def test_rerunning_is_idempotent(self):
        """A second pass updates what it wrote rather than duplicating it."""
        profile = _profile(
            findings=[{'key': 'F-01', 'observation': 'Current at 139 A'}],
            approved_scope={'lines': ['Replace seal'], 'crew_size': 2},
        )
        self._apply(profile)
        second = self._apply(profile)

        self.assertEqual(self.packet.findings.count(), 1)
        self.assertEqual(self.packet.approved_scopes.count(), 1)
        self.assertEqual(second.unchanged, 1)
        self.assertEqual(second.enriched, 0)

    def test_a_changed_scope_creates_a_new_version(self):
        """A revised plan is approved again rather than edited in place."""
        self._apply(
            _profile(approved_scope={'lines': ['Replace seal'], 'crew_size': 2})
        )
        self._apply(
            _profile(
                approved_scope={
                    'lines': ['Replace seal', 'Replace wear ring'],
                    'crew_size': 2,
                }
            )
        )

        self.assertEqual(self.packet.approved_scopes.count(), 2)

    def test_operator_edits_outside_the_boundary_survive(self):
        """Schedule, assignment and lifecycle belong to the operator."""
        self.card.assignee = 'R. Shuruncle'
        self.card.lifecycle_status = WorkOrderLifecycle.READY
        self.card.save(update_fields=['assignee', 'lifecycle_status'])

        self._apply()

        self.card.refresh_from_db()
        self.assertEqual(self.card.assignee, 'R. Shuruncle')
        self.assertEqual(self.card.lifecycle_status, WorkOrderLifecycle.READY)

    def test_findings_without_a_packet_are_refused(self):
        """A completed inspection cannot hold findings, and none are invented."""
        plain = KanbanCard.objects.create(
            reference=f'WO-WW-H-{uuid.uuid4().hex[:6]}',
            title='Routine inspection',
            status=KanbanCard.STATUS_DONE,
            priority=KanbanCard.PRIORITY_LOW,
            machine=self.machine,
            lifecycle_status=WorkOrderLifecycle.COMPLETED,
            tags=['demo', 'water_wastewater', 'water_workflow_demo'],
        )
        validated = validate_profile(
            _profile(
                **{'class': CLASS_HISTORICAL_INSPECTION},
                findings=[{'key': 'F-01', 'observation': 'x'}],
            ),
            reference=plain.reference,
            card_kind=plain.card_kind,
            is_terminal=True,
        )

        with self.assertRaisesMessage(EnrichmentError, 'require a repair packet'):
            apply_profile(
                plain, validated, dataset='water_workflow_demo', report=CoverageReport()
            )


class EnrichmentCommandTest(TestCase):
    """The loader's enrichment mode and its coverage report."""

    @classmethod
    def setUpTestData(cls):
        """Load the prerequisite catalog, users and machines."""
        from django.contrib.auth import get_user_model

        from part.models import Part

        for ipn, name in (
            ('TB1', 'Test Board 1'),
            ('TB2', 'Test Board 2'),
            ('TB3', 'Test Board 3'),
            ('002.02-PCB', 'Widget Board'),
        ):
            Part.objects.create(IPN=ipn, name=name)

        user_model = get_user_model()
        user_model.objects.create_superuser(
            username='admin', email='admin@example.com', password='pw'
        )
        user_model.objects.create_user(username='engineer', password='pw')
        user_model.objects.create_user(username='steven', password='pw')

        call_command('load_asset_demo_data', stdout=StringIO())

    def load(self, **options):
        """Run the water loader, keeping its output out of the test log."""
        out = StringIO()
        call_command('load_water_workflow_demo_data', stdout=out, **options)
        return out.getvalue()

    def test_coverage_report_names_unauthored_records(self):
        """The report is honest about what still has no profile."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        output = self.load(enrich_owned_work_orders=True)

        self.assertIn('Enrichment coverage:', output)
        self.assertIn('owned cards discovered', output)
        # The authored subset covers all five classes; the rest are reported.
        self.assertIn('have no', output)

    def test_require_complete_profiles_fails_while_records_are_unauthored(self):
        """The strict gate is available and refuses an incomplete dataset."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        self.load()

        with self.assertRaisesMessage(CommandError, 'without a detail profile'):
            self.load(enrich_owned_work_orders=True, require_complete_profiles=True)

    def test_authored_profiles_are_applied(self):
        """Every authored class reaches its record."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        self.load(enrich_owned_work_orders=True)

        repair = KanbanCard.objects.get(reference='WO-WW-R-001')
        self.assertEqual(repair.affected_component, 'Influent Pump 2')
        self.assertEqual(repair.affected_component_ref, 'PU-102')
        self.assertEqual(repair.repair_packet.findings.count(), 3)
        self.assertEqual(repair.repair_packet.approved_scopes.get().version, 1)

        history = KanbanCard.objects.get(reference='WO-WW-H-001')
        self.assertEqual(history.affected_component, 'Influent Pumps 1-3')

        procurement = KanbanCard.objects.get(reference='WO-WW-P-001')
        self.assertEqual(procurement.affected_component, 'Centrifuge 2 main bearing')

    def test_enrichment_is_idempotent_across_reruns(self):
        """A second enrichment pass changes nothing."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        self.load(enrich_owned_work_orders=True)
        packet = KanbanCard.objects.get(reference='WO-WW-R-001').repair_packet

        self.load(enrich_owned_work_orders=True)

        self.assertEqual(packet.findings.count(), 3)
        self.assertEqual(packet.approved_scopes.count(), 1)
