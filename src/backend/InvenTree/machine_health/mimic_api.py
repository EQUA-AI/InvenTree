"""Authorized, read-only station mimic endpoint."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from assets.registry_api import LiveSourceSerializer, RegistryAPI
from machine_health.services.mimic import station_mimic


class MimicPointSerializer(serializers.Serializer):
    """A current scalar or explicit unknown with server-computed age."""

    pointer = serializers.CharField()
    label = serializers.CharField()
    group = serializers.CharField(allow_blank=True)
    value = serializers.JSONField(allow_null=True)
    unit = serializers.CharField(allow_blank=True)
    quality = serializers.CharField()
    observed_at = serializers.DateTimeField(allow_null=True)
    age_seconds = serializers.FloatField(allow_null=True)
    reason = serializers.CharField(allow_null=True)
    condition = serializers.CharField()
    thresholds_configured = serializers.BooleanField()


class MimicSerializer(serializers.Serializer):
    """One response for the overview and selected unit, keyed by exact pointers."""

    station = serializers.IntegerField()
    name = serializers.CharField()
    generated_at = serializers.DateTimeField()
    source = LiveSourceSerializer(allow_null=True)
    last_poll_at = serializers.DateTimeField(allow_null=True)
    last_error_code = serializers.CharField(allow_blank=True)
    enabled = serializers.BooleanField()
    layout = serializers.JSONField()
    station_points = serializers.DictField(child=MimicPointSerializer())
    bays = serializers.ListField(child=serializers.JSONField())
    selected_unit = serializers.CharField(allow_null=True)
    points = serializers.DictField(child=MimicPointSerializer())
    totals = serializers.DictField(child=serializers.JSONField())
    alarms = serializers.ListField(child=serializers.JSONField())
    unconfigured_thresholds = serializers.IntegerField()


class StationMimic(RegistryAPI):
    """Reuse exact Client and work-order view authorization from station registration."""

    @extend_schema(
        responses=MimicSerializer,
        parameters=[OpenApiParameter('unit', str, required=False)],
    )
    def get(self, request, pk):
        """Authorize the station before resolving its pumps or telemetry."""
        station = self.machine()
        if station.asset_type != 'pumphouse':
            raise ValidationError('Mimics belong to pump stations.')
        result = station_mimic(station, unit=request.query_params.get('unit'))
        return Response(MimicSerializer(result).data)
