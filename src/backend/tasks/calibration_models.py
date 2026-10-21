"""Attributed calibration traceability revisions; never calibration acceptance."""

from django.conf import settings
from django.db import models


class CalibrationTraceability(models.Model):
    """Append-only facts for one reading, owned by the same Work audit boundary."""

    work_order = models.ForeignKey(
        'tasks.WorkOrder',
        on_delete=models.CASCADE,
        related_name='calibration_traceability',
    )
    reading = models.ForeignKey(
        'tasks.CloseoutReading',
        on_delete=models.CASCADE,
        related_name='calibration_traceability',
    )
    revision = models.PositiveIntegerField()
    supersedes = models.OneToOneField(
        'self',
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name='correction',
    )
    reference_instrument_id = models.CharField(max_length=200)
    certificate_number = models.CharField(max_length=200)
    issuing_lab = models.CharField(max_length=200)
    valid_until = models.DateField(null=True, blank=True)
    certificate = models.ForeignKey(
        'common.Attachment', null=True, on_delete=models.SET_NULL, related_name='+'
    )
    certificate_snapshot = models.JSONField()
    correction_reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """The locked Work command serializes each reading's revision chain."""

        ordering = ['reading_id', '-revision']
        constraints = [
            models.UniqueConstraint(
                fields=['reading', 'revision'], name='tasks_cal_reading_revision'
            ),
            models.CheckConstraint(
                condition=models.Q(revision__gte=1), name='tasks_cal_positive_revision'
            ),
        ]

    def save(self, *args, **kwargs):
        """Correct by appending; preserve earlier facts and attribution."""
        if not self._state.adding:
            raise ValueError(
                'Calibration traceability is immutable; append a correction'
            )
        super().save(*args, **kwargs)
