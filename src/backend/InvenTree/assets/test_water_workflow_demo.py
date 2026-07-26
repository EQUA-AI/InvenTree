"""Tests for the water maintenance and repair workflow demo dataset."""

import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from tasks.models import (
    KanbanCard,
    KanbanCardDependency,
    KanbanCardPart,
    WorkingCalendar,
    WorkOrderLifecycle,
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


class WaterWorkflowDemoTest(TestCase):
    """Verify deterministic maintenance, repair, and scheduling demo records."""

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

    def load_workflow(self, *, dry_run=False, reset=False, anchor='2026-07-27'):
        """Load workflow records while keeping command output out of test logs."""
        call_command(
            'load_water_workflow_demo_data',
            dry_run=dry_run,
            reset_owned_scenarios=reset,
            schedule_anchor=anchor,
            stdout=StringIO(),
        )

    @staticmethod
    def workflow_cards(kind=None):
        """Return cards tagged as belonging to this workflow dataset."""
        cards = [
            card
            for card in KanbanCard.objects.all()
            if DATASET_TAG in set(card.tags or [])
        ]
        if kind:
            cards = [card for card in cards if kind in set(card.tags or [])]
        return cards

    def test_loads_complete_workflow_dataset(self):
        """All machines receive history, repair packets, cards, and schedules."""
        self.load_workflow()

        history_cards = self.workflow_cards('maintenance_history')
        scenario_cards = self.workflow_cards('repair_scenario')
        procurement_cards = self.workflow_cards('procurement')
        active_cards = [*scenario_cards, *procurement_cards]
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

        self.assertEqual(len(scenario_cards), 10)
        self.assertEqual(len(procurement_cards), 4)
        self.assertEqual(len(active_cards), 14)
        self.assertTrue(
            all(
                card.is_active
                and card.scheduled_start is not None
                and card.scheduled_end is not None
                for card in active_cards
            )
        )
        stage_counts = {
            status: sum(card.status == status for card in active_cards)
            for status in (
                KanbanCard.STATUS_BACKLOG,
                KanbanCard.STATUS_IN_PROGRESS,
                KanbanCard.STATUS_REVIEW,
            )
        }
        self.assertEqual(
            stage_counts,
            {
                KanbanCard.STATUS_BACKLOG: 5,
                KanbanCard.STATUS_IN_PROGRESS: 5,
                KanbanCard.STATUS_REVIEW: 4,
            },
        )
        for card in active_cards:
            with self.subTest(stage=card.status, reference=card.reference):
                self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.PLANNED)
                self.assertIsNone(card.actual_started_at)
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
            {card.pk for card in scenario_cards},
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
            KanbanCardPart.objects.filter(card__in=scenario_cards).count(), 22
        )
        self.assertEqual(
            KanbanCardPart.objects.filter(
                card__in=scenario_cards,
                allocation_status=KanbanCardPart.ALLOCATION_INSUFFICIENT,
            ).count(),
            4,
        )
        dependencies = KanbanCardDependency.objects.select_related(
            'from_card', 'to_card'
        )
        self.assertEqual(dependencies.count(), 4)
        for dependency in dependencies:
            with self.subTest(dependency=dependency.pk):
                self.assertEqual(dependency.dependency_type, 'FS')
                self.assertEqual(dependency.from_card.parent_id, dependency.to_card_id)
                self.assertLessEqual(
                    dependency.from_card.scheduled_end,
                    dependency.to_card.scheduled_start,
                )

    def test_dry_run_rolls_back_all_records(self):
        """Dry-run executes the complete loader without retaining writes."""
        initial = {
            'maintenance': AssetMaintenanceRecord.objects.count(),
            'cards': KanbanCard.objects.count(),
            'packets': RepairPacket.objects.count(),
            'calendars': WorkingCalendar.objects.count(),
            'templates': SafetyGateTemplate.objects.count(),
        }

        self.load_workflow(dry_run=True)

        self.assertEqual(AssetMaintenanceRecord.objects.count(), initial['maintenance'])
        self.assertEqual(KanbanCard.objects.count(), initial['cards'])
        self.assertEqual(RepairPacket.objects.count(), initial['packets'])
        self.assertEqual(WorkingCalendar.objects.count(), initial['calendars'])
        self.assertEqual(SafetyGateTemplate.objects.count(), initial['templates'])

    def test_loader_is_idempotent(self):
        """Rerunning does not duplicate history, scenarios, gates, or links."""
        self.load_workflow()
        counts = {
            'maintenance': AssetMaintenanceRecord.objects.count(),
            'cards': KanbanCard.objects.count(),
            'packets': RepairPacket.objects.count(),
            'gates': RepairPacketGate.objects.count(),
            'lockouts': LockoutPoint.objects.count(),
            'parts': KanbanCardPart.objects.count(),
            'dependencies': KanbanCardDependency.objects.count(),
            'calendars': WorkingCalendar.objects.count(),
            'templates': SafetyGateTemplate.objects.count(),
        }

        self.load_workflow()

        self.assertEqual(AssetMaintenanceRecord.objects.count(), counts['maintenance'])
        self.assertEqual(KanbanCard.objects.count(), counts['cards'])
        self.assertEqual(RepairPacket.objects.count(), counts['packets'])
        self.assertEqual(RepairPacketGate.objects.count(), counts['gates'])
        self.assertEqual(LockoutPoint.objects.count(), counts['lockouts'])
        self.assertEqual(KanbanCardPart.objects.count(), counts['parts'])
        self.assertEqual(KanbanCardDependency.objects.count(), counts['dependencies'])
        self.assertEqual(WorkingCalendar.objects.count(), counts['calendars'])
        self.assertEqual(SafetyGateTemplate.objects.count(), counts['templates'])

    def test_normal_rerun_preserves_operator_edits(self):
        """Normal reruns do not overwrite active schedule, state, assignment, or gate edits."""
        self.load_workflow()
        card = KanbanCard.objects.get(reference='WO-WW-R-003')
        steven = get_user_model().objects.get(username='steven')
        edited_start = card.scheduled_start + datetime.timedelta(days=9)
        edited_end = card.scheduled_end + datetime.timedelta(days=9)
        card.scheduled_start = edited_start
        card.scheduled_end = edited_end
        card.lifecycle_status = WorkOrderLifecycle.IN_PROGRESS
        card.status = KanbanCard.STATUS_IN_PROGRESS
        card.assigned_to = steven
        card.save(
            update_fields=[
                'scheduled_start',
                'scheduled_end',
                'lifecycle_status',
                'status',
                'assigned_to',
            ]
        )
        gate = card.repair_packet.gates.first()
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

        card.refresh_from_db()
        gate.refresh_from_db()
        self.assertEqual(card.scheduled_start, edited_start)
        self.assertEqual(card.scheduled_end, edited_end)
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS)
        self.assertEqual(card.status, KanbanCard.STATUS_IN_PROGRESS)
        self.assertEqual(card.assigned_to, steven)
        self.assertEqual(gate.status, GateStatus.WAIVED)
        self.assertEqual(gate.waiver_reason, 'Operator-entered training waiver')

    def test_explicit_reset_recreates_only_open_scenarios(self):
        """Reset restores active scenarios while preserving historical records."""
        self.load_workflow()
        history = AssetMaintenanceRecord.objects.get(
            work_order__reference='WO-WW-H-001'
        )
        original_history_pk = history.pk
        card = KanbanCard.objects.get(reference='WO-WW-R-001')
        original_card_pk = card.pk
        card.scheduled_start += datetime.timedelta(days=9)
        card.scheduled_end += datetime.timedelta(days=9)
        card.save(update_fields=['scheduled_start', 'scheduled_end'])

        self.load_workflow(reset=True)

        history.refresh_from_db()
        card = KanbanCard.objects.get(reference='WO-WW-R-001')
        self.assertEqual(history.pk, original_history_pk)
        self.assertNotEqual(card.pk, original_card_pk)
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.PLANNED)
        self.assertEqual(card.status, KanbanCard.STATUS_IN_PROGRESS)
        self.assertEqual(len(self.workflow_cards('repair_scenario')), 10)
        self.assertEqual(len(self.workflow_cards('procurement')), 4)

    def test_refuses_unowned_reference_collision(self):
        """An existing unrelated card cannot be adopted by reference."""
        KanbanCard.objects.create(
            reference='WO-WW-R-001',
            title='Technician-authored card',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
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
        foreign = KanbanCard.objects.create(
            reference='USER-WW-TRAINING',
            title='Operator-authored training card',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
            tags=['demo', 'water_wastewater', DATASET_TAG, 'repair_scenario'],
        )
        self.load_workflow()

        self.load_workflow(reset=True)

        self.assertTrue(KanbanCard.objects.filter(pk=foreign.pk).exists())

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
