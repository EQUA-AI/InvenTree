"""Database models for the tasks application."""

from decimal import Decimal

from django.contrib.postgres.fields import ArrayField
from django.db import models


class KanbanCard(models.Model):
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
    tags = ArrayField(base_field=models.CharField(max_length=32), default=list, blank=True)
    company = models.CharField(max_length=120, blank=True)
    company_contact_name = models.CharField(max_length=120, blank=True)
    company_contact_phone = models.CharField(max_length=64, blank=True)
    job_number = models.CharField(max_length=64, blank=True)
    service_quote = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['-created_at']

    def __str__(self) -> str:
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
        KanbanCard,
        on_delete=models.CASCADE,
        related_name='card_parts',
    )
    part = models.ForeignKey(
        'part.Part',
        on_delete=models.CASCADE,
        related_name='kanban_allocations',
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
        blank=True,
        help_text='Notes about stock availability or allocation issues',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        unique_together = [('card', 'part')]
        ordering = ['created_at']

    def __str__(self) -> str:
        return f'{self.card.title} - {self.part.name} x{self.quantity}'

    def check_and_allocate(self):
        """Check stock availability and allocate if possible.

        Returns a dict with the allocation result.
        """
        from stock.models import StockItem

        available = Decimal('0')
        stock_items = StockItem.objects.filter(
            part=self.part,
        ).filter(StockItem.IN_STOCK_FILTER)

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
