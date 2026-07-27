"""Tests for the water maintenance and repair workflow demo dataset."""

import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from tasks.models import (
    WorkingCalendar,
    WorkOrder,
    WorkOrderDependency,
    WorkOrderLifecycle,
    WorkOrderPart,
)
from tasks.services.conflicts import detect_conflicts

from assets.models import AssetMaintenanceRecord
from part.models import Part
from repair.models import (
    GateStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    RepairPacketGate,
    SafetyGateTemplate,
)

DATASET_TAG = 'water_workflow_demo'
WATER_MACHINE_NAMES = (
    'Aeration Basin No. 3 Diffuser Grid',
    'Aeration Blower No. 2',
    'Cooling Water Train A',
    'Dewatering Centrifuge No. 2',
    'Influent Pump Station No. 1',
    'Mechanical Bar Screen No. 1',
    'Remote Lift Station 14 — Blue River',
    'Secondary Clarifier No. 4',
    'UF/RO Skid No. 1',
    'UV Disinfection Channel No. 1',
)


class WaterWorkflowFixture(TestCase):
    """Shared catalog, users and loader helper for the workflow demo suites.

    Carries no tests of its own: subclasses that inherited from a populated test
    class would re-run every one of its cases.
    """

    @classmethod
    def setUpTestData(cls):
        """Load the prerequisite asset catalog and target machines."""
        for ipn, name in (
            ('TB1', 'Test Board 1'),
            ('TB2', 'Test Board 2'),
            ('TB3', 'Test Board 3'),
            ('002.02-PCB', 'Widget Board'),
        ):
            Part.objects.create(IPN=ipn, name=name)

        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            username='admin', email='admin@example.com', password='pw'
        )
        admin.first_name = 'Adam'
        admin.last_name = 'Administrator'
        admin.save(update_fields=['first_name', 'last_name'])
        user_model.objects.create_user(
            username='engineer',
            first_name='Robert',
            last_name='Shuruncle',
            password='pw',
        )
        user_model.objects.create_user(
            username='steven',
            first_name='Steven',
            last_name='Stafferson',
            password='pw',
        )

        call_command('load_asset_demo_data', stdout=StringIO())

    def load_workflow(
        self,
        *,
        dry_run=False,
        reset=False,
        anchor='2026-07-27',
        enrich=False,
        require_complete=False,
    ):
        """Load workflow records while keeping command output out of test logs."""
        call_command(
            'load_water_workflow_demo_data',
            dry_run=dry_run,
            reset_owned_scenarios=reset,
            schedule_anchor=anchor,
            enrich_owned_work_orders=enrich,
            require_complete_profiles=require_complete,
            stdout=StringIO(),
        )

    @staticmethod
    def workflow_cards(kind=None):
        """Return cards tagged as belonging to this workflow dataset."""
        work_orders = [
            work_order
            for work_order in WorkOrder.objects.all()
            if DATASET_TAG in set(work_order.tags or [])
        ]
        if kind:
            work_orders = [
                work_order
                for work_order in work_orders
                if kind in set(work_order.tags or [])
            ]
        return work_orders


class WaterWorkflowDemoTest(WaterWorkflowFixture):
    """Verify deterministic maintenance, repair, and scheduling demo records."""

    def test_loads_complete_workflow_dataset(self):
        """All machines receive history, repair packets, cards, and schedules."""
        self.load_workflow()

        history_cards = self.workflow_cards('maintenance_history')
        scenario_work_orders = self.workflow_cards('repair_scenario')
        procurement_cards = self.workflow_cards('procurement')
        active_cards = [*scenario_work_orders, *procurement_cards]
        water_history = AssetMaintenanceRecord.objects.filter(
            machine__name__in=WATER_MACHINE_NAMES
        )

        self.assertEqual(len(history_cards), 30)
        self.assertEqual(water_history.count(), 30)
        self.assertEqual(water_history.exclude(work_order__isnull=False).count(), 0)
        for machine_name in WATER_MACHINE_NAMES:
            with self.subTest(machine=machine_name):
                self.assertEqual(
                    water_history.filter(machine__name=machine_name).count(), 3
                )

        self.assertEqual(len(scenario_work_orders), 10)
        self.assertEqual(len(procurement_cards), 4)
        self.assertEqual(len(active_cards), 14)
        self.assertTrue(
            all(
                work_order.is_active
                and work_order.scheduled_start is not None
                and work_order.scheduled_end is not None
                for work_order in active_cards
            )
        )
        stage_counts = {
            status: sum(work_order.status == status for work_order in active_cards)
            for status in (
                WorkOrder.STATUS_BACKLOG,
                WorkOrder.STATUS_IN_PROGRESS,
                WorkOrder.STATUS_REVIEW,
            )
        }
        self.assertEqual(
            stage_counts,
            {
                WorkOrder.STATUS_BACKLOG: 5,
                WorkOrder.STATUS_IN_PROGRESS: 5,
                WorkOrder.STATUS_REVIEW: 4,
            },
        )
        for work_order in active_cards:
            with self.subTest(stage=work_order.status, reference=work_order.reference):
                self.assertEqual(
                    work_order.lifecycle_status, WorkOrderLifecycle.PLANNED
                )
                self.assertIsNone(work_order.actual_started_at)
        self.assertEqual(detect_conflicts(active_cards), [])

        self.assertEqual(WorkingCalendar.objects.count(), 4)
        self.assertEqual(WorkingCalendar.objects.filter(is_default=True).count(), 1)
        self.assertEqual(
            WorkingCalendar.objects.filter(machine__isnull=False).count(), 3
        )

        packets = RepairPacket.objects.select_related('work_order', 'machine')
        self.assertEqual(packets.count(), 10)
        self.assertEqual(packets.filter(status=PacketStatus.DRAFT).count(), 10)
        self.assertEqual(
            {packet.work_order_id for packet in packets},
            {work_order.pk for work_order in scenario_work_orders},
        )
        self.assertTrue(all(packet.gates.exists() for packet in packets))
        self.assertFalse(
            RepairPacketGate.objects
            .filter(packet__in=packets)
            .exclude(status=GateStatus.PENDING)
            .exists()
        )
        self.assertEqual(
            LockoutPoint.objects.filter(gate__packet__in=packets).count(), 6
        )

        self.assertEqual(
            WorkOrderPart.objects.filter(work_order__in=scenario_work_orders).count(),
            22,
        )
        self.assertEqual(
            WorkOrderPart.objects.filter(
                work_order__in=scenario_work_orders,
                allocation_status=WorkOrderPart.ALLOCATION_INSUFFICIENT,
            ).count(),
            4,
        )
        dependencies = WorkOrderDependency.objects.select_related(
            'predecessor', 'successor'
        )
        self.assertEqual(dependencies.count(), 4)
        for dependency in dependencies:
            with self.subTest(dependency=dependency.pk):
                self.assertEqual(dependency.dependency_type, 'FS')
                self.assertEqual(
                    dependency.predecessor.parent_id, dependency.successor_id
                )
                self.assertLessEqual(
                    dependency.predecessor.scheduled_end,
                    dependency.successor.scheduled_start,
                )

    def test_dry_run_rolls_back_all_records(self):
        """Dry-run executes the complete loader without retaining writes."""
        initial = {
            'maintenance': AssetMaintenanceRecord.objects.count(),
            'cards': WorkOrder.objects.count(),
            'packets': RepairPacket.objects.count(),
            'calendars': WorkingCalendar.objects.count(),
            'templates': SafetyGateTemplate.objects.count(),
        }

        self.load_workflow(dry_run=True)

        self.assertEqual(AssetMaintenanceRecord.objects.count(), initial['maintenance'])
        self.assertEqual(WorkOrder.objects.count(), initial['cards'])
        self.assertEqual(RepairPacket.objects.count(), initial['packets'])
        self.assertEqual(WorkingCalendar.objects.count(), initial['calendars'])
        self.assertEqual(SafetyGateTemplate.objects.count(), initial['templates'])

    def test_loader_is_idempotent(self):
        """Rerunning does not duplicate history, scenarios, gates, or links."""
        self.load_workflow()
        counts = {
            'maintenance': AssetMaintenanceRecord.objects.count(),
            'cards': WorkOrder.objects.count(),
            'packets': RepairPacket.objects.count(),
            'gates': RepairPacketGate.objects.count(),
            'lockouts': LockoutPoint.objects.count(),
            'parts': WorkOrderPart.objects.count(),
            'dependencies': WorkOrderDependency.objects.count(),
            'calendars': WorkingCalendar.objects.count(),
            'templates': SafetyGateTemplate.objects.count(),
        }

        self.load_workflow()

        self.assertEqual(AssetMaintenanceRecord.objects.count(), counts['maintenance'])
        self.assertEqual(WorkOrder.objects.count(), counts['cards'])
        self.assertEqual(RepairPacket.objects.count(), counts['packets'])
        self.assertEqual(RepairPacketGate.objects.count(), counts['gates'])
        self.assertEqual(LockoutPoint.objects.count(), counts['lockouts'])
        self.assertEqual(WorkOrderPart.objects.count(), counts['parts'])
        self.assertEqual(WorkOrderDependency.objects.count(), counts['dependencies'])
        self.assertEqual(WorkingCalendar.objects.count(), counts['calendars'])
        self.assertEqual(SafetyGateTemplate.objects.count(), counts['templates'])

    def test_normal_rerun_preserves_operator_edits(self):
        """Normal reruns do not overwrite active schedule, state, assignment, or gate edits."""
        self.load_workflow()
        work_order = WorkOrder.objects.get(reference='WO-WW-R-003')
        steven = get_user_model().objects.get(username='steven')
        edited_start = work_order.scheduled_start + datetime.timedelta(days=9)
        edited_end = work_order.scheduled_end + datetime.timedelta(days=9)
        work_order.scheduled_start = edited_start
        work_order.scheduled_end = edited_end
        work_order.lifecycle_status = WorkOrderLifecycle.IN_PROGRESS
        work_order.status = WorkOrder.STATUS_IN_PROGRESS
        work_order.assigned_to = steven
        work_order.save(
            update_fields=[
                'scheduled_start',
                'scheduled_end',
                'lifecycle_status',
                'status',
                'assigned_to',
            ]
        )
        gate = work_order.repair_packet.gates.first()
        gate.status = GateStatus.WAIVED
        gate.waived_by = steven
        gate.waived_at = timezone.now()
        gate.waiver_reason = 'Operator-entered training waiver'
        gate.waiver_authority = 'Shift supervisor'
        gate.save(
            update_fields=[
                'status',
                'waived_by',
                'waived_at',
                'waiver_reason',
                'waiver_authority',
            ]
        )

        self.load_workflow()

        work_order.refresh_from_db()
        gate.refresh_from_db()
        self.assertEqual(work_order.scheduled_start, edited_start)
        self.assertEqual(work_order.scheduled_end, edited_end)
        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS)
        self.assertEqual(work_order.status, WorkOrder.STATUS_IN_PROGRESS)
        self.assertEqual(work_order.assigned_to, steven)
        self.assertEqual(gate.status, GateStatus.WAIVED)
        self.assertEqual(gate.waiver_reason, 'Operator-entered training waiver')

    def test_explicit_reset_recreates_only_open_scenarios(self):
        """Reset restores active scenarios while preserving historical records."""
        self.load_workflow()
        history = AssetMaintenanceRecord.objects.get(
            work_order__reference='WO-WW-H-001'
        )
        original_history_pk = history.pk
        work_order = WorkOrder.objects.get(reference='WO-WW-R-001')
        original_work_order_pk = work_order.pk
        work_order.scheduled_start += datetime.timedelta(days=9)
        work_order.scheduled_end += datetime.timedelta(days=9)
        work_order.save(update_fields=['scheduled_start', 'scheduled_end'])

        self.load_workflow(reset=True)

        history.refresh_from_db()
        work_order = WorkOrder.objects.get(reference='WO-WW-R-001')
        self.assertEqual(history.pk, original_history_pk)
        self.assertNotEqual(work_order.pk, original_work_order_pk)
        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.PLANNED)
        self.assertEqual(work_order.status, WorkOrder.STATUS_IN_PROGRESS)
        self.assertEqual(len(self.workflow_cards('repair_scenario')), 10)
        self.assertEqual(len(self.workflow_cards('procurement')), 4)

    def test_refuses_unowned_reference_collision(self):
        """An existing unrelated card cannot be adopted by reference."""
        WorkOrder.objects.create(
            reference='WO-WW-R-001',
            title='Technician-authored card',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )

        with self.assertRaisesMessage(CommandError, 'not owned'):
            self.load_workflow()

    def test_creates_required_loto_template_when_missing(self):
        """The workflow is self-contained when the core LOTO template is absent."""
        SafetyGateTemplate.objects.filter(name='Electrical Lockout/Tagout').delete()

        self.load_workflow()

        self.assertTrue(
            SafetyGateTemplate.objects.filter(
                name='Electrical Lockout/Tagout', gate_type='loto'
            ).exists()
        )
        self.assertEqual(LockoutPoint.objects.count(), 6)

    def test_adopts_existing_default_calendar_without_modifying_it(self):
        """A populated site calendar governs unscoped demo work without takeover."""
        windows = {
            str(day): [['07:00', '11:30'], ['12:00', '15:30']] for day in range(5)
        }
        existing = WorkingCalendar.objects.create(
            name='Plant operations calendar',
            timezone='America/Chicago',
            windows=windows,
            holidays=['2026-12-25'],
            is_default=True,
        )

        self.load_workflow()

        existing.refresh_from_db()
        self.assertEqual(existing.name, 'Plant operations calendar')
        self.assertEqual(existing.holidays, ['2026-12-25'])
        self.assertFalse(
            WorkingCalendar.objects.filter(
                name='WW Demo - Water Maintenance Day Shift'
            ).exists()
        )
        self.assertEqual(WorkingCalendar.objects.count(), 4)

    def test_reset_ignores_tagged_cards_outside_manifest_namespace(self):
        """Reset never treats tags alone as authority to delete another card."""
        foreign = WorkOrder.objects.create(
            reference='USER-WW-TRAINING',
            title='Operator-authored training card',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            tags=['demo', 'water_wastewater', DATASET_TAG, 'repair_scenario'],
        )
        self.load_workflow()

        self.load_workflow(reset=True)

        self.assertTrue(WorkOrder.objects.filter(pk=foreign.pk).exists())

    def test_refuses_same_name_unowned_safety_template(self):
        """A same-name template without the dataset marker is never adopted."""
        SafetyGateTemplate.objects.create(
            name='WW Demo - Process/Basin Isolation',
            gate_type='isolation',
            applies_to={'fault_keywords': ['unrelated']},
        )

        with self.assertRaisesMessage(CommandError, 'not owned'):
            self.load_workflow()

    def test_rejects_invalid_schedule_anchor(self):
        """Schedule anchors must use an unambiguous ISO date."""
        with self.assertRaisesMessage(CommandError, 'Invalid schedule anchor'):
            self.load_workflow(anchor='07/27/2026')


class WaterWorkflowProfileCoverageTest(WaterWorkflowFixture):
    """Every owned record carries a detail profile, and enrichment applies it.

    The manifest is now complete, so ``--require-complete-profiles`` is a
    falsifiable statement rather than an aspiration: adding a record without a
    profile fails the load instead of quietly shipping a blank detail page.
    """

    def test_every_owned_record_is_enriched(self):
        """No owned card is left without a profile."""
        self.load_workflow(enrich=True, require_complete=True)

        work_orders = self.workflow_cards()
        self.assertEqual(len(work_orders), 44)
        without_component = [
            work_order.reference
            for work_order in work_orders
            if not work_order.affected_component
        ]
        self.assertEqual(without_component, [])

    def test_active_repairs_carry_findings_and_history_does_not(self):
        """A profile may only assert what its record's class supports."""
        self.load_workflow(enrich=True, require_complete=True)

        for work_order in self.workflow_cards('repair_scenario'):
            with self.subTest(work_order=work_order.reference):
                self.assertTrue(work_order.repair_packet.findings.exists())

        for work_order in self.workflow_cards('maintenance_history'):
            with self.subTest(work_order=work_order.reference):
                self.assertFalse(hasattr(work_order, 'repair_packet'))

    def test_approved_scope_only_where_work_was_agreed(self):
        """A backlog repair has observations, not an approved scope."""
        self.load_workflow(enrich=True, require_complete=True)

        for work_order in self.workflow_cards('repair_scenario'):
            approved = work_order.repair_packet.approved_scopes.filter(
                superseded_at__isnull=True
            ).exists()
            with self.subTest(work_order=work_order.reference):
                self.assertEqual(
                    approved, work_order.status != WorkOrder.STATUS_BACKLOG
                )

    def test_enrichment_rerun_writes_nothing_new(self):
        """A second pass updates in place rather than duplicating."""
        self.load_workflow(enrich=True, require_complete=True)
        packet = self.workflow_cards('repair_scenario')[0].repair_packet
        findings = packet.findings.count()
        scopes = packet.approved_scopes.count()

        self.load_workflow(enrich=True, require_complete=True)

        packet.refresh_from_db()
        self.assertEqual(packet.findings.count(), findings)
        self.assertEqual(packet.approved_scopes.count(), scopes)

    def test_a_record_without_a_profile_fails_the_load(self):
        """The completeness gate is what keeps the manifest honest."""
        WorkOrder.objects.create(
            reference='WO-WW-R-999',
            title='Unprofiled owned record',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            tags=['demo', 'water_wastewater', DATASET_TAG, 'repair_scenario'],
        )

        with self.assertRaisesMessage(CommandError, 'WO-WW-R-999'):
            self.load_workflow(enrich=True, require_complete=True)
