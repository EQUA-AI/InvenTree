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
    'routine_work',
    'recurrence_policy',
    'routine_origin',
)


class WorkExecutionFieldsMixin(serializers.Serializer):
    """Read-only projections the seven Work panels render from the overview."""

    handled_actions = serializers.SerializerMethodField()
    investigation = serializers.SerializerMethodField()
    prepared_package = serializers.SerializerMethodField()
    verification_return = serializers.SerializerMethodField()
    time_assignment = serializers.SerializerMethodField()
    work_continuity = serializers.SerializerMethodField()
    routine_work = serializers.SerializerMethodField()
    recurrence_policy = serializers.SerializerMethodField()
    routine_origin = serializers.SerializerMethodField()

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_routine_origin(self, obj) -> dict | None:
        """O21: only disclose the source routine Work to its authorized reader."""
        from tasks.services.routine_findings import origin

        request = self.context.get('request')
        return origin(obj, request.user if request else None)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_routine_work(self, obj) -> dict | None:
        """P51: existing type-specific tasks and reading records."""
        from .routine import project

        request = self.context.get('request')
        return project(obj, actor=request.user if request else None)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_recurrence_policy(self, obj) -> dict:
        """P50: read-only policy candidates; creation is an explicit command."""
        from tasks.services.recurrence import project

        request = self.context.get('request')
        return project(obj, request.user if request else None)

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

        return project(obj)

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
