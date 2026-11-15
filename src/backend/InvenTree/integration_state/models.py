"""Durable integration bookkeeping with no authority over operational records."""

from uuid import uuid4

from django.db import models
from django.db.models import Q
from django.utils import timezone

from .native_authority_models import NativeAuthorityDecision as NativeAuthorityDecision
from .native_authority_models import NativeAuthorityEpoch as NativeAuthorityEpoch
from .native_authority_models import NativeAuthorityRequest as NativeAuthorityRequest
from .native_authority_models import (
    NativeAuthorityWithdrawal as NativeAuthorityWithdrawal,
)
from .native_authority_models import NativeSourceBirth as NativeSourceBirth
from .shared_models import ConsumerAspectProgress as ConsumerAspectProgress
from .shared_models import EventConsumer as EventConsumer
from .shared_models import EventDelivery as EventDelivery
from .shared_models import GenericSourceIdentity as GenericSourceIdentity
from .shared_models import PrivateCopyAttempt as PrivateCopyAttempt
from .shared_models import PrivateCopyImpact as PrivateCopyImpact
from .shared_models import PrivateDeletionRequest as PrivateDeletionRequest
from .shared_models import ReconciliationAttempt as ReconciliationAttempt
from .shared_models import ReconciliationIdentity as ReconciliationIdentity
from .shared_models import ReconciliationScan as ReconciliationScan


class SourceNamespace(models.Model):
    """A server-owned source environment; activation never follows user labels."""

    environment = models.CharField(max_length=128, unique=True)
    epoch = models.UUIDField(default=uuid4, editable=False)
    active = models.BooleanField(default=False)

    class Meta:
        """Permit one active source namespace in the selected database."""

        constraints = [
            models.UniqueConstraint(
                fields=['active'],
                condition=Q(active=True),
                name='integration_one_active_source',
            )
        ]


class SourceState(models.Model):
    """Latest captured source aspect, never an actor's disclosure capability."""

    namespace = models.ForeignKey(SourceNamespace, on_delete=models.PROTECT)
    epoch = models.UUIDField()
    entity_kind = models.CharField(max_length=32)
    aspect = models.CharField(max_length=32)
    source_id = models.PositiveBigIntegerField(null=True)
    generic_identity = models.ForeignKey(
        GenericSourceIdentity, on_delete=models.PROTECT, null=True
    )
    generic_snapshot = models.JSONField(null=True)
    revision = models.PositiveBigIntegerField(default=1)
    client_id = models.PositiveBigIntegerField(null=True)
    location_id = models.PositiveBigIntegerField(null=True)
    native_version = models.PositiveBigIntegerField(default=0, null=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        """Source identity and aspect revision remain unique and positive."""

        constraints = [
            models.UniqueConstraint(
                fields=['namespace', 'epoch', 'entity_kind', 'aspect', 'source_id'],
                name='integration_source_aspect',
            ),
            models.CheckConstraint(
                condition=Q(revision__gt=0)
                & (
                    Q(
                        generic_identity__isnull=True,
                        source_id__isnull=False,
                        source_id__gt=0,
                        native_version__isnull=False,
                        generic_snapshot__isnull=True,
                    )
                    | Q(
                        generic_identity__isnull=False,
                        source_id__isnull=True,
                        native_version__isnull=True,
                        client_id__isnull=True,
                        location_id__isnull=True,
                        deleted=False,
                        generic_snapshot__isnull=False,
                    )
                ),
                name='integration_positive_fences',
            ),
            models.UniqueConstraint(
                fields=['generic_identity'],
                condition=Q(generic_identity__isnull=False),
                name='shared_identity_current_state',
            ),
        ]


class SourceChange(models.Model):
    """Retained before/after aspect evidence, not an authorized public tombstone."""

    namespace = models.ForeignKey(SourceNamespace, on_delete=models.PROTECT)
    epoch = models.UUIDField()
    entity_kind = models.CharField(max_length=32)
    aspect = models.CharField(max_length=32)
    source_id = models.PositiveBigIntegerField(null=True)
    generic_identity = models.ForeignKey(
        GenericSourceIdentity, on_delete=models.PROTECT, null=True
    )
    producer_key = models.CharField(max_length=128, null=True)
    canonical_digest = models.CharField(max_length=64, null=True)
    revision = models.PositiveBigIntegerField()
    operation = models.CharField(max_length=12)
    previous = models.JSONField(null=True)
    current = models.JSONField(null=True)
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        """One durable change per source aspect and committed revision."""

        constraints = [
            models.UniqueConstraint(
                fields=[
                    'namespace',
                    'epoch',
                    'entity_kind',
                    'aspect',
                    'source_id',
                    'revision',
                ],
                name='integration_source_change',
            ),
            models.CheckConstraint(
                condition=Q(revision__gt=0)
                & (
                    Q(
                        generic_identity__isnull=True,
                        source_id__isnull=False,
                        source_id__gt=0,
                        producer_key__isnull=True,
                        canonical_digest__isnull=True,
                    )
                    | Q(
                        generic_identity__isnull=False,
                        source_id__isnull=True,
                        producer_key__isnull=False,
                        canonical_digest__isnull=False,
                        operation='observed',
                        current__isnull=False,
                    )
                ),
                name='integration_change_fences',
            ),
            models.UniqueConstraint(
                fields=['generic_identity', 'revision'],
                condition=Q(generic_identity__isnull=False),
                name='shared_identity_change_fence',
            ),
            models.UniqueConstraint(
                fields=['generic_identity', 'producer_key'],
                condition=Q(generic_identity__isnull=False),
                name='shared_identity_producer_key',
            ),
        ]


class ProjectionEnrollment(models.Model):
    """Server-owned projection configuration, inactive until verified bootstrap."""

    namespace = models.ForeignKey(SourceNamespace, on_delete=models.PROTECT)
    namespace_epoch = models.UUIDField()
    projection = models.CharField(max_length=128)
    client_id = models.PositiveBigIntegerField()
    epoch = models.UUIDField(default=uuid4, editable=False, unique=True)
    active = models.BooleanField(default=False)
    bootstrapped = models.BooleanField(default=False)
    bootstrap_status = models.CharField(max_length=16, default='preparing')
    bootstrap_cursor = models.PositiveBigIntegerField(default=0)
    observation_revision = models.PositiveBigIntegerField(default=0)
    served_generation = models.CharField(max_length=256, null=True)
    generation_known = models.BooleanField(default=False)

    class Meta:
        """Only one active configuration may own a projection/client population."""

        constraints = [
            models.UniqueConstraint(
                fields=['namespace', 'namespace_epoch', 'projection', 'client_id'],
                condition=Q(active=True),
                name='integration_active_projection',
            ),
            models.CheckConstraint(
                condition=Q(
                    bootstrap_status__in=[
                        'preparing',
                        'ready',
                        'failed',
                        'disabled',
                        'superseded',
                    ]
                ),
                name='integration_bootstrap_status',
            ),
            models.CheckConstraint(
                condition=Q(active=False)
                | (Q(bootstrapped=True) & Q(bootstrap_status='ready')),
                name='integration_ready_projection',
            ),
        ]


class PlacementAcknowledgement(models.Model):
    """A retained accepted observation, never an operational write receipt."""

    enrollment = models.ForeignKey(ProjectionEnrollment, on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField()
    namespace_epoch = models.UUIDField()
    source_revision = models.PositiveBigIntegerField()
    client_id = models.PositiveBigIntegerField()
    location_id = models.PositiveBigIntegerField(null=True)
    native_version = models.PositiveBigIntegerField()
    serving_generation = models.CharField(max_length=256)

    class Meta:
        """One accepted row is atomically replaced for each enrolled machine."""

        constraints = [
            models.UniqueConstraint(
                fields=['enrollment', 'source_id'], name='integration_accepted_source'
            )
        ]
