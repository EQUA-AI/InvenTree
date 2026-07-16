"""Tests for maintenance job-kit planning models."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from part.models import Part
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitLine,
    JobKitShortage,
    JobKitStatus,
    KanbanCard,
    Procedure,
    ProcedureResourceKind,
    ProcedureResourceRequirement,
    ProcedureRevision,
    WorkOrderType,
)


class JobKitModelTest(TestCase):
    """Exercise job-kit planning defaults, relationships, and constraints."""

    def setUp(self):
        """Create a work order, parts, and a procedure resource requirement."""
        self.user = get_user_model().objects.create_user(username='job-kit-planner')
        self.work_order = KanbanCard.objects.create(
            title='Prepare maintenance kit',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
        )
        self.requested_part = Part.objects.create(
            name='Requested bearing', description='Requested job-kit part'
        )
        self.selected_part = Part.objects.create(
            name='Selected bearing', description='Selected job-kit part'
        )
        self.procedure = Procedure.objects.create(
            code='KIT-001', name='Job-kit procedure', created_by=self.user
        )
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=1,
            work_order_type=WorkOrderType.PREVENTIVE,
            created_by=self.user,
        )
        self.requirement = ProcedureResourceRequirement.objects.create(
            revision=self.revision,
            sequence=1,
            kind=ProcedureResourceKind.PART,
            part=self.requested_part,
            quantity=Decimal('2'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.user
        )

    def create_line(self, **overrides):
        """Create a job-kit line with valid required defaults."""
        values = {
            'kit': self.kit,
            'sequence': self.kit.lines.count() + 1,
            'kind': ProcedureResourceKind.PART,
            'requested_part': self.requested_part,
            'selected_part': self.selected_part,
            'required_quantity': Decimal('2'),
            'fulfillment_mode': FulfillmentMode.RESERVE_CONSUME,
            'source': 'manual',
        }
        values.update(overrides)
        return JobKitLine.objects.create(**values)

    def test_job_kit_defaults_to_draft(self):
        """A new job kit starts in the draft lifecycle state."""
        self.assertEqual(self.kit.status, JobKitStatus.DRAFT)
        self.assertEqual(self.kit.status, 'draft')

    def test_work_order_has_only_one_job_kit(self):
        """The database enforces one job kit per work order."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            JobKit.objects.create(work_order=self.work_order, created_by=self.user)

    def test_line_sequence_is_unique_per_kit(self):
        """A line sequence cannot repeat within one kit."""
        self.create_line(sequence=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_line(sequence=1)

    def test_procedure_source_requirement_is_unique_per_kit(self):
        """One procedure-derived line may represent each source requirement."""
        self.create_line(
            sequence=1,
            source='procedure',
            source_requirement=self.requirement,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_line(
                sequence=2,
                source='procedure',
                source_requirement=self.requirement,
            )

    def test_manual_lines_with_null_source_requirement_are_allowed(self):
        """Manual lines without a source requirement do not conflict."""
        first = self.create_line(sequence=1)
        second = self.create_line(sequence=2)

        self.assertIsNone(first.source_requirement)
        self.assertIsNone(second.source_requirement)
        self.assertEqual(self.kit.lines.count(), 2)

    def test_required_quantity_must_be_positive(self):
        """The database rejects a zero required quantity."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_line(required_quantity=Decimal('0'))

    def test_sequence_must_be_positive(self):
        """The database rejects sequence zero."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_line(sequence=0)

    def test_shortage_defaults_open_and_cascades_with_line(self):
        """A shortage starts open and is removed with its line."""
        line = self.create_line()
        shortage = JobKitShortage.objects.create(
            line=line, quantity=Decimal('1.5')
        )

        self.assertEqual(shortage.status, 'open')
        shortage_pk = shortage.pk
        line.delete()
        self.assertFalse(JobKitShortage.objects.filter(pk=shortage_pk).exists())
