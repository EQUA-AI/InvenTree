"""Strict explicit calibration intents; no instrument or certificate inference."""

from rest_framework import serializers
from rest_framework.settings import api_settings


class CalibrationCommandSerializer(serializers.Serializer):
    """A full replacement revision with optimistic Work and reading versions."""

    operation = serializers.ChoiceField(choices=['record', 'correct'])
    expected_version = serializers.IntegerField(min_value=1)
    expected_revision = serializers.IntegerField(min_value=0)
    idempotency_key = serializers.CharField(max_length=128)
    reference_instrument_id = serializers.CharField(max_length=200)
    certificate_number = serializers.CharField(max_length=200)
    issuing_lab = serializers.CharField(max_length=200)
    valid_until = serializers.DateField(allow_null=True)
    certificate_id = serializers.IntegerField(min_value=1)
    correction_reason = serializers.CharField(
        required=False, allow_blank=True, max_length=4000
    )

    def to_internal_value(self, data):
        """Reject typos, invented fields and boolean/coerced version tokens."""
        # A top-level serializer needs a mapping error: DRF calls this outside
        # the try block that would otherwise wrap a bare message for ``errors``.
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({
                api_settings.NON_FIELD_ERRORS_KEY: [
                    'Unsupported calibration command fields.'
                ]
            })
        for key in ('expected_version', 'expected_revision', 'certificate_id'):
            if type(data.get(key)) is not int:
                raise serializers.ValidationError({key: 'An integer is required.'})
        return super().to_internal_value(data)
