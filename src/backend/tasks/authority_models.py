"""Attributed operational facts. Corrections append; lifecycle is not authority."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class AppendOnlyRecords(models.QuerySet):
    """No application bulk path may rewrite or discard a saved fact."""

    def update(self, **kwargs):
        """A correction must create a new row."""
        raise ValueError('Authority records are append-only.')

    def delete(self):
        """Keep both current and superseded records."""
        raise ValueError('Authority records are append-only.')


class AuthorityEntry(models.Model):
    """Common immutable provenance, including a separately attributed verifier."""

    work_order = models.ForeignKey(
        'tasks.WorkOrder', on_delete=models.PROTECT, related_name='+'
    )
    version = models.PositiveIntegerField()
    supersedes = models.OneToOneField(
        'self',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='correction',
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)
    observed_at = models.DateTimeField()
    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(null=True, blank=True)
    source_reference = models.CharField(max_length=1000)
    statement = models.TextField()
    basis = models.JSONField(default=dict)
    objects = AppendOnlyRecords.as_manager()

    class Meta:
        """No separate table for shared record fields."""

        abstract = True
        ordering = ['-version', '-pk']

    def save(self, *args, **kwargs):
        """Saved facts cannot be edited through application model paths."""
        if not self._state.adding:
            raise ValueError('Authority records are immutable; append a correction.')
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Retain originals and their superseding records."""
        raise ValueError('Authority records are append-only.')


class OperationalAuthorityRecord(AuthorityEntry):
    """Separate test acceptance, operating permission, observation and duty facts."""

    class Kind(models.TextChoices):
        """Never derive one of these facts from another."""

        TEST_ACCEPTANCE = 'test_acceptance', 'Test acceptance'
        OPERATING_PERMISSION = 'operating_permission', 'Operating permission'
        OBSERVED_RETURN = 'observed_return', 'Observed return'
        SERVICE_STATE = 'service_state', 'Duty / standby / redundancy'

    kind = models.CharField(max_length=24, choices=Kind.choices)
    outcome = models.CharField(max_length=24)
    target = models.ForeignKey(
        'assets.AssetMachine', on_delete=models.PROTECT, related_name='+'
    )
    details = models.JSONField(default=dict)

    class Meta(AuthorityEntry.Meta):
        """Index the Work's distinct fact histories."""

        abstract = False
        permissions = [
            (
                'record_operating_authority',
                'Can record operating authority and service state',
            )
        ]
        indexes = [
            models.Index(
                fields=['work_order', 'kind'], name='tasks_authority_work_kind'
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=(
                        'test_acceptance',
                        'operating_permission',
                        'observed_return',
                        'service_state',
                    )
                ),
                name='tasks_authority_kind',
            )
        ]
