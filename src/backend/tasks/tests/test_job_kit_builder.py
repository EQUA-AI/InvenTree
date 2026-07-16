"""Tests for the deterministic Job Kit build service (planning layer)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from company.models import Company
from part.models import Part
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitLine,
    JobKitStatus,
    KanbanCard,
    Procedure,
    ProcedureResourceKind,
    ProcedureResourceRequirement,
    ProcedureRevision,
    ProcedureRevisionStatus,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kits import JobKitBuildError, build_job_kit
from tasks.services.procedure_execution import apply_procedure_revision
from tasks.services.work_orders import StaleVersion


class JobKitBuilderServiceTest(TestCase):
    """Exercise deterministic, idempotent, governed Job Kit construction."""

    def setUp(self):
        self.customer = Company.objects.create(name='ME4 Customer', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='me4-planner', email='me4@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = KanbanCard.objects.create(
            title='ME4 work order', status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM, customer=self.customer,
            assigned_to=self.actor, work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.procedure = Procedure.objects.create(
            code='ME4-PM', name='ME4 procedure', customer=self.customer,
            created_by=self.actor,
        )
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure, revision=1,
            status=ProcedureRevisionStatus.PUBLISHED,
            work_order_type=WorkOrderType.PREVENTIVE, created_by=self.actor,
            published_by=self.actor, published_at=timezone.now(),
        )
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])
        self.part_a = Part.objects.create(name='Filter', description='Filter part')
        self.part_b = Part.objects.create(name='Gasket', description='Gasket part')

    def add_requirement(self, part, sequence, quantity, **overrides):
        values = {
            'revision': self.revision, 'sequence': sequence,
            'kind': ProcedureResourceKind.PART, 'part': part,
            'quantity': Decimal(quantity),
            'fulfillment_mode': FulfillmentMode.RESERVE_CONSUME,
        }
        values.update(overrides)
        return ProcedureResourceRequirement.objects.create(**values)

    def apply_procedure(self, key='apply-me4'):
        return apply_procedure_revision(
            work_order_id=self.work_order.pk, revision_id=self.revision.pk,
            actor=self.actor, expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def build(self, key='build-me4'):
        return build_job_kit(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_build_rolls_up_requirements_into_lines(self):
        self.add_requirement(self.part_a, 1, '2')
        self.add_requirement(self.part_b, 2, '5', required=False)
        self.apply_procedure()

        kit = self.build()

        self.assertEqual(kit.work_order_id, self.work_order.pk)
        self.assertEqual(kit.status, JobKitStatus.DRAFT)
        self.assertIsNotNone(kit.built_at)
        lines = list(kit.lines.order_by('sequence'))
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].requested_part_id, self.part_a.pk)
        self.assertEqual(lines[0].selected_part_id, self.part_a.pk)
        self.assertEqual(lines[0].required_quantity, Decimal('2'))
        self.assertTrue(lines[0].required)
        self.assertEqual(lines[0].source, 'procedure')
        self.assertFalse(lines[1].required)
        self.assertEqual(kit.source_application_hash,
                         self.work_order.procedure_applications.get().snapshot_hash)

    def test_rebuild_is_idempotent_and_creates_no_duplicates(self):
        self.add_requirement(self.part_a, 1, '2')
        self.apply_procedure()

        first = self.build(key='build-1')
        second = self.build(key='build-2')  # different key -> real rebuild path

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(JobKit.objects.count(), 1)
        self.assertEqual(JobKitLine.objects.filter(kit=first).count(), 1)

    def test_exact_replay_returns_same_kit_without_new_command(self):
        self.add_requirement(self.part_a, 1, '2')
        self.apply_procedure()

        first = self.build(key='same-key')
        replay = self.build(key='same-key')

        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(
            self.work_order.commands.filter(command='build_job_kit').count(), 1
        )
        self.assertEqual(
            self.work_order.events.filter(event_type='JOB_KIT_BUILT').count(), 1
        )

    def test_manual_lines_are_preserved_across_rebuild(self):
        self.add_requirement(self.part_a, 1, '2')
        self.apply_procedure()
        kit = self.build(key='build-1')
        manual = JobKitLine.objects.create(
            kit=kit, sequence=99, kind=ProcedureResourceKind.CONSUMABLE,
            requested_part=self.part_b, selected_part=self.part_b,
            required_quantity=Decimal('1'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME, source='manual',
        )

        self.build(key='build-2')

        self.assertTrue(JobKitLine.objects.filter(pk=manual.pk).exists())
        self.assertEqual(kit.lines.count(), 2)

    def test_build_without_application_fails_closed(self):
        with self.assertRaises(JobKitBuildError):
            self.build()

    def test_stale_version_is_rejected(self):
        self.add_requirement(self.part_a, 1, '2')
        self.apply_procedure()
        with self.assertRaises(StaleVersion):
            build_job_kit(
                work_order_id=self.work_order.pk, actor=self.actor,
                expected_version=self.work_order.lifecycle_version + 5,
                idempotency_key='stale',
            )

    def test_permission_is_required(self):
        self.add_requirement(self.part_a, 1, '2')
        self.apply_procedure()
        planner = get_user_model().objects.create_user(username='no-perms')
        planner.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        with self.assertRaises(PermissionDenied):
            build_job_kit(
                work_order_id=self.work_order.pk, actor=planner,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='noperm',
            )
