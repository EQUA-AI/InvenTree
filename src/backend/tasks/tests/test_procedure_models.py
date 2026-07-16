"""Tests for governed maintenance procedure domain models."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from tasks.models import (
    FulfillmentMode,
    Procedure,
    ProcedureApplicability,
    ProcedureFieldDecision,
    ProcedureResourceKind,
    ProcedureResourceRequirement,
    ProcedureRevision,
    ProcedureRevisionSource,
    ProcedureRevisionStatus,
    ProcedureStep,
    ProcedureStepType,
    StepExecutionStatus,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
    WorkOrderType,
)


class ProcedureModelTest(TestCase):
    """Exercise core procedure constraints and the public model import surface."""

    def setUp(self):
        """Create the common author and procedure family."""
        self.user = get_user_model().objects.create_user(username='procedure-author')
        self.procedure = Procedure.objects.create(
            code='PM-001', name='Routine inspection', created_by=self.user
        )

    def create_revision(self, revision=1, **kwargs):
        """Create a procedure revision with required defaults."""
        values = {
            'procedure': self.procedure,
            'revision': revision,
            'work_order_type': WorkOrderType.PREVENTIVE,
            'created_by': self.user,
        }
        values.update(kwargs)
        return ProcedureRevision.objects.create(**values)

    def test_create_procedure_and_revision(self):
        """A procedure and its first draft revision can be persisted."""
        revision = self.create_revision()

        self.assertEqual(revision.procedure, self.procedure)
        self.assertEqual(revision.status, ProcedureRevisionStatus.DRAFT)

    def test_procedure_revision_number_is_unique(self):
        """A revision number cannot repeat within one procedure."""
        self.create_revision()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_revision()

    def test_only_one_published_revision_per_procedure(self):
        """A procedure cannot have two revisions marked as published."""
        published_metadata = {
            'status': ProcedureRevisionStatus.PUBLISHED,
            'published_by': self.user,
            'published_at': timezone.now(),
        }
        self.create_revision(1, **published_metadata)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_revision(2, **published_metadata)

    def test_revision_must_be_positive(self):
        """The database rejects revision zero."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_revision(0)

    def test_step_sequence_must_be_positive(self):
        """The database rejects step sequence zero."""
        revision = self.create_revision()

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcedureStep.objects.create(
                revision=revision,
                sequence=0,
                step_type=ProcedureStepType.INSTRUCTION,
                title='Inspect',
                instruction='Inspect the machine.',
            )

    def test_step_sequence_is_unique_per_revision(self):
        """A sequence number cannot repeat within one revision."""
        revision = self.create_revision()
        step_values = {
            'revision': revision,
            'sequence': 1,
            'step_type': ProcedureStepType.INSTRUCTION,
            'title': 'Inspect',
            'instruction': 'Inspect the machine.',
        }
        ProcedureStep.objects.create(**step_values)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcedureStep.objects.create(**step_values)

    def test_all_procedure_names_are_reexported(self):
        """Every new model and choice type is exposed by tasks.models."""
        exported_names = (
            FulfillmentMode,
            Procedure,
            ProcedureApplicability,
            ProcedureFieldDecision,
            ProcedureResourceKind,
            ProcedureResourceRequirement,
            ProcedureRevision,
            ProcedureRevisionSource,
            ProcedureRevisionStatus,
            ProcedureStep,
            ProcedureStepType,
            StepExecutionStatus,
            WorkOrderProcedureApplication,
            WorkOrderStepExecution,
        )

        self.assertTrue(all(exported_names))
