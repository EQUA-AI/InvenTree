"""Serializers for the machine health API.

Everything here is read-only except acknowledgement. Health state is written by
connectors and by the deterministic detectors, never by a browser: an operator
must not be able to type a machine back to normal.
"""

from rest_framework import serializers

from assets.health_models import (
    HealthEvidenceSnapshot,
    MachineAnomaly,
    MachineSignalBinding,
)


class MachineSignalSerializer(serializers.Serializer):
    """One mapped signal with its current value, freshness and limits."""

    binding_id = serializers.IntegerField(read_only=True)
    source_id = serializers.IntegerField(read_only=True)
    source_name = serializers.CharField(read_only=True)
    source_type = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    signal_kind = serializers.CharField(read_only=True)
    unit = serializers.CharField(read_only=True)
    value = serializers.JSONField(read_only=True)
    observed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    received_at = serializers.DateTimeField(read_only=True, allow_null=True)
    quality = serializers.CharField(read_only=True)
    stale = serializers.BooleanField(read_only=True)
    freshness_threshold_seconds = serializers.IntegerField(read_only=True)
    state = serializers.CharField(read_only=True)
    limits = serializers.JSONField(read_only=True)


class HealthSourceStatusSerializer(serializers.Serializer):
    """Connection health for one source mapped to a machine."""

    source_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    source_type = serializers.CharField(read_only=True)
    active = serializers.BooleanField(read_only=True)
    healthy = serializers.BooleanField(read_only=True)
    last_success_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_error_at = serializers.DateTimeField(read_only=True, allow_null=True)
    # Redacted classification only; connector messages never reach a client.
    last_error_code = serializers.CharField(read_only=True)
    freshness_threshold_seconds = serializers.IntegerField(read_only=True)
    mapped_tag_count = serializers.IntegerField(read_only=True)


class MachineHealthSummarySerializer(serializers.Serializer):
    """Current condition for one machine."""

    state = serializers.CharField(read_only=True)
    configured = serializers.BooleanField(read_only=True)
    signal_count = serializers.IntegerField(read_only=True)
    stale_signal_count = serializers.IntegerField(read_only=True)
    degraded_data = serializers.BooleanField(read_only=True)
    last_observed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    anomaly_counts = serializers.JSONField(read_only=True)
    active_anomaly_count = serializers.IntegerField(read_only=True)
    sources = HealthSourceStatusSerializer(many=True, read_only=True)


class MachineAnomalySerializer(serializers.ModelSerializer):
    """An anomaly as the Health blade renders it."""

    source_name = serializers.CharField(
        source='source.name', read_only=True, default=None
    )
    source_type = serializers.CharField(
        source='source.source_type', read_only=True, default=None
    )
    signals = serializers.SerializerMethodField()
    acknowledged_by_name = serializers.SerializerMethodField()

    class Meta:
        """Serializer metadata."""

        model = MachineAnomaly
        fields = (
            'pk',
            'machine',
            'source',
            'source_name',
            'source_type',
            'external_id',
            'alarm_code',
            'fingerprint',
            'severity',
            'status',
            'title',
            'evidence_summary',
            'metrics',
            'detector',
            'detector_version',
            'signals',
            'first_observed_at',
            'last_observed_at',
            'acknowledged_at',
            'acknowledged_by_name',
            'acknowledgement_note',
            'resolved_at',
            'resolution_note',
            'work_order',
            'repair_packet',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    def get_signals(self, anomaly) -> list:
        """Return the implicated signals, enough to cite them in the UI."""
        return [
            {
                'binding_id': binding.pk,
                'display_name': binding.display_name,
                'unit': binding.unit,
                'signal_kind': binding.signal_kind,
            }
            for binding in anomaly.bindings.all()
        ]

    def get_acknowledged_by_name(self, anomaly) -> str | None:
        """Return who acknowledged the anomaly, if anyone has."""
        actor = anomaly.acknowledged_by
        if actor is None:
            return None
        return actor.get_full_name() or actor.get_username()


class HealthEvidenceSnapshotSerializer(serializers.ModelSerializer):
    """An immutable evidence snapshot, as cited by a preliminary result."""

    class Meta:
        """Serializer metadata."""

        model = HealthEvidenceSnapshot
        fields = (
            'id',
            'machine',
            'anomaly',
            'source',
            'binding',
            'signal_label',
            'unit',
            'window_start',
            'window_end',
            'captured_at',
            'samples',
            'statistics',
            'quality',
            'stale',
            'reason',
            'source_references',
            'content_hash',
            'system_actor',
        )
        read_only_fields = fields


class MachineSignalBindingSerializer(serializers.ModelSerializer):
    """Administrative view of a tag mapping."""

    source_name = serializers.CharField(source='source.name', read_only=True)

    class Meta:
        """Serializer metadata."""

        model = MachineSignalBinding
        fields = (
            'pk',
            'machine',
            'source',
            'source_name',
            'external_key',
            'display_name',
            'signal_kind',
            'unit',
            'normal_min',
            'normal_max',
            'warn_min',
            'warn_max',
            'critical_min',
            'critical_max',
            'transform',
            'active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('pk', 'created_at', 'updated_at')
