"""Typed request/evidence boundaries for the investigation command service."""

from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

from rest_framework import serializers

from tasks.investigation_models import InvestigationArtifact, InvestigationRequest

KINDS = InvestigationArtifact.Kind.values
STATES = InvestigationRequest.Status.values


class StrictFieldsSerializer(serializers.Serializer):
    """Reject unsupported fields instead of silently dropping authored intent."""

    def to_internal_value(self, data):
        """Reject unknown authored keys before DRF normalizes the known fields."""
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError('Unsupported investigation fields.')
        return super().to_internal_value(data)


class RequiredArtifactSerializer(StrictFieldsSerializer):
    """A required type and its recorded conditions, never inferred requirements."""

    kind = serializers.ChoiceField(choices=KINDS)
    description = serializers.CharField()
    unit = serializers.CharField(required=False, allow_blank=True)
    conditions = serializers.CharField(required=False, allow_blank=True)


class InvestigationRequestSerializer(serializers.ModelSerializer):
    """Canonical identity and attribution visible to an authorized Work reader."""

    owner_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    artifacts = serializers.SerializerMethodField()

    class Meta:
        """Only canonical fields; no private thread contents or receipt."""

        model = InvestigationRequest
        fields = '__all__'

    def get_owner_name(self, obj):
        """Use the same person label as other Work projections."""
        from tasks.work_execution.common import person

        return person(obj.owner)

    def get_created_by_name(self, obj):
        """Name the recorded creator using the native person label."""
        from tasks.work_execution.common import person

        return person(obj.created_by)

    def get_updated_by_name(self, obj):
        """Display the recorded updater without treating a user id as a name."""
        from tasks.work_execution.common import person

        return person(obj.updated_by)

    def get_artifacts(self, obj):
        """Saved evidence retains its own ids and attribution."""
        return InvestigationArtifactSerializer(
            sorted(
                obj.artifacts.all(),
                key=lambda artifact: (artifact.created_at, artifact.pk),
            ),
            many=True,
        ).data


class InvestigationRequestRevisionSerializer(InvestigationRequestSerializer):
    """Retain native P43 comparisons without copying the growing evidence history."""

    class Meta(InvestigationRequestSerializer.Meta):
        """Only the request facts consumed by the native scope revision projection."""

        fields = (
            'id',
            'work_order',
            'version',
            'purpose',
            'next_check',
            'status',
            'owner_name',
            'next_checkpoint',
            'required_artifacts',
            'blocked_reason',
            'source_finding',
            'source_proposal',
            'resolution',
            'priority',
        )


class InvestigationArtifactSerializer(serializers.ModelSerializer):
    """Append-only response; attachment is an id in the existing Work store."""

    originals = serializers.SerializerMethodField()

    def get_originals(self, obj):
        """Immutable copies survive changes to the native Work attachment."""
        from tasks.services.capture_originals import original_summary

        return [original_summary(row) for row in obj.originals.all()]

    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        """Return exact payload, including compound laboratory provenance."""

        model = InvestigationArtifact
        fields = '__all__'

    def get_recorded_by_name(self, obj):
        """Retain the actual recorder rather than a model attribution."""
        from tasks.work_execution.common import person

        return person(obj.recorded_by)


class RequestFieldsSerializer(StrictFieldsSerializer):
    """Validated authored facts; no unknown may default to a completed state."""

    purpose = serializers.CharField()
    next_check = serializers.CharField()
    priority = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    required_artifacts = RequiredArtifactSerializer(many=True)
    status = serializers.ChoiceField(choices=STATES, required=False)
    owner_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    next_checkpoint = serializers.DateTimeField(allow_null=True, required=False)
    source_finding_id = serializers.IntegerField(
        min_value=1, allow_null=True, required=False
    )
    source_proposal = serializers.JSONField(allow_null=True, required=False)
    blocked_reason = serializers.CharField(allow_blank=True, required=False)
    resolution = serializers.CharField(allow_blank=True, required=False)

    def validate_required_artifacts(self, value):
        """Nested replacements are complete even when the request is partial."""
        validator = RequiredArtifactSerializer(data=value, many=True)
        validator.is_valid(raise_exception=True)
        return validator.validated_data


class ArtifactFieldsSerializer(StrictFieldsSerializer):
    """Typed artifact content; nested measurement sets are validated below."""

    kind = serializers.ChoiceField(choices=KINDS)
    payload = serializers.JSONField()
    attachment_id = serializers.IntegerField(
        min_value=1, allow_null=True, required=False
    )
    supersedes_id = serializers.IntegerField(
        min_value=1, allow_null=True, required=False
    )

    def validate(self, attrs):
        """Lab results keep their own provenance and cannot be a process snapshot."""
        payload = attrs['payload']
        if not isinstance(payload, dict):
            raise serializers.ValidationError('Artifact payload must be an object.')
        if 'recording_started_at' in payload:
            serializers.DateTimeField().run_validation(payload['recording_started_at'])
            raw = parse_datetime(str(payload['recording_started_at']))
            if raw is None or not is_aware(raw):
                raise serializers.ValidationError(
                    'Recording start time must include a timezone.'
                )
        if 'recording_duration_ms' in payload:
            value = payload['recording_duration_ms']
            if type(value) is not int or value < 0:
                raise serializers.ValidationError(
                    'Recording duration must be nonnegative milliseconds.'
                )
        required = {
            'observation': ('text',),
            'reading_set': ('readings', 'observed_at', 'conditions'),
            'photo': ('description',),
            'report': ('description', 'source', 'status'),
            'laboratory': (
                'sample',
                'method',
                'observed_at',
                'source',
                'status',
                'results',
            ),
            'check_result': ('text', 'status'),
        }[attrs['kind']]
        if any(
            key not in payload or payload[key] in (None, '', []) for key in required
        ):
            raise serializers.ValidationError(
                'Missing typed artifact facts: ' + ', '.join(required)
            )
        for key in required:
            if key not in ('readings', 'results') and (
                not isinstance(payload[key], str) or not payload[key].strip()
            ):
                raise serializers.ValidationError(f'{key} must be recorded text.')
        if (
            attrs['kind'] in ('photo', 'report')
            and not attrs.get('attachment_id')
            and not self.context.get('pending_chat_file')
        ):
            raise serializers.ValidationError(
                'Photo/report requires a saved Work attachment.'
            )
        if attrs['kind'] in ('reading_set', 'laboratory'):
            key = 'readings' if attrs['kind'] == 'reading_set' else 'results'
            rows = payload[key]
            if not isinstance(rows, list) or not all(
                isinstance(row, dict)
                and all(k in row for k in ('name', 'value', 'unit'))
                for row in rows
            ):
                raise serializers.ValidationError(
                    'Each reading needs name, value and unit.'
                )
        if attrs['kind'] in ('reading_set', 'laboratory'):
            import math

            key = 'readings' if attrs['kind'] == 'reading_set' else 'results'
            for row in payload[key]:
                value = row['value']
                if (
                    not isinstance(row['name'], str)
                    or not row['name'].strip()
                    or not isinstance(row['unit'], str)
                ):
                    raise serializers.ValidationError(
                        'Reading name and unit must be text.'
                    )
                if (
                    isinstance(value, bool)
                    or (value is not None and not isinstance(value, (int, float, str)))
                    or (isinstance(value, float) and not math.isfinite(value))
                ):
                    raise serializers.ValidationError(
                        'A reading must be a finite value, result text or explicitly missing.'
                    )
        if 'observed_at' in payload:
            serializers.DateTimeField().run_validation(payload['observed_at'])
        if attrs['kind'] in ('laboratory', 'report') and payload['status'] not in (
            'pending',
            'preliminary',
            'final',
            'unavailable',
            'disputed',
        ):
            raise serializers.ValidationError('Unknown result status.')
        if attrs['kind'] == 'check_result' and payload['status'] not in (
            'performed',
            'cannot_perform',
            'pending',
            'disputed',
        ):
            raise serializers.ValidationError('Unknown check outcome status.')
        return attrs
