"""Work-owned investigation requests; evidence is append-only, never a verdict."""

from django.conf import settings
from django.db import models


class InvestigationRequest(models.Model):
    """A versioned decision and the artifacts needed to inform it."""

    class Status(models.TextChoices):
        """Recorded request outcomes; no evidence implies resolution."""

        OPEN = 'open', 'Open'
        ANSWERED = 'answered', 'Answered'
        CANNOT_PERFORM = 'cannot_perform', 'Cannot perform'
        PENDING_EXTERNAL = 'pending_external', 'Pending external'
        DISPUTED = 'disputed', 'Disputed'
        RESOLVED = 'resolved', 'Resolved'

    work_order = models.ForeignKey(
        'tasks.WorkOrder',
        on_delete=models.PROTECT,
        related_name='investigation_requests',
    )
    schema_version = models.PositiveIntegerField(default=1)
    version = models.PositiveIntegerField(default=1)
    purpose = models.TextField()
    next_check = models.TextField()
    priority = models.PositiveIntegerField(null=True, blank=True, default=None)
    required_artifacts = models.JSONField(default=list)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.OPEN
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    next_checkpoint = models.DateTimeField(null=True, blank=True)
    source_finding = models.ForeignKey(
        'repair.RepairInvestigationFinding',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='investigation_requests',
    )
    source_proposal = models.JSONField(null=True, blank=True)
    blocked_reason = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Priority is supplied by the owning workflow; ties retain creation order."""

        ordering = [models.F('priority').asc(nulls_last=True), 'created_at', 'pk']
        indexes = [
            models.Index(
                fields=['work_order', 'status'], name='tasks_inv_work_status_idx'
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        'open',
                        'answered',
                        'cannot_perform',
                        'pending_external',
                        'disputed',
                        'resolved',
                    )
                ),
                name='tasks_inv_request_status',
            )
        ]


class InvestigationArtifact(models.Model):
    """An attributed typed response; original media stays in its native store.

    Payloads retain measurement sets and laboratory sample/method/time/status.
    A response never completes a request automatically. Corrections append rows
    referring to the prior artifact; they cannot erase unrelated observations.
    """

    class Kind(models.TextChoices):
        """Supported typed evidence, enforced at the storage boundary."""

        OBSERVATION = 'observation', 'Observation'
        READING_SET = 'reading_set', 'Reading set'
        PHOTO = 'photo', 'Photo'
        REPORT = 'report', 'Report'
        LABORATORY = 'laboratory', 'Laboratory'
        CHECK_RESULT = 'check_result', 'Check result'

    request = models.ForeignKey(
        InvestigationRequest, on_delete=models.CASCADE, related_name='artifacts'
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    payload = models.JSONField()
    attachment = models.ForeignKey(
        'common.Attachment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    attachment_snapshot = models.JSONField(null=True, blank=True)
    supersedes = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name='corrections',
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    source_turn_id = models.CharField(max_length=80, blank=True)
    source_proposal_id = models.UUIDField(null=True, blank=True)
    source_file = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Unknown artifact kinds cannot be saved by alternate ORM paths."""

        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=(
                        'observation',
                        'reading_set',
                        'photo',
                        'report',
                        'laboratory',
                        'check_result',
                    )
                ),
                name='tasks_inv_artifact_kind',
            )
        ]

    def save(self, *args, **kwargs):
        """App paths append a correction instead of editing a saved response."""
        if not self._state.adding:
            raise ValueError(
                'Investigation artifacts are immutable; append a correction'
            )
        super().save(*args, **kwargs)
