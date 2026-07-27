"""Database models for the tasks application."""

from decimal import Decimal

from django.conf import settings
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
    tags = models.JSONField(default=list, blank=True)
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

    KIND_WORK_ORDER = 'work_order'
    KIND_SUBTASK = 'subtask'
    KIND_PROCUREMENT = 'procurement'
    KIND_CHOICES = [
        (KIND_WORK_ORDER, 'Work Order'),
        (KIND_SUBTASK, 'Subtask'),
        (KIND_PROCUREMENT, 'Procurement'),
    ]

    # A work order can fan out into child cards (§5.10), one of which may be a
    # procurement task raised from a parts shortfall. Depth is exactly one: a
    # child cannot itself have children (enforced in the command service). PROTECT
    # so a parent with children cannot be deleted out from under them.
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT, related_name='children'
    )
    card_kind = models.CharField(
        max_length=16, choices=KIND_CHOICES, default=KIND_WORK_ORDER, db_index=True
    )

    # The maintainable asset owns maintenance history, but a fault usually
    # concerns one part of it: a pump, a lamp bank, a rake chain, a membrane
    # train. Until a governed asset hierarchy exists, the component is recorded
    # alongside the machine rather than buried in free-text description, where
    # it could be neither filtered nor trusted.
    affected_component = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Affected Component'),
        help_text=_('The part of the asset this work concerns'),
    )
    affected_component_ref = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name=_('Affected Component Reference'),
        help_text=_('External/human identifier for the component'),
    )
    installed_part = models.ForeignKey(
        'assets.MachinePart',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='work_orders',
        verbose_name=_('Installed Part'),
        help_text=_('Installed part this work concerns, when one is mapped'),
    )

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


class KanbanColumn(models.Model):
    """A persisted board column (work-order status lane).

    Board columns used to live only in frontend ``useState``: adding, reordering
    or deleting a column never reached the server, so a refresh reset the board to
    four hardcoded defaults and any card whose ``status`` pointed at a custom
    column silently vanished (its status string matched no rendered column).

    Persisting them here fixes that. ``KanbanCard.status`` remains a free-text
    field that stores this column's ``key``; the seed migration creates the four
    original columns under their existing keys so every stored ``status`` keeps
    resolving. There is deliberately no FK from ``KanbanCard.status`` to this
    model yet -- that is a heavier migration tracked separately -- so nothing
    enforces referential integrity at the database level, and code that maps a
    card to its column must tolerate an unmatched key.
    """

    key = models.SlugField(
        max_length=32,
        unique=True,
        verbose_name=_('Key'),
        help_text=_('Stable identifier stored in KanbanCard.status'),
    )
    label = models.CharField(max_length=64, verbose_name=_('Label'))
    color = models.CharField(
        max_length=32,
        default='gray',
        blank=True,
        verbose_name=_('Color'),
        help_text=_('Mantine color name used for the column badge'),
    )
    order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        verbose_name=_('Order'),
        help_text=_('Left-to-right position on the board'),
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name=_('Default'),
        help_text=_('Seeded system column; protected from deletion'),
    )
    is_terminal = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_('Terminal'),
        help_text=_('The "done" column; a card enters it only via closeout'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['order', 'key']
        verbose_name = _('Kanban Column')
        verbose_name_plural = _('Kanban Columns')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.label} ({self.key})'

    @classmethod
    def terminal_key(cls) -> str | None:
        """Return the key of the terminal (done) column, or None if unset."""
        terminal = (
            cls.objects.filter(is_terminal=True).values_list('key', flat=True).first()
        )
        return terminal

    def card_count(self, *, active_only: bool = True) -> int:
        """Return the number of cards currently in this column."""
        cards = KanbanCard.objects.filter(status=self.key)

        if active_only:
            cards = cards.filter(is_active=True)

        return cards.count()


def _default_windows() -> dict:
    """Mon-Fri 09:00-17:00, the previous implicit assumption made explicit."""
    return {str(day): [['09:00', '17:00']] for day in range(5)}


class WorkingCalendar(models.Model):
    """A named working-time definition for scheduling (S6, plan §5.5/§5.11).

    Holds a per-day shift definition, a holiday exception list and an IANA
    timezone. It answers *when is work possible*, which is separate from *what
    clock a user reads times in* (that is a per-user display preference).

    A calendar may be scoped to a machine, to a customer, or be the system
    default. A card resolves to exactly one via machine → customer → default (see
    ``tasks.services.calendars``). ``windows`` maps a weekday index as a *string*
    ("0"=Monday … "6"=Sunday) to a list of ``["HH:MM", "HH:MM"]`` pairs, allowing
    split shifts. ``holidays`` is a list of ISO date strings.
    """

    name = models.CharField(max_length=120, unique=True, verbose_name=_('Name'))
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        verbose_name=_('Timezone'),
        help_text=_('IANA timezone name, e.g. "America/New_York"'),
    )
    windows = models.JSONField(
        default=_default_windows,
        blank=True,
        help_text=_('Weekday (0=Mon..6=Sun) to list of [open, close] time pairs'),
    )
    holidays = models.JSONField(
        default=list, blank=True, help_text=_('List of ISO date strings')
    )
    is_default = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_('Default'),
        help_text=_('The fallback calendar when nothing more specific matches'),
    )
    machine = models.ForeignKey(
        'assets.AssetMachine',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='working_calendars',
    )
    customer = models.ForeignKey(
        'company.Company',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='working_calendars',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['name']
        verbose_name = _('Working Calendar')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.name} ({self.timezone})'

    def clean(self):
        """Validate the timezone and window structure before saving."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        from django.core.exceptions import ValidationError

        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError({
                'timezone': f'Unknown timezone: {self.timezone}'
            }) from exc

        for key, pairs in (self.windows or {}).items():
            if str(key) not in {str(d) for d in range(7)}:
                raise ValidationError({'windows': f'Invalid weekday key: {key!r}'})
            for pair in pairs:
                if len(pair) != 2:
                    raise ValidationError({
                        'windows': f'Each window must be [open, close]: {pair!r}'
                    })

    def to_spec(self):
        """Build the pure ``CalendarSpec`` the working_time helpers consume."""
        from datetime import date, time

        from tasks.services.working_time import CalendarSpec

        def _time(value: str) -> time:
            hour, minute = value.split(':')
            return time(int(hour), int(minute))

        windows = {
            int(day): tuple(
                (_time(open_str), _time(close_str)) for open_str, close_str in pairs
            )
            for day, pairs in (self.windows or {}).items()
        }
        holidays = frozenset(
            date.fromisoformat(value) for value in (self.holidays or [])
        )
        return CalendarSpec(tzname=self.timezone, windows=windows, holidays=holidays)


class KanbanCardDependency(models.Model):
    """A scheduling dependency between two work orders (S6, plan §5.10).

    The edge points from predecessor (``from_card``) to successor (``to_card``):
    for the default finish-to-start type, ``from_card`` must finish before
    ``to_card`` starts. ``lag_minutes`` is working-time slack applied after the
    constraint (negative values are leads). The graph is kept acyclic by
    service-level validation on creation.
    """

    TYPE_FS = 'FS'
    TYPE_SS = 'SS'
    TYPE_FF = 'FF'
    TYPE_SF = 'SF'
    TYPE_CHOICES = [
        (TYPE_FS, 'Finish-to-Start'),
        (TYPE_SS, 'Start-to-Start'),
        (TYPE_FF, 'Finish-to-Finish'),
        (TYPE_SF, 'Start-to-Finish'),
    ]

    from_card = models.ForeignKey(
        KanbanCard, on_delete=models.CASCADE, related_name='dependencies_out'
    )
    to_card = models.ForeignKey(
        KanbanCard, on_delete=models.CASCADE, related_name='dependencies_in'
    )
    dependency_type = models.CharField(
        max_length=2, choices=TYPE_CHOICES, default=TYPE_FS
    )
    lag_minutes = models.IntegerField(
        default=0, help_text='Working-time slack after the constraint (may be negative)'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        unique_together = [('from_card', 'to_card', 'dependency_type')]
        ordering = ['created_at']
        verbose_name = _('Work Order Dependency')
        verbose_name_plural = _('Work Order Dependencies')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.from_card_id} {self.dependency_type} {self.to_card_id}'


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

    def check_and_allocate(self, persist: bool = True):
        """Check stock availability and allocate if possible.

        Args:
            persist: When False, compute the allocation result without saving -
                used by read-only callers (e.g. voice Tier-1 stock checks) that
                must not mutate the database.

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

        if persist:
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
    WorkOrderDeletionRecord,
    WorkOrderDeviation,
    WorkOrderEvent,
)
