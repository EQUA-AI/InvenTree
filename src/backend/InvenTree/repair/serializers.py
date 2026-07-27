"""Serializers for the Repair Packet application."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from tasks.serializers import KanbanCardPartSerializer

from .models import (
    LockoutPoint,
    RepairPacket,
    RepairPacketEvent,
    RepairPacketEvidence,
    RepairPacketGate,
    RepairPacketGenerationRun,
    SafetyEvidenceProof,
    SafetyGateTemplate,
)


class SafetyGateTemplateSerializer(serializers.ModelSerializer):
    """Serializer for reusable safety gate templates."""

    class Meta:
        """Serializer metadata."""

        model = SafetyGateTemplate
        fields = (
            'pk',
            'name',
            'gate_type',
            'instructions',
            'applies_to',
            'required_permission',
            'requires_photo',
            'requires_second_person',
            'is_blocking',
            'is_mandatory',
            'risk_tier',
            'default_sequence',
            'active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('pk', 'created_at', 'updated_at')


class LockoutPointSerializer(serializers.ModelSerializer):
    """Serializer for LOTO energy-control points."""

    class Meta:
        """Serializer metadata."""

        model = LockoutPoint
        fields = (
            'pk',
            'gate',
            'energy_source',
            'isolation_device',
            'lock_id',
            'tag_id',
            'status',
            'applied_by',
            'verified_by',
            'verified_at',
            'restored_at',
            'note',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'pk',
            'gate',
            'applied_by',
            'verified_by',
            'verified_at',
            'restored_at',
            'created_at',
            'updated_at',
        )


class SafetyEvidenceProofSerializer(serializers.ModelSerializer):
    """Serializer for structured gate proof (photo/scan/reading/etc.)."""

    class Meta:
        """Serializer metadata."""

        model = SafetyEvidenceProof
        fields = (
            'pk',
            'gate',
            'lockout_point',
            'proof_type',
            'value',
            'captured_by',
            'captured_at',
        )
        read_only_fields = ('pk', 'gate', 'captured_by', 'captured_at')


class RepairPacketGateSerializer(serializers.ModelSerializer):
    """Serializer for safety gates attached to a packet."""

    lockout_points = LockoutPointSerializer(many=True, read_only=True)
    proofs = SafetyEvidenceProofSerializer(many=True, read_only=True)
    unsatisfied_reason = serializers.SerializerMethodField()

    class Meta:
        """Serializer metadata."""

        model = RepairPacketGate
        fields = (
            'pk',
            'template',
            'sequence',
            'is_blocking',
            'is_mandatory',
            'name',
            'gate_type',
            'status',
            'requires_photo',
            'required_permission',
            'requires_second_person',
            'confirmed_by',
            'confirmed_at',
            'verified_by',
            'verified_at',
            'waived_by',
            'waived_at',
            'waiver_reason',
            'waiver_authority',
            'note',
            'lockout_points',
            'proofs',
            'unsatisfied_reason',
            'created_at',
        )
        read_only_fields = (
            'pk',
            'confirmed_by',
            'confirmed_at',
            'verified_by',
            'verified_at',
            'waived_by',
            'waived_at',
            'created_at',
        )

    def get_unsatisfied_reason(self, obj) -> str:
        """Return the current blocking reason for UI/tooltips."""
        return obj.unsatisfied_reason()


class RepairPacketEvidenceSerializer(serializers.ModelSerializer):
    """Serializer for evidence items attached to a packet."""

    class Meta:
        """Serializer metadata."""

        model = RepairPacketEvidence
        fields = ('pk', 'kind', 'label', 'value', 'created_at')
        read_only_fields = ('pk', 'created_at')


class RepairPacketEventSerializer(serializers.ModelSerializer):
    """Read-only serializer for the packet audit timeline."""

    class Meta:
        """Serializer metadata."""

        model = RepairPacketEvent
        fields = (
            'pk',
            'event_type',
            'from_status',
            'to_status',
            'actor',
            'reason',
            'metadata',
            'created_at',
        )
        read_only_fields = fields


class RepairPacketGenerationRunSerializer(serializers.ModelSerializer):
    """Read-only serializer for generation provenance runs."""

    class Meta:
        """Serializer metadata."""

        model = RepairPacketGenerationRun
        fields = (
            'pk',
            'agent_run_id',
            'provider',
            'status',
            'started_at',
            'finished_at',
            'error',
            'result_summary',
        )
        read_only_fields = fields


class RepairPacketSerializer(serializers.ModelSerializer):
    """Serializer for RepairPacket instances with nested read-only sections."""

    machine_name = serializers.CharField(
        source='machine.name', read_only=True, default=''
    )
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    parts = serializers.SerializerMethodField()
    gates = RepairPacketGateSerializer(many=True, read_only=True)
    evidence = RepairPacketEvidenceSerializer(many=True, read_only=True)
    events = RepairPacketEventSerializer(many=True, read_only=True)
    approvals = serializers.SerializerMethodField()
    latest_generation_run = serializers.SerializerMethodField()
    unsatisfied_safety_gates = serializers.SerializerMethodField()
    work_order_reference = serializers.CharField(
        source='work_order.reference', read_only=True, default=None
    )
    # Finalization is version-checked against the work order, so the client needs
    # the token it must echo back to /close/.
    work_order_lifecycle_version = serializers.IntegerField(
        source='work_order.lifecycle_version', read_only=True, default=None
    )

    class Meta:
        """Serializer metadata."""

        model = RepairPacket
        fields = (
            'pk',
            'reference',
            'status',
            'status_label',
            'machine',
            'machine_name',
            'fault_summary',
            'symptom',
            'criticality',
            'production_impact',
            'diagnosis',
            'diagnosis_schema_version',
            'generation_status',
            'work_order',
            'work_order_reference',
            'work_order_lifecycle_version',
            'parts',
            'gates',
            'unsatisfied_safety_gates',
            'evidence',
            'events',
            'approvals',
            'latest_generation_run',
            'closeout',
            'agent_run_id',
            'created_by',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'pk',
            'reference',
            'status',
            'status_label',
            'diagnosis',
            'diagnosis_schema_version',
            'generation_status',
            'agent_run_id',
            'created_by',
            'created_at',
            'updated_at',
        )

    def get_parts(self, obj) -> list:
        """Return the parts path via the linked work order's card parts."""
        if not obj.work_order_id:
            return []
        return KanbanCardPartSerializer(obj.work_order.card_parts.all(), many=True).data

    def get_approvals(self, obj) -> list:
        """Return a compact view of approvals linked to this packet."""
        return [
            {
                'pk': str(link.approval_id),
                'purpose': link.purpose,
                'status': link.approval.status,
            }
            for link in obj.approval_links.select_related('approval').all()
        ]

    @extend_schema_field(RepairPacketGenerationRunSerializer(allow_null=True))
    def get_latest_generation_run(self, obj):
        """Return the most recent generation run (provenance) if any."""
        run = obj.generation_runs.first()
        if run is None:
            return None
        return RepairPacketGenerationRunSerializer(run).data

    def get_unsatisfied_safety_gates(self, obj) -> list:
        """Return safety gate blockers in a compact UI-friendly shape."""
        return [
            {'pk': gate.pk, 'name': gate.name, 'reason': reason}
            for gate, reason in obj.unsatisfied_blocking_gates()
        ]
