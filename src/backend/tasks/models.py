"""Database models for the tasks application."""

from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

import InvenTree.models


class WorkOrderLifecycle(models.TextChoices):
    """Lifecycle states for maintenance work orders."""

    DRAFT = 'draft', _('Draft')
    PLANNED = 'planned', _('Planned')
    READY = 'ready', _('Ready')
    IN_PROGRESS = 'in_progress', _('In Progress')
    ON_HOLD = 'on_hold', _('On Hold')
    VERIFYING = 'verifying', _('Verifying')
    COMPLETED = 'completed', _('Completed')
    CANCELED = 'canceled', _('Canceled')


class WorkOrderType(models.TextChoices):
    """Supported maintenance work-order types."""

    CORRECTIVE = 'corrective', _('Corrective')
    PREVENTIVE = 'preventive', _('Preventive')
    INSPECTION = 'inspection', _('Inspection')
    CALIBRATION = 'calibration', _('Calibration')
    OTHER = 'other', _('Other')


class KanbanCard(InvenTree.models.InvenTreeAttachmentMixin, models.Model):
    """Persistent representation of a Kanban card."""

    STATUS_BACKLOG = 'backlog'
    STATUS_IN_PROGRESS = 'in-progress'
    STATUS_REVIEW = 'review'
    STATUS_DONE = 'done'

    PRIORITY_LOW = 'low'
    PRIORITY_MEDIUM = 'medium'
    PRIORITY_HIGH = 'high'

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Low'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_HIGH, 'High'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=32, db_index=True)
    priority = models.CharField(max_length=16, choices=PRIORITY_CHOICES, db_index=True)
    due_date = models.DateField(null=True, blank=True)
    assignee = models.CharField(max_length=120, blank=True)
    tags = ArrayField(
        base_field=models.CharField(max_length=32), default=list, blank=True
    )
    company = models.CharField(max_length=120, blank=True)
    company_contact_name = models.CharField(max_length=120, blank=True)
    company_contact_phone = models.CharField(max_length=64, blank=True)
    job_number = models.CharField(max_length=64, blank=True)
    service_quote = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reference = models.CharField(
        max_length=32, null=True, blank=True, unique=True, db_index=True
    )
    lifecycle_status = models.CharField(
        max_length=20,
        choices=WorkOrderLifecycle.choices,
        default=WorkOrderLifecycle.DRAFT,
        db_index=True,
    )
    work_order_type = models.CharField(
        max_length=20,
        choices=WorkOrderType.choices,
        default=WorkOrderType.CORRECTIVE,
        db_index=True,
    )
    machine = models.ForeignKey(
        'assets.AssetMachine',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='work_orders',
    )
    customer = models.ForeignKey(
        'company.Company',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='maintenance_work_orders',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_work_orders',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='requested_work_orders',
    )
    scheduled_start = models.DateTimeField(null=True, blank=True)
    scheduled_end = models.DateTimeField(null=True, blank=True)
    actual_started_at = models.DateTimeField(null=True, blank=True)
    actual_completed_at = models.DateTimeField(null=True, blank=True)
    estimated_minutes = models.PositiveIntegerField(null=True, blank=True)
    lifecycle_version = models.PositiveIntegerField(default=1)
    hold_reason = models.TextField(blank=True)

    class Meta:
        """Model metadata."""

        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['machine', 'lifecycle_status'],
                name='tasks_wo_machine_lifecycle',
            ),
            models.Index(
                fields=['assigned_to', 'lifecycle_status'],
                name='tasks_wo_assignee_lifecycle',
            ),
            models.Index(fields=['due_date'], name='tasks_wo_due_date'),
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return self.title


class KanbanCardPart(models.Model):
    """Links a Part to a KanbanCard with a required quantity and allocation tracking."""

    ALLOCATION_NONE = 'none'
    ALLOCATION_PARTIAL = 'partial'
    ALLOCATION_FULL = 'full'
    ALLOCATION_INSUFFICIENT = 'insufficient'

    ALLOCATION_STATUS_CHOICES = [
        (ALLOCATION_NONE, 'None'),
        (ALLOCATION_PARTIAL, 'Partial'),
        (ALLOCATION_FULL, 'Full'),
        (ALLOCATION_INSUFFICIENT, 'Insufficient'),
    ]

    card = models.ForeignKey(
        KanbanCard, on_delete=models.CASCADE, related_name='card_parts'
    )
    part = models.ForeignKey(
        'part.Part', on_delete=models.CASCADE, related_name='kanban_allocations'
    )
    quantity = models.DecimalField(
        max_digits=15,
        decimal_places=5,
        default=Decimal('1'),
        help_text='Required quantity of this part for the card',
    )
    allocated_quantity = models.DecimalField(
        max_digits=15,
        decimal_places=5,
        default=Decimal('0'),
        help_text='Quantity successfully reserved/allocated from stock',
    )
    allocation_status = models.CharField(
        max_length=16,
        choices=ALLOCATION_STATUS_CHOICES,
        default=ALLOCATION_NONE,
        db_index=True,
    )
    allocation_note = models.TextField(
        blank=True, help_text='Notes about stock availability or allocation issues'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        unique_together = [('card', 'part')]
        ordering = ['created_at']

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.card.title} - {self.part.name} x{self.quantity}'

    def check_and_allocate(self):
        """Check stock availability and allocate if possible.

        Returns a dict with the allocation result.
        """
        from stock.models import StockItem

        available = Decimal('0')
        stock_items = StockItem.objects.filter(part=self.part).filter(
            StockItem.IN_STOCK_FILTER
        )

        for item in stock_items:
            available += item.unallocated_quantity()

        needed = self.quantity

        if available >= needed:
            self.allocated_quantity = needed
            self.allocation_status = self.ALLOCATION_FULL
            self.allocation_note = f'Fully allocated: {needed} available in stock'
        elif available > 0:
            self.allocated_quantity = available
            self.allocation_status = self.ALLOCATION_PARTIAL
            shortage = needed - available
            self.allocation_note = (
                f'Partially allocated: {available} of {needed} available. '
                f'Short by {shortage}'
            )
        else:
            self.allocated_quantity = Decimal('0')
            self.allocation_status = self.ALLOCATION_INSUFFICIENT
            self.allocation_note = f'No stock available. Need {needed}'

        self.save()

        return {
            'part_id': self.part.pk,
            'part_name': self.part.name,
            'quantity_needed': float(needed),
            'quantity_available': float(available),
            'quantity_allocated': float(self.allocated_quantity),
            'allocation_status': self.allocation_status,
            'note': self.allocation_note,
        }


# Re-export split work-order models for compatibility with ``tasks.models`` imports.
# Re-export job-kit models for compatibility with ``tasks.models`` imports.
# Re-export closeout-automation models for compatibility with ``tasks.models``.
from tasks.closeout_models import (  # noqa: F401
    ACTIVE_CAPTURE_STATUSES,
    CloseoutAmendment,
    CloseoutAmendmentStatus,
    CloseoutCapture,
    CloseoutCaptureRevision,
    CloseoutCaptureStatus,
    CloseoutEffect,
    CloseoutEffectStatus,
    CloseoutFieldDecision,
    CloseoutLearningDraft,
    CloseoutPartUsage,
    CloseoutPartUsageState,
    CloseoutProposal,
    CloseoutProposalStatus,
    CloseoutReading,
    CloseoutReadingEvidence,
    CloseoutReadingState,
    CloseoutSourceType,
    PartUsageDisposition,
)
from tasks.jobkit_models import (  # noqa: F401
    ACTIVE_ALLOCATION_STATUSES,
    JobKit,
    JobKitAllocation,
    JobKitAllocationStatus,
    JobKitLine,
    JobKitShortage,
    JobKitStatus,
    JobKitSubstitution,
    JobKitSubstitutionStatus,
)

# Re-export procedure models for compatibility with ``tasks.models`` imports.
from tasks.procedure_models import (  # noqa: F401
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
from tasks.workorder_models import (  # noqa: F401
    WorkOrderCloseout,
    WorkOrderCommand,
    WorkOrderDeviation,
    WorkOrderEvent,
)
