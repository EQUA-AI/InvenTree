"""O15 work time and confirmed telemetry windows, with retained corrections."""

from django.conf import settings
from django.db import models


class WorkTimeInterval(models.Model):
    """Server-clock person-work interval; a correction supersedes its original."""

    work_order = models.ForeignKey(
        'tasks.WorkOrder', on_delete=models.PROTECT, related_name='time_intervals'
    )
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_action = models.CharField(max_length=8, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    ended_recorded_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    supersedes = models.OneToOneField(
        'self',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='replacement',
    )

    class Meta:
        """Bound reads by work and person."""

        indexes = [
            models.Index(fields=['work_order', 'person'], name='work_time_person')
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True)
                | models.Q(ended_at__gt=models.F('started_at')),
                name='work_time_positive',
            )
        ]


class WorkDowntime(models.Model):
    """Technician-confirmed machine stop window, never a service-outage claim."""

    work_order = models.ForeignKey(
        'tasks.WorkOrder', on_delete=models.PROTECT, related_name='downtime_records'
    )
    binding = models.ForeignKey('assets.MachineSignalBinding', on_delete=models.PROTECT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    proposal = models.JSONField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)
    supersedes = models.OneToOneField(
        'self',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='replacement',
    )
