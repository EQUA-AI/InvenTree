"""Serializers for the AI Agent Approval Queue API."""

import json

from django.db import IntegrityError
from rest_framework import serializers

from InvenTree.serializers import InvenTreeModelSerializer

from .models import (
    ActionType,
    Approval,
    ApprovalEvent,
    ApprovalRevision,
    ApprovalStatus,
    EventType,
    ExecutedEffect,
    compute_idempotency_key,
    get_default_expiry_days,
)
from .sanitizers import sanitize_payload


# ---------------------------------------------------------------------------
# Read-only / list serializers
# ---------------------------------------------------------------------------


class ApprovalEventSerializer(InvenTreeModelSerializer):
    """Serializer for ApprovalEvent (read-only audit log)."""

    class Meta:
        model = ApprovalEvent
        fields = [
            'id',
            'approval',
            'event_type',
            'actor_user',
            'timestamp',
            'event_payload',
        ]
        read_only_fields = fields


class ApprovalRevisionSerializer(InvenTreeModelSerializer):
    """Serializer for ApprovalRevision (read-only revision history)."""

    class Meta:
        model = ApprovalRevision
        fields = [
            'id',
            'approval',
            'revision_number',
            'payload_snapshot',
            'diff_summary',
            'created_at',
            'created_by_user',
        ]
        read_only_fields = fields


class ExecutedEffectSerializer(InvenTreeModelSerializer):
    """Serializer for ExecutedEffect (idempotency ledger)."""

    class Meta:
        model = ExecutedEffect
        fields = [
            'idempotency_key',
            'approval',
            'effect_type',
            'effect_ref',
            'created_at',
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Approval serializers (list, detail, creation)
# ---------------------------------------------------------------------------


class ApprovalListSerializer(InvenTreeModelSerializer):
    """Compact serializer for approval list views."""

    is_terminal = serializers.BooleanField(read_only=True)
    is_lock_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Approval
        fields = [
            'id',
            'status',
            'risk_tier',
            'action_type',
            'summary',
            'source_chat_id',
            'agent_run_id',
            'tool_call_id',
            'assigned_to_user',
            'created_at',
            'updated_at',
            'expires_at',
            'current_revision_number',
            'is_terminal',
            'is_lock_active',
            'resolved_at',
        ]
        read_only_fields = fields


class ApprovalDetailSerializer(InvenTreeModelSerializer):
    """Full serializer for approval detail views."""

    is_terminal = serializers.BooleanField(read_only=True)
    is_lock_active = serializers.BooleanField(read_only=True)
    lock_holder_id = serializers.IntegerField(read_only=True, allow_null=True)
    latest_events = serializers.SerializerMethodField()
    latest_revision = serializers.SerializerMethodField()

    class Meta:
        model = Approval
        fields = [
            'id',
            'status',
            'risk_tier',
            'action_type',
            'summary',
            'payload',
            'payload_schema_version',
            'source_chat_id',
            'agent_run_id',
            'agent_checkpoint_id',
            'tool_call_id',
            'assigned_to_user',
            'created_at',
            'updated_at',
            'expires_at',
            'baseline_context',
            'preconditions',
            'card_context',
            'idempotency_key',
            'viewed_confirmed_at',
            'viewed_confirmed_by_user',
            'current_revision_number',
            'modification_lock_user',
            'modification_lock_acquired_at',
            'modification_lock_expires_at',
            'deny_reason',
            'canceled_reason',
            'execution_result',
            'execution_error',
            'resolved_at',
            'resolved_by_user',
            'is_terminal',
            'is_lock_active',
            'lock_holder_id',
            'latest_events',
            'latest_revision',
        ]
        read_only_fields = fields

    def get_latest_events(self, obj):
        """Return the 10 most recent events."""
        events = obj.events.order_by('-timestamp')[:10]
        return ApprovalEventSerializer(events, many=True).data

    def get_latest_revision(self, obj):
        """Return the latest revision."""
        revision = obj.revisions.order_by('-revision_number').first()
        if revision:
            return ApprovalRevisionSerializer(revision).data
        return None


class ApprovalCreateSerializer(serializers.Serializer):
    """Serializer for creating a new approval (agent/service-internal).

    Section 7.0 of the spec.
    """

    tool_call_id = serializers.CharField(max_length=255, required=True)
    agent_run_id = serializers.CharField(max_length=255, required=True)
    agent_checkpoint_id = serializers.CharField(max_length=255, required=True)
    action_type = serializers.ChoiceField(choices=ActionType.choices, required=True)
    summary = serializers.CharField(required=True)
    payload = serializers.JSONField(required=True)
    payload_schema_version = serializers.IntegerField(default=1, required=False)
    source_chat_id = serializers.CharField(
        max_length=255, default='', required=False, allow_blank=True
    )
    card_context = serializers.JSONField(default=dict, required=False)
    baseline_context = serializers.JSONField(default=dict, required=False)
    preconditions = serializers.JSONField(default=dict, required=False)
    assigned_to_user_id = serializers.IntegerField(
        required=False, allow_null=True, default=None
    )

    def validate(self, attrs):
        """Compute derived fields, validate payload, and check size."""
        # Compute idempotency_key
        attrs['idempotency_key'] = compute_idempotency_key(
            attrs['agent_run_id'], attrs['tool_call_id']
        )

        # Phase 1: always risk_tier = 2
        attrs['risk_tier'] = 2

        # Compute expires_at
        from django.utils import timezone
        import datetime

        expiry_days = get_default_expiry_days()
        attrs['expires_at'] = timezone.now() + datetime.timedelta(days=expiry_days)

        # Sanitize payload (D-5)
        attrs['payload'] = sanitize_payload(
            attrs['payload'], attrs['action_type']
        )

        # Executor validation (E-3): surface warnings from executor.validate()
        from .executors import registry
        if registry.has(attrs['action_type']):
            executor = registry.get(attrs['action_type'])
            warnings = executor.validate(attrs['payload'])
            if warnings:
                attrs['_validation_warnings'] = warnings

        # Check payload size (50 MB limit)
        total_size = sum(
            len(json.dumps(attrs.get(key, {})))
            for key in ('payload', 'card_context', 'baseline_context', 'preconditions')
        )
        max_bytes = 50 * 1024 * 1024  # 50 MB
        if total_size > max_bytes:
            raise serializers.ValidationError({
                'error': 'payload_too_large',
                'detail': f'Combined payload exceeds {max_bytes} bytes',
                'max_bytes': max_bytes,
            })

        return attrs

    def create(self, validated_data):
        """Create the approval, revision 0, and created event.

        Handles D-1: idempotency race condition by catching IntegrityError.
        """
        from django.contrib.auth import get_user_model
        from django.db import transaction
        from django.utils import timezone

        User = get_user_model()

        assigned_to_user_id = validated_data.pop('assigned_to_user_id', None)
        validation_warnings = validated_data.pop('_validation_warnings', [])
        assigned_to_user = None
        if assigned_to_user_id:
            try:
                assigned_to_user = User.objects.get(pk=assigned_to_user_id)
            except User.DoesNotExist:
                pass

        # Check idempotency: return existing if key matches
        existing = Approval.objects.filter(
            idempotency_key=validated_data['idempotency_key']
        ).first()
        if existing:
            existing._was_existing = True
            return existing

        # D-1: Catch IntegrityError for concurrent identical requests
        try:
            with transaction.atomic():
                approval = Approval.objects.create(
                    status=ApprovalStatus.PENDING,
                    risk_tier=validated_data['risk_tier'],
                    action_type=validated_data['action_type'],
                    summary=validated_data['summary'],
                    payload=validated_data['payload'],
                    payload_schema_version=validated_data.get('payload_schema_version', 1),
                    source_chat_id=validated_data.get('source_chat_id', ''),
                    agent_run_id=validated_data['agent_run_id'],
                    agent_checkpoint_id=validated_data['agent_checkpoint_id'],
                    tool_call_id=validated_data['tool_call_id'],
                    assigned_to_user=assigned_to_user,
                    expires_at=validated_data['expires_at'],
                    baseline_context=validated_data.get('baseline_context', {}),
                    preconditions=validated_data.get('preconditions', {}),
                    card_context=validated_data.get('card_context', {}),
                    idempotency_key=validated_data['idempotency_key'],
                    current_revision_number=0,
                )

                # Create revision 0 (initial snapshot)
                ApprovalRevision.objects.create(
                    approval=approval,
                    revision_number=0,
                    payload_snapshot=validated_data['payload'],
                    diff_summary=None,
                    created_by_user=None,
                )

                # Emit created event (include validation warnings from E-3)
                event_payload = {
                    'action_type': validated_data['action_type'],
                    'risk_tier': validated_data['risk_tier'],
                    'agent_run_id': validated_data['agent_run_id'],
                    'tool_call_id': validated_data['tool_call_id'],
                }
                if validation_warnings:
                    event_payload['validation_warnings'] = validation_warnings

                ApprovalEvent.objects.create(
                    approval=approval,
                    event_type=EventType.CREATED,
                    actor_user=None,
                    event_payload=event_payload,
                )

        except IntegrityError:
            # D-1: Race condition — another request created the same approval
            approval = Approval.objects.get(
                idempotency_key=validated_data['idempotency_key']
            )
            approval._was_existing = True

        return approval


# ---------------------------------------------------------------------------
# Card package serializer (for Modify-in-chat)
# ---------------------------------------------------------------------------


class CardPackageSerializer(serializers.Serializer):
    """Serializer for the card-package endpoint (Section 7.1).

    Returns the exact context bundle needed by Modify-in-chat.
    """

    approval_id = serializers.UUIDField(source='id')
    summary = serializers.CharField()
    risk_tier = serializers.IntegerField()
    action_type = serializers.CharField()
    status = serializers.CharField()
    payload = serializers.JSONField()
    card_context = serializers.JSONField()
    baseline_context = serializers.JSONField()
    preconditions = serializers.JSONField()
    latest_diff_summary = serializers.SerializerMethodField()
    validation_warnings = serializers.SerializerMethodField()

    def get_latest_diff_summary(self, obj):
        """Return the diff_summary from the latest revision."""
        latest = obj.revisions.order_by('-revision_number').first()
        if latest:
            return latest.diff_summary
        return None

    def get_validation_warnings(self, obj):
        """Return validation warnings (placeholder for future revalidation)."""
        warnings = []
        # Check stale baseline
        from django.utils import timezone

        from .models import get_baseline_stale_threshold_hours

        threshold_hours = get_baseline_stale_threshold_hours()
        if obj.created_at:
            age_hours = (timezone.now() - obj.created_at).total_seconds() / 3600
            if age_hours > threshold_hours:
                warnings.append(
                    f'Baseline context is {age_hours:.0f}h old '
                    f'(threshold: {threshold_hours}h). '
                    'Live checks will still be performed at approve-time.'
                )
        return warnings


# ---------------------------------------------------------------------------
# Write action serializers (decision endpoints)
# ---------------------------------------------------------------------------


class OpenApprovalSerializer(serializers.Serializer):
    """Serializer for POST /open (no body required)."""

    pass


class ConfirmViewedSerializer(serializers.Serializer):
    """Serializer for POST /confirm-viewed (no body required)."""

    pass


class RequestChangesSerializer(serializers.Serializer):
    """Serializer for POST /request-changes."""

    instructions = serializers.CharField(required=True)


class ApproveSerializer(serializers.Serializer):
    """Serializer for POST /approve (no body required)."""

    pass


class DenySerializer(serializers.Serializer):
    """Serializer for POST /deny."""

    reason = serializers.CharField(required=True)


class CancelSerializer(serializers.Serializer):
    """Serializer for POST /cancel."""

    reason = serializers.CharField(required=False, default='', allow_blank=True)


class ReviseSerializer(serializers.Serializer):
    """Serializer for POST /revise."""

    payload = serializers.JSONField(required=True)
    diff_summary = serializers.JSONField(required=False, default=None, allow_null=True)
    note = serializers.CharField(required=False, default='', allow_blank=True)
    expected_revision = serializers.IntegerField(required=True)

    def validate_payload(self, value):
        """A-9: Check payload size before acquiring row lock."""
        max_bytes = 50 * 1024 * 1024  # 50 MB
        payload_size = len(json.dumps(value))
        if payload_size > max_bytes:
            raise serializers.ValidationError(
                f'Payload exceeds {max_bytes} bytes'
            )
        return value


class AcquireModifyLockSerializer(serializers.Serializer):
    """Serializer for POST /acquire-modify-lock (no body required)."""

    pass


class ReleaseModifyLockSerializer(serializers.Serializer):
    """Serializer for POST /release-modify-lock (no body required)."""

    pass


# ---------------------------------------------------------------------------
# Count serializer
# ---------------------------------------------------------------------------


class ApprovalCountSerializer(serializers.Serializer):
    """Serializer for GET /count endpoint."""

    count = serializers.IntegerField()
