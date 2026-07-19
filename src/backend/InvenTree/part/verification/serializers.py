"""Read resources and explicit command serializers for part verification.

Read serializers never expose client-writable authority fields; every state
change flows through an explicit command serializer plus the transactional
services (FR-RPF-009).
"""

from rest_framework import serializers

from part.verification.schema import PartVerificationPurpose
from part.verification_models import (
    PartCandidateEvaluation,
    PartVerificationDecision,
    PartVerificationEvent,
    PartVerificationEvidence,
    PartVerificationRequirement,
    PartVerificationSession,
    PartVerificationUse,
)


class PartVerificationSessionSerializer(serializers.ModelSerializer):
    """Read resource for one verification session."""

    class Meta:
        """Serializer options."""

        model = PartVerificationSession
        fields = [
            'pk',
            'reference',
            'purpose',
            'state',
            'revision',
            'scope_customer',
            'scope_site_key',
            'requested_part',
            'requested_part_name',
            'machine',
            'machine_part',
            'bom_item',
            'work_order',
            'job_kit_line',
            'policy_key',
            'policy_version',
            'current_decision',
            'stale_reason',
            'expires_at',
            'universe_complete',
            'considered_count',
            'eligible_count',
            'created_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    requested_part_name = serializers.CharField(
        source='requested_part.name', read_only=True, default=None
    )
    policy_key = serializers.CharField(source='policy.key', read_only=True)
    policy_version = serializers.IntegerField(source='policy.version', read_only=True)


class PartVerificationRequirementSerializer(serializers.ModelSerializer):
    """Read resource for one typed requirement."""

    class Meta:
        """Serializer options."""

        model = PartVerificationRequirement
        fields = [
            'pk',
            'key',
            'category',
            'value_kind',
            'operator',
            'value',
            'raw_value',
            'unit',
            'tolerance',
            'hard_constraint',
            'resolution',
            'blocker_code',
            'authority',
            'provenance',
        ]
        read_only_fields = fields


class PartVerificationEvidenceSerializer(serializers.ModelSerializer):
    """Read resource for one evidence item (safe projection)."""

    class Meta:
        """Serializer options."""

        model = PartVerificationEvidence
        fields = [
            'pk',
            'requirement_key',
            'source_kind',
            'source_model',
            'source_object_id',
            'source_field',
            'source_fingerprint',
            'digest',
            'raw_value',
            'canonical_value',
            'unit',
            'authority',
            'origin',
            'decision',
            'decided_by',
            'decided_at',
            'expires_at',
            'created_by',
            'created_at',
        ]
        read_only_fields = fields


class PartCandidateEvaluationSerializer(serializers.ModelSerializer):
    """Read resource for one candidate evaluation (full comparison)."""

    class Meta:
        """Serializer options."""

        model = PartCandidateEvaluation
        fields = [
            'pk',
            'session',
            'session_revision',
            'candidate',
            'candidate_name',
            'candidate_ipn',
            'retrieval_tiers',
            'eligible',
            'hard_conflicts',
            'matched_attributes',
            'missing_attributes',
            'rank_factors',
            'rank_value',
            'rank',
            'availability_snapshot',
            'evaluation_hash',
            'evaluated_at',
            'rejected',
            'rejected_reason',
        ]
        read_only_fields = fields

    candidate_name = serializers.CharField(source='candidate.name', read_only=True)
    candidate_ipn = serializers.CharField(
        source='candidate.IPN', read_only=True, default=None
    )


class PartVerificationDecisionSerializer(serializers.ModelSerializer):
    """Read resource for one immutable decision."""

    class Meta:
        """Serializer options."""

        model = PartVerificationDecision
        fields = [
            'pk',
            'session',
            'session_revision',
            'kind',
            'selected_evaluation',
            'selected_part',
            'decision_hash',
            'requirements_hash',
            'source_fingerprint',
            'evaluation_hash',
            'policy_hash',
            'scope_fingerprint',
            'decided_by',
            'reason',
            'decided_at',
            'valid_until',
        ]
        read_only_fields = fields


class PartVerificationUseSerializer(serializers.ModelSerializer):
    """Read resource for one consumer use binding."""

    class Meta:
        """Serializer options."""

        model = PartVerificationUse
        fields = [
            'pk',
            'decision',
            'consumer_kind',
            'consumer_model',
            'consumer_object_id',
            'consumer_action',
            'final_observation_hash',
            'idempotency_key',
            'actor',
            'created_at',
        ]
        read_only_fields = fields


class PartVerificationEventSerializer(serializers.ModelSerializer):
    """Read resource for one append-only event."""

    class Meta:
        """Serializer options."""

        model = PartVerificationEvent
        fields = [
            'pk',
            'event_type',
            'state',
            'reason',
            'actor',
            'correlation_id',
            'metadata',
            'created_at',
        ]
        read_only_fields = fields


class _CommandSerializer(serializers.Serializer):
    """Base command envelope: stable idempotency key."""

    idempotency_key = serializers.CharField(max_length=64)


class CreateSessionSerializer(_CommandSerializer):
    """Intent to create one scoped verification session."""

    purpose = serializers.ChoiceField(choices=PartVerificationPurpose.choices)
    requested_part_id = serializers.IntegerField(required=False, allow_null=True)
    machine_id = serializers.IntegerField(required=False, allow_null=True)
    machine_part_id = serializers.IntegerField(required=False, allow_null=True)
    bom_item_id = serializers.IntegerField(required=False, allow_null=True)
    work_order_id = serializers.IntegerField(required=False, allow_null=True)
    job_kit_line_id = serializers.IntegerField(required=False, allow_null=True)


class RevisionCommandSerializer(_CommandSerializer):
    """Command envelope requiring the expected session revision."""

    expected_revision = serializers.IntegerField(min_value=1)


class ReasonedRevisionCommandSerializer(RevisionCommandSerializer):
    """Revision command that requires a human rationale."""

    reason = serializers.CharField()


class ReasonedCommandSerializer(_CommandSerializer):
    """Command envelope that requires a human rationale."""

    reason = serializers.CharField()


class CancelCommandSerializer(_CommandSerializer):
    """Intent to cancel a session."""

    reason = serializers.CharField(required=False, allow_blank=True, default='')


class AttachEvidenceSerializer(_CommandSerializer):
    """Intent to attach one proposed evidence item."""

    requirement_key = serializers.CharField(max_length=100)
    value = serializers.JSONField(required=False, allow_null=True)
    unit = serializers.CharField(
        max_length=25, required=False, allow_blank=True, default=''
    )
    source_kind = serializers.CharField(
        max_length=32, required=False, default='observation'
    )
    digest = serializers.CharField(
        max_length=71, required=False, allow_blank=True, default=''
    )
    expires_at = serializers.DateTimeField(
        required=False, allow_null=True, default=None
    )


class DecideEvidenceSerializer(_CommandSerializer):
    """Intent to accept or reject one proposed evidence item."""

    accept = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_blank=True, default='')
