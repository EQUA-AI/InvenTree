"""Source birth/attestation and readiness-review records; never erasure state."""

from uuid import uuid4

from django.db import models
from django.db.models import Q


class NativeAuthorityEpoch(models.Model):
    """Explicit future-only source enrollment, bound to a source incarnation."""

    namespace = models.ForeignKey(
        'integration_state.SourceNamespace', on_delete=models.PROTECT
    )
    namespace_epoch = models.UUIDField()
    source_environment = models.CharField(max_length=128)
    epoch = models.UUIDField(default=uuid4, unique=True, editable=False)
    started_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=False)

    class Meta:
        """Retain old epochs without enabling a source at migration/startup."""

        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=['namespace'],
                condition=Q(active=True),
                name='native_authority_active_epoch',
            )
        ]


class NativeSourceBirth(models.Model):
    """Observed ORM insertion, not a backfill from mutable creation timestamps."""

    epoch = models.ForeignKey(NativeAuthorityEpoch, on_delete=models.PROTECT)
    entity_kind = models.CharField(max_length=16)
    source_pk = models.PositiveBigIntegerField()
    incarnation = models.UUIDField(default=uuid4, editable=False)
    observed_at = models.DateTimeField(auto_now_add=True)
    ambiguous = models.BooleanField(default=False)

    class Meta:
        """Reused keys become ambiguous; no old proof silently adopts a new row."""

        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=['epoch', 'entity_kind', 'source_pk'],
                name='native_source_birth_key',
            ),
            models.CheckConstraint(
                condition=Q(
                    source_pk__gt=0,
                    entity_kind__in=['work', 'maintenance', 'attachment'],
                ),
                name='native_source_birth_kind',
            ),
        ]


class NativeAuthorityRequest(models.Model):
    """Immutable source-owner attestation or readiness request, never a purge."""

    namespace = models.ForeignKey(
        'integration_state.SourceNamespace', on_delete=models.PROTECT
    )
    namespace_epoch = models.UUIDField()
    purpose = models.CharField(max_length=24)
    request_key = models.CharField(max_length=128)
    requester_pk = models.PositiveBigIntegerField()
    requester_key = models.CharField(max_length=64)
    client_bindings = models.JSONField()
    request_reference = models.CharField(max_length=1024)
    reference_digest = models.CharField(max_length=64)
    source_binding = models.JSONField()
    binding_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """No cascade from user/client deletion and no mutable operational copy."""

        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=['namespace', 'purpose', 'request_key'],
                name='native_authority_request_key',
            ),
            models.CheckConstraint(
                condition=Q(
                    purpose__in=['attribution', 'deletion_readiness'],
                    requester_pk__gt=0,
                ),
                name='native_authority_request_kind',
            ),
        ]


class NativeAuthorityDecision(models.Model):
    """One retained, exact-revision review by a different current native actor."""

    request = models.OneToOneField(
        NativeAuthorityRequest, on_delete=models.PROTECT, related_name='decision'
    )
    reviewer_pk = models.PositiveBigIntegerField()
    reviewer_key = models.CharField(max_length=64)
    approved = models.BooleanField()
    request_digest = models.CharField(max_length=64)
    impact_digest = models.CharField(max_length=64)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """A decision is an audit record, not a source-delete authorization."""

        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(reviewer_pk__gt=0), name='native_authority_reviewer'
            )
        ]


class NativeAuthorityWithdrawal(models.Model):
    """Append-only invalidation of an exact claim, never deletion of its audit."""

    request = models.OneToOneField(
        NativeAuthorityRequest, on_delete=models.PROTECT, related_name='withdrawal'
    )
    actor_pk = models.PositiveBigIntegerField()
    actor_key = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    reason_reference = models.CharField(max_length=1024)
    reference_digest = models.CharField(max_length=64)
    withdrawn_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Invalidated claims and their actors remain indefinitely retained."""

        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(actor_pk__gt=0), name='native_authority_withdrawer'
            )
        ]
