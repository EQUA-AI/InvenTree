"""Overview serializer fields for the R6 S4 Work execution projections.

Kept apart from ``WorkOrderOverviewSerializer`` so each projection lives in
its own module and the overview stays one read for the Work page.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

WORK_EXECUTION_FIELDS = ('handled_actions', 'investigation')


class WorkExecutionFieldsMixin(serializers.Serializer):
    """Read-only projections the seven Work panels render from the overview."""

    handled_actions = serializers.SerializerMethodField()
    investigation = serializers.SerializerMethodField()

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
