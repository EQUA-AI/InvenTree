"""Overview serializer fields for the R6 S4 Work execution projections.

Kept apart from ``WorkOrderOverviewSerializer`` so each projection lives in
its own module and the overview stays one read for the Work page.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

WORK_EXECUTION_FIELDS = (
    'handled_actions',
    'investigation',
    'prepared_package',
    'verification_return',
    'time_assignment',
    'work_continuity',
    'operational_authority',
)


class WorkExecutionFieldsMixin(serializers.Serializer):
    """Read-only projections the seven Work panels render from the overview."""

    def to_representation(self, instance):
        """One source snapshot for displayed facts and their recording context."""
        from tasks.services.operational_authority import (
            capture_read_sources,
            clear_read_sources,
        )

        capture_read_sources(instance)
        try:
            return super().to_representation(instance)
        finally:
            clear_read_sources(instance)

    operational_authority = serializers.SerializerMethodField()

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_operational_authority(self, obj) -> dict:
        """O15: recorded authority and role-gated commands, never lifecycle inference."""
        from tasks.services.operational_authority import project

        request = self.context.get('request')
        return project(obj, request.user if request else None)

    handled_actions = serializers.SerializerMethodField()
    investigation = serializers.SerializerMethodField()
    prepared_package = serializers.SerializerMethodField()
    verification_return = serializers.SerializerMethodField()
    time_assignment = serializers.SerializerMethodField()
    work_continuity = serializers.SerializerMethodField()

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_work_continuity(self, obj) -> dict:
        """P44: attributed holds and existing standalone record commands."""
        from .continuity import project

        request = self.context.get('request')
        return project(obj, actor=request.user if request else None)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_time_assignment(self, obj) -> dict:
        """P27: native assignment provenance, without inferred labor."""
        from .time_assignment import project

        return project(obj)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_verification_return(self, obj) -> dict:
        """P23: recorded test results without inferred operating authority."""
        from .verification import project

        request = self.context.get('request')
        return project(obj, actor=request.user if request else None)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_prepared_package(self, obj) -> dict:
        """P21: immutable applied instructions and recorded scope history."""
        from .package import project

        request = self.context.get('request')
        return project(obj, actor=request.user if request else None)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_handled_actions(self, obj) -> dict:
        """P18: recorded digital work AIMMS did for this work order."""
        from .handled import project

        return project(obj)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_investigation(self, obj) -> dict:
        """P20: the saved investigation Diagnose projects (no confidence score)."""
        from .investigation import project

        return project(obj)
