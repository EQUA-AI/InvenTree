"""Retained originals and append-only accepted maintenance reports (O15)."""

from django.conf import settings
from django.db import models

from tasks.services.capture_storage import original_path, original_storage


class FrozenQuerySet(models.QuerySet):
    """Reject bulk mutation through the application ORM as well."""

    def update(self, **kwargs):
        """Append a revision instead."""
        raise ValueError('Frozen records cannot be modified')

    def delete(self):
        """Retain accepted evidence."""
        raise ValueError('Frozen records cannot be deleted')


class FrozenRecord(models.Model):
    """Application writes can only append; no edit or delete interface exists."""

    objects = FrozenQuerySet.as_manager()

    class Meta:
        """Shared behavior only."""

        abstract = True

    def save(self, *args, **kwargs):
        """Require a new row for every revision."""
        if not self._state.adding:
            raise ValueError('Frozen records cannot be modified')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Retain the original through normal application paths."""
        raise ValueError('Frozen records cannot be deleted')


class CaptureOriginal(FrozenRecord):
    """Original bytes in server media storage, never a public storage URL."""

    work_order = models.ForeignKey('tasks.WorkOrder', on_delete=models.PROTECT)
    voice_capture = models.ForeignKey(
        'voice.VoiceCaptureSession',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='originals',
    )
    artifact = models.ForeignKey(
        'tasks.InvestigationArtifact',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='originals',
    )
    file = models.FileField(upload_to=original_path, storage=original_storage)
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    content_hash = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    started_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveBigIntegerField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class AcceptedCloseoutReport(FrozenRecord):
    """Exact JSON and text frozen in the completion/amendment transaction."""

    closeout = models.ForeignKey(
        'tasks.WorkOrderCloseout', on_delete=models.PROTECT, related_name='reports'
    )
    revision = models.PositiveIntegerField()
    previous = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT
    )
    amendment = models.OneToOneField(
        'tasks.CloseoutAmendment', null=True, blank=True, on_delete=models.PROTECT
    )
    document = models.JSONField()
    text = models.TextField()
    content_hash = models.CharField(max_length=64)
    # Revision recorder/time; the API exposes frozen_*. Native verification is
    # recorded separately on WorkOrderCloseout. Keep the reserved schema stable.
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    accepted_at = models.DateTimeField()

    class Meta:
        """A serialized Work command assigns the next revision."""

        constraints = [
            models.UniqueConstraint(
                fields=['closeout', 'revision'], name='tasks_report_revision_uniq'
            )
        ]
