"""Database models for the tasks application."""

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
