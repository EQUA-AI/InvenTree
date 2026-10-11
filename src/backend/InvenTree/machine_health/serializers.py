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
from InvenTree.serializers import (
    InvenTreeIsoDateTimeField,
    InvenTreeIsoDateTimeModelSerializerMixin,
)
from machine_health.services.display_time import display_shift, to_display


class MachineSignalSerializer(serializers.Serializer):
    """One mapped signal with its current value, freshness and limits."""

    binding_id = serializers.IntegerField(read_only=True)
    source_id = serializers.IntegerField(read_only=True)
    source_name = serializers.CharField(read_only=True)
    source_type = serializers.CharField(read_only=True)
    external_key = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    signal_kind = serializers.CharField(read_only=True)
    unit = serializers.CharField(read_only=True)
    value = serializers.JSONField(read_only=True)
    observed_at = InvenTreeIsoDateTimeField(read_only=True, allow_null=True)
    received_at = InvenTreeIsoDateTimeField(read_only=True, allow_null=True)
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
    last_success_at = InvenTreeIsoDateTimeField(read_only=True, allow_null=True)
    last_error_at = InvenTreeIsoDateTimeField(read_only=True, allow_null=True)
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
    last_observed_at = InvenTreeIsoDateTimeField(read_only=True, allow_null=True)
    anomaly_counts = serializers.JSONField(read_only=True)
    active_anomaly_count = serializers.IntegerField(read_only=True)
    sources = HealthSourceStatusSerializer(many=True, read_only=True)


class MachineAnomalySerializer(
    InvenTreeIsoDateTimeModelSerializerMixin, serializers.ModelSerializer
):
    """An anomaly as the Health blade renders it.

    Observation times are moved into the clock the rest of the blade shows. A
    station whose history is a recorded window presents it shifted forward - the
    signal row that produced an anomaly says "5 s ago" - so serving the stored
    plant instant here would date the anomaly card 442 days before the reading it
    cites, on the same screen. The stored value stays on the plant's own clock,
    which is what keeps the migration checkable against the source; only the
    presentation moves, exactly as the trend, series and range endpoints do.

    Acknowledgement, resolution and row times are *not* shifted: a person
    acknowledged an anomaly at a real moment on the server's clock, and moving
    that would misreport when somebody acted.
    """

    source_name = serializers.CharField(
        source='source.name', read_only=True, default=None
    )
    source_type = serializers.CharField(
        source='source.source_type', read_only=True, default=None
    )
    signals = serializers.SerializerMethodField()
    acknowledged_by_name = serializers.SerializerMethodField()
    first_observed_at = serializers.SerializerMethodField()
    last_observed_at = serializers.SerializerMethodField()
    display_shifted = serializers.SerializerMethodField()
    display_shift_seconds = serializers.SerializerMethodField()

    def _shift(self, anomaly):
        """The station's presentation offset, resolved once per anomaly."""
        cache = self.context.setdefault('_anomaly_shifts', {})
        key = (anomaly.machine_id, anomaly.source_id)
        if key not in cache:
            machine = anomaly.machine
            station = machine if machine.asset_type == 'pumphouse' else machine.parent
            cache[key] = display_shift(station, anomaly.source)
        return cache[key]

    def get_first_observed_at(self, anomaly):
        """When the condition was first seen, in the clock the blade shows."""
        moment = to_display(anomaly.first_observed_at, self._shift(anomaly))
        return moment.isoformat() if moment is not None else None

    def get_last_observed_at(self, anomaly):
        """When the condition was last seen, in the clock the blade shows."""
        moment = to_display(anomaly.last_observed_at, self._shift(anomaly))
        return moment.isoformat() if moment is not None else None

    def get_display_shifted(self, anomaly) -> bool:
        """Whether the two observation times above were moved."""
        return bool(self._shift(anomaly))

    def get_display_shift_seconds(self, anomaly) -> int:
        """By how much, so a client can recover the plant's own instant."""
        return int(self._shift(anomaly).total_seconds())

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
            'display_shifted',
            'display_shift_seconds',
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


class HealthEvidenceSnapshotSerializer(
    InvenTreeIsoDateTimeModelSerializerMixin, serializers.ModelSerializer
):
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


class MachineSignalBindingSerializer(
    InvenTreeIsoDateTimeModelSerializerMixin, serializers.ModelSerializer
):
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
