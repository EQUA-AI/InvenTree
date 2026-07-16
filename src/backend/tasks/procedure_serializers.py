"""Serializers for governed procedure authoring and review."""

from rest_framework import serializers

from .models import (
    Procedure,
    ProcedureResourceRequirement,
    ProcedureRevision,
    ProcedureRevisionStatus,
    ProcedureStep,
)


class ProcedureSerializer(serializers.ModelSerializer):
    """Stable procedure-family metadata."""

    class Meta:
        """Serializer metadata."""

        model = Procedure
        fields = (
            'id',
            'code',
            'name',
            'description',
            'customer',
            'active',
            'current_revision',
            'created_by',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'current_revision',
            'created_by',
            'created_at',
            'updated_at',
        )


class ProcedureRevisionSerializer(serializers.ModelSerializer):
    """Revision resource with lifecycle-owned fields always read-only."""

    class Meta:
        """Serializer metadata."""

        model = ProcedureRevision
        fields = (
            'id',
            'procedure',
            'revision',
            'status',
            'work_order_type',
            'change_summary',
            'default_estimated_minutes',
            'review_due_at',
            'schema_version',
            'content_hash',
            'content_version',
            'created_by',
            'reviewed_by',
            'published_by',
            'created_at',
            'published_at',
        )
        read_only_fields = (
            'id',
            'procedure',
            'revision',
            'status',
            'content_hash',
            'content_version',
            'created_by',
            'reviewed_by',
            'published_by',
            'created_at',
            'published_at',
        )

    def get_fields(self):
        """Make all definition fields immutable once a revision leaves draft."""
        fields = super().get_fields()
        if (
            self.instance is not None
            and self.instance.status != ProcedureRevisionStatus.DRAFT
        ):
            for name in (
                'work_order_type',
                'change_summary',
                'default_estimated_minutes',
                'review_due_at',
                'schema_version',
            ):
                fields[name].read_only = True
        return fields


class ProcedureStepSerializer(serializers.ModelSerializer):
    """Procedure step definition; identity and parent are server-owned."""

    class Meta:
        """Serializer metadata."""

        model = ProcedureStep
        fields = (
            'id',
            'revision',
            'key',
            'sequence',
            'step_type',
            'title',
            'instruction',
            'required',
            'estimated_minutes',
            'required_permission',
            'value_type',
            'unit',
            'min_value',
            'max_value',
            'allowed_values',
            'evidence_policy',
            'safety_gate_template',
        )
        read_only_fields = ('id', 'revision', 'key')

    def get_fields(self):
        """Get fields."""
        fields = super().get_fields()
        revision = getattr(self.instance, 'revision', None)
        if revision is not None and revision.status != ProcedureRevisionStatus.DRAFT:
            for field in fields.values():
                field.read_only = True
        return fields


class ProcedureResourceRequirementSerializer(serializers.ModelSerializer):
    """Catalog-backed procedure resource definition."""

    class Meta:
        """Serializer metadata."""

        model = ProcedureResourceRequirement
        fields = (
            'id',
            'revision',
            'key',
            'sequence',
            'kind',
            'part',
            'quantity',
            'fulfillment_mode',
            'required',
            'substitution_policy',
            'requires_scan',
            'notes',
        )
        read_only_fields = ('id', 'revision', 'key')

    def get_fields(self):
        """Get fields."""
        fields = super().get_fields()
        revision = getattr(self.instance, 'revision', None)
        if revision is not None and revision.status != ProcedureRevisionStatus.DRAFT:
            for field in fields.values():
                field.read_only = True
        return fields


class ProcedureBlockerSerializer(serializers.Serializer):
    """Stable publication blocker representation."""

    code = serializers.CharField()
    message = serializers.CharField()


class ExpectedContentVersionSerializer(serializers.Serializer):
    """Base optimistic-concurrency command."""

    expected_content_version = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        """Require the token even when a resource PATCH uses partial validation."""
        if 'expected_content_version' not in attrs:
            raise serializers.ValidationError({
                'expected_content_version': 'This field is required.'
            })
        return super().validate(attrs)


class CreateDraftRevisionSerializer(serializers.Serializer):
    """Create the server-numbered next draft revision."""


class EditDraftRevisionSerializer(ExpectedContentVersionSerializer):
    """Edit mutable revision metadata."""

    work_order_type = serializers.ChoiceField(
        choices=ProcedureRevision._meta.get_field('work_order_type').choices,
        required=False,
    )
    change_summary = serializers.CharField(required=False, allow_blank=True)
    default_estimated_minutes = serializers.IntegerField(
        required=False, allow_null=True, min_value=0
    )
    review_due_at = serializers.DateTimeField(required=False, allow_null=True)
    schema_version = serializers.IntegerField(required=False, min_value=1)


class EditDraftStepSerializer(
    ExpectedContentVersionSerializer, ProcedureStepSerializer
):
    """Create or edit a step with optimistic concurrency."""

    class Meta(ProcedureStepSerializer.Meta):
        """Serializer metadata."""

        fields = ('expected_content_version', *ProcedureStepSerializer.Meta.fields)
        read_only_fields = ProcedureStepSerializer.Meta.read_only_fields


class EditDraftResourceSerializer(
    ExpectedContentVersionSerializer, ProcedureResourceRequirementSerializer
):
    """Create or edit a resource with optimistic concurrency."""

    class Meta(ProcedureResourceRequirementSerializer.Meta):
        """Serializer metadata."""

        fields = (
            'expected_content_version',
            *ProcedureResourceRequirementSerializer.Meta.fields,
        )
        read_only_fields = ProcedureResourceRequirementSerializer.Meta.read_only_fields


class ReorderStepsSerializer(ExpectedContentVersionSerializer):
    """Replace ordering with the full stable-key list."""

    step_keys = serializers.ListField(child=serializers.UUIDField(), allow_empty=True)


class RequestReviewSerializer(ExpectedContentVersionSerializer):
    """Freeze the exact draft version for human review."""


class PublishProcedureSerializer(serializers.Serializer):
    """Resolve the pending approval entry for this reviewed revision."""

    approval_id = serializers.UUIDField(required=False)


class ArchiveProcedureRevisionSerializer(serializers.Serializer):
    """Controlled revision archive command."""

    reason = serializers.CharField(required=False, allow_blank=True, default='')
