"""Serializers for the Closeout Automation API (Feature #15)."""

from rest_framework import serializers

from .closeout_models import (
    CloseoutAmendment,
    CloseoutCapture,
    CloseoutEffect,
    CloseoutFieldDecision,
    CloseoutPartUsage,
    CloseoutProposal,
    CloseoutReading,
)
from .workorder_serializers import BaseCommandSerializer


class CloseoutCaptureSerializer(serializers.ModelSerializer):
    """Read-only capture resource with its current revision inline."""

    revision = serializers.IntegerField(
        source='current_revision.revision', read_only=True, default=None
    )
    narrative = serializers.CharField(
        source='current_revision.narrative', read_only=True, default=''
    )

    class Meta:
        """Serializer metadata."""

        model = CloseoutCapture
        fields = (
            'id',
            'work_order',
            'status',
            'source_type',
            'transcript_reference',
            'revision',
            'narrative',
            'completed_closeout',
            'created_by',
            'created_at',
        )
        read_only_fields = fields


class CaptureCreateCommandSerializer(BaseCommandSerializer):
    """Validate typed narrative capture intent."""

    narrative = serializers.CharField(allow_blank=False)


class CaptureReviseCommandSerializer(BaseCommandSerializer):
    """Validate a revise (new revision) or abandon intent."""

    narrative = serializers.CharField(required=False, allow_blank=True, default='')
    expected_revision = serializers.IntegerField(required=False, min_value=1)
    abandon = serializers.BooleanField(required=False, default=False)


class CloseoutFieldDecisionSerializer(serializers.ModelSerializer):
    """Read-only decision row."""

    class Meta:
        """Serializer metadata."""

        model = CloseoutFieldDecision
        fields = (
            'id',
            'field_path',
            'origin',
            'decision',
            'final_value',
            'decided_by',
            'decided_at',
        )
        read_only_fields = fields


class CloseoutProposalSerializer(serializers.ModelSerializer):
    """Bounded proposal resource; never a raw model dump."""

    decisions = CloseoutFieldDecisionSerializer(many=True, read_only=True)

    class Meta:
        """Serializer metadata."""

        model = CloseoutProposal
        fields = (
            'id',
            'capture_revision',
            'schema_version',
            'extractor',
            'fields',
            'part_candidates',
            'reading_candidates',
            'warnings',
            'content_hash',
            'status',
            'created_at',
            'decisions',
        )
        read_only_fields = fields


class DecisionEntrySerializer(serializers.Serializer):
    """One explicit per-field promotion decision."""

    field_path = serializers.CharField(max_length=128)
    decision = serializers.ChoiceField(choices=['accepted', 'edited', 'rejected'])
    final_value = serializers.JSONField(required=False, allow_null=True)


class DecisionBatchCommandSerializer(BaseCommandSerializer):
    """Validate a batch of field decisions."""

    decisions = DecisionEntrySerializer(many=True, allow_empty=False)


class CloseoutPartUsageSerializer(serializers.ModelSerializer):
    """Read-only reconciliation row."""

    class Meta:
        """Serializer metadata."""

        model = CloseoutPartUsage
        fields = (
            'id',
            'work_order',
            'allocation',
            'part',
            'stock_item',
            'planned_quantity',
            'issued_quantity',
            'used_quantity',
            'disposition',
            'variance_reason',
            'stock_tracking_id',
            'source',
            'candidate_text',
            'resolved_by',
            'state',
            'version',
        )
        read_only_fields = fields


class PartUsageCreateSerializer(serializers.Serializer):
    """Add a walk-up usage row or an unresolved narrative candidate."""

    kind = serializers.ChoiceField(choices=['walkup', 'candidate'])
    stock_item = serializers.IntegerField(required=False)
    used_quantity = serializers.DecimalField(
        max_digits=15, decimal_places=5, required=False
    )
    stock_tracking_id = serializers.IntegerField(required=False)
    candidate_text = serializers.CharField(required=False, allow_blank=True, default='')
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class PartUsageResolveSerializer(serializers.Serializer):
    """Resolve one usage row with an explicit disposition."""

    disposition = serializers.CharField(max_length=24)
    reason = serializers.CharField(required=False, allow_blank=True, default='')
    used_quantity = serializers.DecimalField(
        max_digits=15, decimal_places=5, required=False, allow_null=True, default=None
    )
    expected_row_version = serializers.IntegerField(required=False, allow_null=True)


class CloseoutReadingSerializer(serializers.ModelSerializer):
    """Read-only reading row."""

    class Meta:
        """Serializer metadata."""

        model = CloseoutReading
        fields = (
            'id',
            'work_order',
            'step_execution',
            'label',
            'phase',
            'raw_text',
            'warnings',
            'value',
            'unit',
            'expected_min',
            'expected_max',
            'required',
            'normalization_rule_version',
            'verification_state',
            'disposition_reason',
            'recorded_by',
            'recorded_at',
        )
        read_only_fields = fields


class ReadingCreateSerializer(serializers.Serializer):
    """Record one closeout reading."""

    label = serializers.CharField(max_length=128)
    raw_text = serializers.CharField(max_length=64, allow_blank=True, default='')
    unit = serializers.CharField(
        max_length=32, required=False, allow_blank=True, default=''
    )
    phase = serializers.ChoiceField(choices=['before', 'after'], default='after')
    required = serializers.BooleanField(required=False, default=False)
    expected_min = serializers.DecimalField(
        max_digits=20, decimal_places=6, required=False, allow_null=True, default=None
    )
    expected_max = serializers.DecimalField(
        max_digits=20, decimal_places=6, required=False, allow_null=True, default=None
    )
    step_execution = serializers.IntegerField(required=False, allow_null=True)
    evidence_attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )


class ReadingDispositionSerializer(serializers.Serializer):
    """Resolve a failed or ambiguous reading."""

    disposition = serializers.ChoiceField(
        choices=['retest', 'deviation', 'supervisor_review']
    )
    reason = serializers.CharField()


class CloseoutEffectSerializer(serializers.ModelSerializer):
    """Read-only effect ledger row with retry visibility."""

    class Meta:
        """Serializer metadata."""

        model = CloseoutEffect
        fields = (
            'id',
            'closeout',
            'effect_type',
            'effect_key',
            'status',
            'attempts',
            'next_retry_at',
            'reconciliation_due_at',
            'last_error',
            'result_reference',
            'created_at',
            'resolved_at',
        )
        read_only_fields = fields


class CloseoutAmendmentSerializer(serializers.ModelSerializer):
    """Read-only amendment resource."""

    class Meta:
        """Serializer metadata."""

        model = CloseoutAmendment
        fields = (
            'id',
            'closeout',
            'changes',
            'base_content_hash',
            'reason',
            'requested_by',
            'status',
            'effective_snapshot',
            'effective_snapshot_hash',
            'decided_by',
            'created_at',
            'applied_at',
        )
        read_only_fields = fields


class AmendmentProposeSerializer(BaseCommandSerializer):
    """Propose a governed correction."""

    changes = serializers.DictField()
    reason = serializers.CharField(allow_blank=False)


class AmendmentDecideSerializer(BaseCommandSerializer):
    """Approve or reject one proposed amendment."""

    approve = serializers.BooleanField()


class EffectRetrySerializer(serializers.Serializer):
    """Authorized manual effect retry."""

    reason = serializers.CharField(required=False, allow_blank=True, default='')
