"""Job-kit planning models for maintenance work orders."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .procedure_models import FulfillmentMode, ProcedureResourceKind


class JobKitStatus(models.TextChoices):
    """Lifecycle states for a maintenance job kit."""

    DRAFT = 'draft', _('Draft')
    SHORT = 'short', _('Short')
    READY = 'ready', _('Ready')
    STAGED = 'staged', _('Staged')
    RELEASED = 'released', _('Released')
    CLOSED = 'closed', _('Closed')
    CANCELED = 'canceled', _('Canceled')


class JobKitAllocationStatus(models.TextChoices):
    """Lifecycle states for a real maintenance stock allocation."""

    RESERVED = 'reserved', _('Reserved')
    STAGED = 'staged', _('Staged')
    ISSUED = 'issued', _('Issued')
    CONSUMED = 'consumed', _('Consumed')
    RETURNED = 'returned', _('Returned')
    RELEASED = 'released', _('Released')
    EXCEPTION = 'exception', _('Exception')


# Only these states count as a live promise against stock availability.
ACTIVE_ALLOCATION_STATUSES = (
    JobKitAllocationStatus.RESERVED,
    JobKitAllocationStatus.STAGED,
    JobKitAllocationStatus.ISSUED,
)


class JobKit(models.Model):
    """Planning container for the resources required by one work order."""

    work_order = models.OneToOneField(
        'tasks.KanbanCard', on_delete=models.CASCADE, related_name='job_kit'
    )
    status = models.CharField(
        max_length=16,
        choices=JobKitStatus.choices,
        default=JobKitStatus.DRAFT,
        db_index=True,
    )
    version = models.PositiveIntegerField(default=1)
    source_application_hash = models.CharField(max_length=64, blank=True)
    built_at = models.DateTimeField(null=True, blank=True)
    staged_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    staging_location = models.ForeignKey(
        'stock.StockLocation', null=True, blank=True, on_delete=models.PROTECT
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'Job kit for {self.work_order}'


class JobKitLine(models.Model):
    """Planned resource requirement within a maintenance job kit."""

    kit = models.ForeignKey(JobKit, on_delete=models.CASCADE, related_name='lines')
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=ProcedureResourceKind.choices)
    requested_part = models.ForeignKey(
        'part.Part', on_delete=models.PROTECT, related_name='job_kit_requests'
    )
    selected_part = models.ForeignKey(
        'part.Part', on_delete=models.PROTECT, related_name='job_kit_selections'
    )
    required_quantity = models.DecimalField(max_digits=15, decimal_places=5)
    required = models.BooleanField(default=True)
    fulfillment_mode = models.CharField(max_length=24, choices=FulfillmentMode.choices)
    substitution_policy = models.CharField(max_length=20, default='none')
    requires_scan = models.BooleanField(default=False)
    source_requirement = models.ForeignKey(
        'tasks.ProcedureResourceRequirement',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    source_snapshot = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['kit', 'sequence'], name='tasks_jobkitline_kit_sequence_uniq'
            ),
            models.UniqueConstraint(
                fields=['kit', 'source_requirement'],
                condition=Q(source='procedure'),
                name='tasks_jobkitline_kit_source_req_uniq',
            ),
            models.CheckConstraint(
                condition=Q(required_quantity__gt=0),
                name='tasks_jobkitline_qty_positive',
            ),
            models.CheckConstraint(
                condition=Q(sequence__gt=0), name='tasks_jobkitline_sequence_positive'
            ),
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.kit} line {self.sequence}'


class JobKitShortage(models.Model):
    """Unfulfilled quantity recorded against a planned job-kit line."""

    line = models.ForeignKey(
        JobKitLine, on_delete=models.CASCADE, related_name='shortages'
    )
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    status = models.CharField(
        max_length=16,
        choices=[
            ('open', 'Open'),
            ('requested', 'Requested'),
            ('ordered', 'Ordered'),
            ('partial', 'Partially Received'),
            ('received', 'Received'),
            ('canceled', 'Canceled'),
        ],
        default='open',
        db_index=True,
    )
    purchase_order_line = models.ForeignKey(
        'order.PurchaseOrderLineItem', null=True, blank=True, on_delete=models.PROTECT
    )
    approval = models.ForeignKey(
        'approvals.Approval', null=True, blank=True, on_delete=models.PROTECT
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'Shortage for {self.line}: {self.quantity}'


class JobKitSubstitutionStatus(models.TextChoices):
    """Decision states for a proposed alternate part."""

    PROPOSED = 'proposed', _('Proposed')
    APPROVED = 'approved', _('Approved')
    REJECTED = 'rejected', _('Rejected')
    REVOKED = 'revoked', _('Revoked')


class JobKitSubstitution(models.Model):
    """A governed proposal to fulfil a line with an alternate part.

    Only a policy-authorized human/executor decision may set the line's
    ``selected_part``; proposing an alternate never silently replaces a part.
    """

    line = models.ForeignKey(
        JobKitLine, on_delete=models.CASCADE, related_name='substitutions'
    )
    requested_part = models.ForeignKey(
        'part.Part', on_delete=models.PROTECT, related_name='+'
    )
    proposed_part = models.ForeignKey(
        'part.Part', on_delete=models.PROTECT, related_name='+'
    )
    basis = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=JobKitSubstitutionStatus.choices,
        default=JobKitSubstitutionStatus.PROPOSED,
        db_index=True,
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    approval = models.ForeignKey(
        'approvals.Approval', null=True, blank=True, on_delete=models.PROTECT
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return (
            f'JobKitSubstitution#{self.pk} (line {self.line_id}, '
            f'{self.requested_part_id}->{self.proposed_part_id}, {self.status})'
        )


class JobKitAllocation(models.Model):
    """A real reservation of a specific stock item against a Job Kit line.

    Active states (reserved/staged/issued) participate in the shared
    ``StockItem`` availability calculation alongside build, sales, and transfer
    allocations. Consumed/returned/released rows are terminal and do not remain
    double-counted against unallocated stock.
    """

    line = models.ForeignKey(
        JobKitLine, on_delete=models.CASCADE, related_name='allocations'
    )
    stock_item = models.ForeignKey(
        'stock.StockItem', on_delete=models.PROTECT, related_name='job_kit_allocations'
    )
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    status = models.CharField(
        max_length=16,
        choices=JobKitAllocationStatus.choices,
        default=JobKitAllocationStatus.RESERVED,
        db_index=True,
    )
    source_location_snapshot = models.JSONField(default=dict)
    scan_proof = models.JSONField(default=dict, blank=True)
    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    reserved_at = models.DateTimeField(auto_now_add=True)
    staged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    staged_at = models.DateTimeField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    disposed_at = models.DateTimeField(null=True, blank=True)
    stock_tracking_id = models.IntegerField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=128)

    class Meta:
        """Model metadata."""

        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name='tasks_jobkitallocation_qty_positive'
            ),
            models.UniqueConstraint(
                fields=['line', 'stock_item'],
                condition=Q(status__in=[s.value for s in ACTIVE_ALLOCATION_STATUSES]),
                name='tasks_jobkitallocation_active_line_stock_uniq',
            ),
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return (
            f'JobKitAllocation#{self.pk} (line {self.line_id}, '
            f'stock {self.stock_item_id}, {self.status})'
        )
