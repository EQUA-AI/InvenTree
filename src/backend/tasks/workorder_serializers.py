"""Serializers for the canonical maintenance work-order API."""

from django.contrib.auth import get_user_model

from rest_framework import serializers

from .models import KanbanCard, WorkOrderEvent, WorkOrderLifecycle


class WorkOrderSerializer(serializers.ModelSerializer):
    """Canonical work-order resource serializer.

    Lifecycle state and execution-owned values are deliberately read-only. Typed
    assignment is performed through the dedicated assignment command.
    """

    class Meta:
        """Serializer metadata."""

        model = KanbanCard
        fields = (
            'id',
            'reference',
            'title',
            'description',
            'lifecycle_status',
            'work_order_type',
            'machine',
            'customer',
            'assigned_to',
            'requested_by',
            'scheduled_start',
            'scheduled_end',
            'actual_started_at',
            'actual_completed_at',
            'estimated_minutes',
            'lifecycle_version',
            'hold_reason',
            'status',
            'priority',
            'due_date',
            'assignee',
            'tags',
            'company',
            'company_contact_name',
            'company_contact_phone',
            'job_number',
            'service_quote',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'reference',
            'lifecycle_status',
            'assigned_to',
            'requested_by',
            'actual_started_at',
            'actual_completed_at',
            'lifecycle_version',
            'hold_reason',
            'is_active',
            'created_at',
            'updated_at',
        )


class ReadinessBlockerSerializer(serializers.Serializer):
    """Stable readiness blocker representation."""

    code = serializers.CharField()
    message = serializers.CharField()
    source = serializers.CharField()
    object_type = serializers.CharField()
    object_id = serializers.CharField()
    blocking = serializers.BooleanField()
    remediation = serializers.DictField()
    metadata = serializers.DictField()


class WorkOrderReadinessSerializer(serializers.Serializer):
    """Serialize an immutable unified readiness decision."""

    action = serializers.CharField()
    ready = serializers.BooleanField()
    evaluated_at = serializers.DateTimeField()
    lifecycle_version = serializers.IntegerField()
    policy_version = serializers.IntegerField()
    blockers = ReadinessBlockerSerializer(many=True)
    warnings = ReadinessBlockerSerializer(many=True)
    snapshot_hash = serializers.CharField()


class BaseCommandSerializer(serializers.Serializer):
    """Common optimistic-concurrency and idempotency command fields."""

    expected_version = serializers.IntegerField(min_value=1, required=True)
    idempotency_key = serializers.CharField(max_length=128, required=True)
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class TransitionCommandSerializer(BaseCommandSerializer):
    """Validate a requested standalone lifecycle transition."""

    to_status = serializers.ChoiceField(choices=WorkOrderLifecycle.choices)


class AssignCommandSerializer(BaseCommandSerializer):
    """Validate typed assignment intent."""

    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all()
    )


class HoldCommandSerializer(BaseCommandSerializer):
    """Validate hold intent; holds require an auditable reason."""

    reason = serializers.CharField(allow_blank=False, required=True)


class ResumeCommandSerializer(BaseCommandSerializer):
    """Validate resume intent."""


class CancelCommandSerializer(BaseCommandSerializer):
    """Validate cancellation intent; cancellations require a reason."""

    reason = serializers.CharField(allow_blank=False, required=True)


class CompleteCommandSerializer(BaseCommandSerializer):
    """Validate structured completion intent and closeout content."""

    action = serializers.CharField(allow_blank=False)
    result = serializers.CharField(allow_blank=False)
    verification_summary = serializers.CharField(allow_blank=False)
    cause = serializers.CharField(required=False, allow_blank=True, default='')
    downtime_minutes = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=0
    )
    follow_up_required = serializers.BooleanField(required=False, default=False)
    follow_up = serializers.CharField(required=False, allow_blank=True, default='')
    # Feature #15: optional reviewed-capture provenance; additive and
    # backward-compatible (absent means the pre-existing manual contract).
    capture_id = serializers.IntegerField(required=False, allow_null=True, default=None)


class WorkOrderEventSerializer(serializers.ModelSerializer):
    """Read-only work-order audit event serializer."""

    class Meta:
        """Serializer metadata."""

        model = WorkOrderEvent
        fields = (
            'id',
            'event_type',
            'from_status',
            'to_status',
            'actor',
            'reason',
            'correlation_id',
            'idempotency_key',
            'metadata',
            'created_at',
        )
        read_only_fields = fields
