"""Serializers for applying and executing governed procedures."""

import uuid

from rest_framework import serializers

from .models import (
    WorkOrderDeviation,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)


class ProcedureApplicationSerializer(serializers.ModelSerializer):
    """Read-only immutable procedure application resource."""

    class Meta:
        """Serializer metadata."""

        model = WorkOrderProcedureApplication
        fields = (
            'id',
            'work_order',
            'revision',
            'sequence',
            'primary',
            'snapshot',
            'snapshot_hash',
            'policy_version',
            'applied_by',
            'applied_at',
            'idempotency_key',
            'drift_status',
        )
        read_only_fields = fields


class StepExecutionSerializer(serializers.ModelSerializer):
    """Read-only state and exact snapshot for one applied step."""

    class Meta:
        """Serializer metadata."""

        model = WorkOrderStepExecution
        fields = (
            'id',
            'application',
            'step_key',
            'sequence',
            'step_snapshot',
            'status',
            'value',
            'passed',
            'note',
            'completed_by',
            'completed_at',
            'disposition_reason',
            'version',
        )
        read_only_fields = fields


class WorkOrderDeviationSerializer(serializers.ModelSerializer):
    """Controlled work-order deviation resource.

    Ownership, actor, approval, and resolution fields cannot be selected by a
    create request. Resolution is a separate future workflow.
    """

    class Meta:
        """Serializer metadata."""

        model = WorkOrderDeviation
        fields = (
            'id',
            'work_order',
            'category',
            'application_key',
            'step_key',
            'resource_key',
            'expected',
            'actual',
            'reason',
            'actor',
            'approval',
            'resolution',
            'created_at',
            'resolved_at',
        )
        read_only_fields = (
            'id',
            'work_order',
            'actor',
            'approval',
            'resolution',
            'created_at',
            'resolved_at',
        )

    def validate(self, attrs):
        """Reject application and step references outside the scoped parent."""
        work_order = self.context.get('work_order')
        application_key = attrs.get('application_key', '')
        step_key = attrs.get('step_key', '')

        if work_order is None or not application_key:
            if step_key:
                raise serializers.ValidationError({
                    'application_key': 'An application is required for a step deviation.'
                })
            return attrs

        try:
            application_id = int(application_key)
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError({
                'application_key': 'Application identifier is invalid.'
            }) from exc
        applications = work_order.procedure_applications.filter(pk=application_id)
        if not applications.exists():
            raise serializers.ValidationError({
                'application_key': 'Application does not belong to this work order.'
            })
        if step_key:
            try:
                step_id = uuid.UUID(str(step_key))
            except (AttributeError, TypeError, ValueError) as exc:
                raise serializers.ValidationError({
                    'step_key': 'Step identifier is invalid.'
                }) from exc
            if not WorkOrderStepExecution.objects.filter(
                application__in=applications, step_key=step_id
            ).exists():
                raise serializers.ValidationError({
                    'step_key': 'Step does not belong to this procedure application.'
                })
        return attrs


class BaseProcedureExecutionCommandSerializer(serializers.Serializer):
    """Common concurrency and idempotency fields for execution commands."""

    expected_version = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.CharField(max_length=128, allow_blank=False)


class ApplyProcedureCommandSerializer(BaseProcedureExecutionCommandSerializer):
    """Intent to apply one exact governed revision."""

    revision_id = serializers.IntegerField(min_value=1)


class CompleteStepCommandSerializer(BaseProcedureExecutionCommandSerializer):
    """Intent to record a validated step result."""

    value = serializers.JSONField(required=False, allow_null=True, default=None)
    passed = serializers.BooleanField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default='')
    evidence_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        default=list,
    )


class NotApplicableStepCommandSerializer(BaseProcedureExecutionCommandSerializer):
    """Intent to record an explicit not-applicable disposition."""

    reason = serializers.CharField(allow_blank=False)
    evidence_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        default=list,
    )


class ReopenStepCommandSerializer(BaseProcedureExecutionCommandSerializer):
    """Intent to reopen a terminal step for correction or rework."""

    reason = serializers.CharField(required=False, allow_blank=True, default='')
