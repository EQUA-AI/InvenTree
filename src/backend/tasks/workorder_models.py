"""Supporting database models for maintenance work orders."""

from django.conf import settings
from django.db import models


class WorkOrderEvent(models.Model):
    """Append-only audit event associated with a work order."""

    work_order = models.ForeignKey(
        'tasks.KanbanCard', on_delete=models.CASCADE, related_name='events'
    )
    event_type = models.CharField(max_length=40, db_index=True)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    reason = models.TextField(blank=True)
    correlation_id = models.UUIDField(db_index=True)
    idempotency_key = models.CharField(max_length=128, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Model metadata."""

        indexes = [
            models.Index(
                fields=['work_order', 'created_at'], name='tasks_wo_event_created'
            )
        ]


# The sole post-completion carveout on a closeout row (FR-CO-014): supervisor
# verification may set previously-null verified_by/verified_at exactly once.
_CLOSEOUT_MUTABLE_FIELDS = frozenset({'verified_by', 'verified_at'})


class WorkOrderCloseout(models.Model):
    """Canonical structured execution result for a work order.

    Completed closeouts are immutable through app paths; corrections are
    append-only ``CloseoutAmendment`` rows and readers overlay the latest
    applied projection (Feature #15, FR-CO-013).
    """

    def save(self, *args, **kwargs):
        """Reject destructive edits of a completed closeout."""
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get('update_fields')
            if (
                update_fields is None
                or not set(update_fields) <= _CLOSEOUT_MUTABLE_FIELDS
            ):
                raise ValueError(
                    'Completed closeouts are immutable; corrections are amendments'
                )
        super().save(*args, **kwargs)

    work_order = models.OneToOneField(
        'tasks.KanbanCard', on_delete=models.CASCADE, related_name='structured_closeout'
    )
    cause = models.TextField(blank=True)
    action = models.TextField()
    result = models.TextField()
    verification_summary = models.TextField()
    downtime_minutes = models.PositiveIntegerField(null=True, blank=True)
    follow_up_required = models.BooleanField(default=False)
    follow_up = models.TextField(blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    completed_at = models.DateTimeField()
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64)
    version = models.PositiveIntegerField(default=1)


class WorkOrderDeviation(models.Model):
    """Recorded departure from expected work-order procedure execution."""

    work_order = models.ForeignKey(
        'tasks.KanbanCard', on_delete=models.CASCADE, related_name='deviations'
    )
    category = models.CharField(max_length=40, db_index=True)
    application_key = models.CharField(max_length=128, blank=True)
    step_key = models.CharField(max_length=128, blank=True)
    resource_key = models.CharField(max_length=128, blank=True)
    expected = models.JSONField(null=True, blank=True)
    actual = models.JSONField(null=True, blank=True)
    reason = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='work_order_deviations',
    )
    approval = models.ForeignKey(
        'approvals.Approval',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='work_order_deviations',
    )
    resolution = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)


class WorkOrderCommand(models.Model):
    """Durable idempotency ledger for work-order commands."""

    work_order = models.ForeignKey(
        'tasks.KanbanCard', on_delete=models.CASCADE, related_name='commands'
    )
    command = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    correlation_id = models.UUIDField(db_index=True)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=32, db_index=True)
    result_ref = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Model metadata."""

        unique_together = [('work_order', 'idempotency_key')]
