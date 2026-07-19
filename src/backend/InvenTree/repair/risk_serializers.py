"""Serializers for the Risk Radar / Command Center API."""

from django.utils import timezone

from rest_framework import serializers

from .risk_models import (
    RiskActionLink,
    RiskFinding,
    RiskFindingEvent,
    RiskRuleDefinition,
)


class RiskActionLinkSerializer(serializers.ModelSerializer):
    """Serialize a governed deep link attached to a finding."""

    class Meta:
        """Serializer metadata."""

        model = RiskActionLink
        fields = ('label', 'target_kind', 'target_id', 'route')
        read_only_fields = fields


class RiskFindingEventSerializer(serializers.ModelSerializer):
    """Serialize an immutable finding event."""

    actor_username = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        """Serializer metadata."""

        model = RiskFindingEvent
        fields = (
            'pk',
            'event_type',
            'actor',
            'actor_username',
            'reason',
            'metadata',
            'created_at',
        )
        read_only_fields = fields


class RiskFindingSerializer(serializers.ModelSerializer):
    """Serialize a finding for list views (no evidence payload)."""

    owner_username = serializers.CharField(source='owner.username', read_only=True)
    age_hours = serializers.SerializerMethodField()
    due_breached = serializers.SerializerMethodField()

    class Meta:
        """Serializer metadata."""

        model = RiskFinding
        fields = (
            'pk',
            'scope_key',
            'rule_code',
            'rule_version',
            'category',
            'severity',
            'severity_factors',
            'source_model',
            'source_id',
            'title',
            'summary',
            'state',
            'owner',
            'owner_username',
            'first_seen',
            'last_seen',
            'condition_started_at',
            'source_as_of',
            'due_at',
            'due_breached',
            'age_hours',
            'snooze_until',
            'dismiss_recheck_at',
            'reopen_count',
            'version',
        )
        read_only_fields = fields

    def get_age_hours(self, obj) -> float:
        """Return the condition age in hours."""
        delta = timezone.now() - obj.condition_started_at
        return round(max(delta.total_seconds(), 0) / 3600, 1)

    def get_due_breached(self, obj) -> bool:
        """Return True when the finding's due timestamp has passed."""
        return bool(obj.due_at and obj.due_at <= timezone.now())


class RiskFindingDetailSerializer(RiskFindingSerializer):
    """Serialize a finding with evidence and event history."""

    events = RiskFindingEventSerializer(many=True, read_only=True)
    action_links = RiskActionLinkSerializer(many=True, read_only=True)

    class Meta(RiskFindingSerializer.Meta):
        """Serializer metadata."""

        fields = (
            *RiskFindingSerializer.Meta.fields,
            'evidence',
            'events',
            'action_links',
        )
        read_only_fields = fields


class RiskRuleSerializer(serializers.ModelSerializer):
    """Serialize the current revision of a rule definition."""

    class Meta:
        """Serializer metadata."""

        model = RiskRuleDefinition
        fields = (
            'pk',
            'code',
            'version',
            'category',
            'schedule',
            'watermark_strategy',
            'default_severity_policy',
            'config',
            'critical_rule',
            'notification_policy',
            'enabled_scopes',
            'enabled',
            'is_current',
            'activation_generation',
            'created_at',
        )
        read_only_fields = fields


class RiskCommandSerializer(serializers.Serializer):
    """Base envelope for finding lifecycle commands (FR-RR-006)."""

    expected_version = serializers.IntegerField(min_value=1, required=True)
    idempotency_key = serializers.CharField(
        max_length=128, required=True, allow_blank=False
    )
    reason = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000
    )


class RiskAssignSerializer(RiskCommandSerializer):
    """Command payload for assignment."""

    owner_id = serializers.IntegerField(required=False, allow_null=True, default=None)


class RiskSnoozeSerializer(RiskCommandSerializer):
    """Command payload for snoozing.

    ``snooze_until`` is validated by the service so a missing or past value
    surfaces as the stable ``SNOOZE_INVALID`` envelope code rather than a
    bare serializer error.
    """

    snooze_until = serializers.DateTimeField(
        required=False, allow_null=True, default=None
    )


class RiskDismissSerializer(RiskCommandSerializer):
    """Command payload for dismissal (reason + recheck policy).

    The reason requirement is enforced by the service so it surfaces as the
    stable ``DISMISS_REASON_REQUIRED`` envelope code.
    """

    recheck_hours = serializers.FloatField(
        required=False, allow_null=True, default=None, min_value=1
    )


class RiskRuleUpdateSerializer(serializers.Serializer):
    """Admin payload creating the next immutable rule revision."""

    config = serializers.JSONField(required=False)
    enabled = serializers.BooleanField(required=False)
    enabled_scopes = serializers.ListField(
        child=serializers.CharField(max_length=128), required=False
    )
    notification_policy = serializers.JSONField(required=False)
    critical_rule = serializers.BooleanField(required=False)
    reason = serializers.CharField(required=True, allow_blank=False, max_length=2000)
