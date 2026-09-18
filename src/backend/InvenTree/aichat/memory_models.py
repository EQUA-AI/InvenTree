"""Consent records for the default-off durable memory service."""

from django.conf import settings
from django.db import models
from django.db.models import Q


class MemoryMode(models.TextChoices):
    """A thread's extraction preference; ordinary compaction is independent."""

    INHERIT = 'inherit', 'Use my default'
    EXTRACT = 'extract', 'Save eligible memories'
    OFF = 'off', 'Do not save memories'


class ClientAISettings(models.Model):
    """Explicit tenant enrollment; absence is equivalent to disabled."""

    client = models.OneToOneField(
        'assets.Client', on_delete=models.PROTECT, related_name='ai_settings'
    )
    memory_enabled = models.BooleanField(default=False, db_default=False)
    required_notice_version = models.CharField(max_length=64, default='', blank=True)
    enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    enabled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Enrollment always records the notice requirement and time."""

        constraints = [
            models.CheckConstraint(
                condition=Q(memory_enabled=False)
                | (~Q(required_notice_version='') & Q(enabled_at__isnull=False)),
                name='aichat_memory_enrollment_notice',
            )
        ]


class MemoryNoticeAcknowledgement(models.Model):
    """Append-only evidence of the exact notice version acknowledged by a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memory_notices',
    )
    notice_version = models.CharField(max_length=64)
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """One acknowledgement per version, never inferred from existing chats."""

        constraints = [
            models.UniqueConstraint(
                fields=['user', 'notice_version'], name='aichat_memory_notice_unique'
            ),
            models.CheckConstraint(
                condition=~Q(notice_version=''), name='aichat_memory_notice_nonempty'
            ),
        ]


class UserMemorySettings(models.Model):
    """Owner-level withdrawal takes precedence over all thread/client settings."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memory_settings',
    )
    opted_out = models.BooleanField(default=False, db_default=False)
    updated_at = models.DateTimeField(auto_now=True)
