"""Typed identity registry for the existing integration source/change ledger."""

from django.db import models
from django.db.models import Q


class GenericSourceIdentity(models.Model):
    """Retain typed opaque keys and declared, not proved, native incarnation."""

    namespace = models.ForeignKey(
        'integration_state.SourceNamespace', on_delete=models.PROTECT
    )
    epoch = models.UUIDField()
    entity_kind = models.CharField(max_length=32)
    aspect = models.CharField(max_length=32)
    key_type = models.CharField(max_length=7)
    integer_key = models.PositiveBigIntegerField(null=True)
    text_key = models.CharField(max_length=256, null=True)
    declared_incarnation = models.CharField(max_length=256, null=True)
    identity_digest = models.CharField(max_length=64)

    class Meta:
        """No nullable-key duplicate or replacement native-placement branch."""

        constraints = [
            models.UniqueConstraint(
                fields=['namespace', 'epoch', 'identity_digest'],
                name='shared_identity_digest',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        key_type='integer',
                        integer_key__isnull=False,
                        integer_key__gt=0,
                        text_key__isnull=True,
                    )
                    | (
                        Q(
                            key_type='string',
                            integer_key__isnull=True,
                            text_key__isnull=False,
                        )
                        & ~Q(text_key='')
                    )
                ),
                name='shared_identity_key_type',
            ),
            models.CheckConstraint(
                condition=~Q(entity_kind='machine', aspect='placement'),
                name='shared_native_aspect_reserved',
            ),
        ]


class EventConsumer(models.Model):
    """An internal immutable configuration, never a reader or actor grant."""

    namespace = models.ForeignKey(
        'integration_state.SourceNamespace', on_delete=models.PROTECT
    )
    namespace_epoch = models.UUIDField()
    consumer_key = models.CharField(max_length=128)
    configuration_epoch = models.UUIDField(unique=True)
    aspects = models.JSONField()
    binding_digest = models.CharField(max_length=64)
    active = models.BooleanField(default=False)

    class Meta:
        """A replacement never inherits prior configuration acknowledgments."""

        constraints = [
            models.UniqueConstraint(
                fields=['namespace', 'consumer_key'],
                condition=Q(active=True),
                name='shared_active_consumer',
            )
        ]


class EventDelivery(models.Model):
    """Retain every lease attempt and exact event disposition indefinitely."""

    consumer = models.ForeignKey(EventConsumer, on_delete=models.PROTECT)
    change = models.ForeignKey(
        'integration_state.SourceChange', on_delete=models.PROTECT
    )
    fence = models.PositiveBigIntegerField()
    lease_token = models.UUIDField(unique=True)
    worker_key = models.CharField(max_length=128)
    lease_until = models.DateTimeField()
    event_payload = models.TextField()
    payload_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default='claimed')
    requested_outcome = models.CharField(max_length=16, null=True)
    outcome = models.CharField(max_length=16, null=True)
    advanced = models.BooleanField(default=False)
    receipt_digest = models.CharField(max_length=64, null=True)

    class Meta:
        """One active attempt per configuration/event, with retained old fences."""

        constraints = [
            models.UniqueConstraint(
                fields=['consumer', 'change', 'fence'], name='shared_delivery_fence'
            ),
            models.UniqueConstraint(
                fields=['consumer', 'change'],
                condition=Q(state='claimed'),
                name='shared_delivery_one_claim',
            ),
            models.CheckConstraint(
                condition=Q(fence__gt=0)
                & (
                    Q(
                        state__in=['claimed', 'expired'],
                        requested_outcome__isnull=True,
                        outcome__isnull=True,
                        receipt_digest__isnull=True,
                        advanced=False,
                    )
                    | Q(
                        state='acknowledged',
                        requested_outcome__isnull=False,
                        requested_outcome__in=[
                            'applied',
                            'superseded',
                            'failed',
                            'uncertain',
                        ],
                        outcome__isnull=False,
                        outcome__in=['applied', 'superseded', 'failed', 'uncertain'],
                        receipt_digest__isnull=False,
                    )
                ),
                name='shared_delivery_state',
            ),
        ]


class ConsumerAspectProgress(models.Model):
    """Highest acknowledged aspect revision, not a global commit watermark."""

    consumer = models.ForeignKey(EventConsumer, on_delete=models.PROTECT)
    source_digest = models.CharField(max_length=64)
    change = models.ForeignKey(
        'integration_state.SourceChange', on_delete=models.PROTECT
    )
    revision = models.PositiveBigIntegerField()

    class Meta:
        """Old observations cannot regress a newer accepted aspect revision."""

        constraints = [
            models.UniqueConstraint(
                fields=['consumer', 'source_digest'], name='shared_consumer_aspect'
            ),
            models.CheckConstraint(
                condition=Q(revision__gt=0), name='shared_consumer_fence'
            ),
        ]


class PrivateDeletionRequest(models.Model):
    """Immutable private planning intent, never an approved native command."""

    namespace = models.ForeignKey(
        'integration_state.SourceNamespace', on_delete=models.PROTECT
    )
    epoch = models.UUIDField()
    intent_id = models.UUIDField()
    request_key = models.CharField(max_length=128)
    payload = models.JSONField()
    intent_digest = models.CharField(max_length=64)
    payload_digest = models.CharField(max_length=64)

    class Meta:
        """Neither request-key replay nor UUID reuse adopts another owner."""

        constraints = [
            models.UniqueConstraint(
                fields=['namespace', 'request_key'], name='shared_private_request_key'
            ),
            models.UniqueConstraint(
                fields=['namespace', 'intent_id'], name='shared_private_request_uuid'
            ),
        ]


class PrivateCopyImpact(models.Model):
    """One retained immutable copy claim; changed authority cannot adopt it."""

    request = models.ForeignKey(PrivateDeletionRequest, on_delete=models.PROTECT)
    copy_key = models.CharField(max_length=256)
    copy_kind = models.CharField(max_length=16)
    payload = models.JSONField()
    payload_digest = models.CharField(max_length=64)

    class Meta:
        """One literal copy key per exact private request, never a copy census."""

        constraints = [
            models.UniqueConstraint(
                fields=['request', 'copy_key'], name='shared_private_copy_key'
            ),
            models.CheckConstraint(
                condition=Q(
                    copy_kind__in=[
                        'original',
                        'derivative',
                        'index',
                        'cache',
                        'report',
                        'audit',
                        'ledger',
                        'backup',
                        'unknown',
                    ]
                ),
                name='shared_private_copy_kind',
            ),
        ]


class PrivateCopyAttempt(models.Model):
    """Retained synthetic attempt snapshots, not native command receipts."""

    impact = models.ForeignKey(PrivateCopyImpact, on_delete=models.PROTECT)
    attempt_id = models.UUIDField(unique=True)
    ordinal = models.PositiveIntegerField()
    max_attempts = models.PositiveIntegerField()
    version = models.PositiveBigIntegerField(default=1)
    checkpoint = models.JSONField()
    checkpoint_digest = models.CharField(max_length=64)

    class Meta:
        """Attempt identities and cumulative bounds cannot reset on retry."""

        constraints = [
            models.UniqueConstraint(
                fields=['impact', 'ordinal'], name='shared_private_copy_ordinal'
            ),
            models.CheckConstraint(
                condition=Q(
                    ordinal__gt=0,
                    ordinal__lte=models.F('max_attempts'),
                    max_attempts__gt=0,
                    max_attempts__lte=1000,
                    version__gt=0,
                ),
                name='shared_private_copy_budget',
            ),
        ]


class ReconciliationScan(models.Model):
    """Actor/population-bound transport progress, never source completeness."""

    namespace = models.ForeignKey(
        'integration_state.SourceNamespace', on_delete=models.PROTECT
    )
    epoch = models.UUIDField()
    request_key = models.CharField(max_length=128)
    binding = models.JSONField()
    baseline_digest = models.CharField(max_length=64)
    checkpoint = models.JSONField()
    checkpoint_digest = models.CharField(max_length=64)
    max_pages = models.PositiveIntegerField()
    max_records = models.PositiveIntegerField()
    version = models.PositiveBigIntegerField(default=1)

    class Meta:
        """One immutable request binding and bounded cumulative budget."""

        constraints = [
            models.UniqueConstraint(
                fields=['namespace', 'request_key'], name='shared_scan_request'
            ),
            models.CheckConstraint(
                condition=Q(
                    version__gt=0,
                    max_pages__gt=0,
                    max_pages__lte=1000,
                    max_records__gt=0,
                    max_records__lte=10000,
                ),
                name='shared_scan_budget',
            ),
        ]


class ReconciliationIdentity(models.Model):
    """Bounded unique seen identities with exact integer/text key fidelity."""

    scan = models.ForeignKey(ReconciliationScan, on_delete=models.PROTECT)
    identity_digest = models.CharField(max_length=64)
    identity = models.JSONField()

    class Meta:
        """A repeated page cannot count a source identity twice."""

        constraints = [
            models.UniqueConstraint(
                fields=['scan', 'identity_digest'], name='shared_scan_seen_identity'
            )
        ]


class ReconciliationAttempt(models.Model):
    """Retain pre-IO reservations and consumed work across failures/restart."""

    scan = models.ForeignKey(ReconciliationScan, on_delete=models.PROTECT)
    attempt_id = models.UUIDField(unique=True)
    ordinal = models.PositiveIntegerField()
    reserved_version = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16, default='pending')
    response_digest = models.CharField(max_length=64, null=True)

    class Meta:
        """No reused attempt or reset cumulative ordinal."""

        constraints = [
            models.UniqueConstraint(
                fields=['scan', 'ordinal'], name='shared_scan_attempt_ordinal'
            ),
            models.CheckConstraint(
                condition=Q(ordinal__gt=0, ordinal__lte=1000, reserved_version__gt=0)
                & Q(state__in=['pending', 'accepted', 'abandoned']),
                name='shared_scan_attempt_state',
            ),
        ]
