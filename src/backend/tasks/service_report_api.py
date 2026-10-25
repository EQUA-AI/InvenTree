"""Native Closeout service-report review and post-completion commands."""

from dataclasses import asdict

from rest_framework import serializers
from rest_framework.response import Response

from InvenTree.permissions import InvenTreeTokenMatchesOASRequirements, map_scope
from tasks.closeout_api import CloseoutCommandView
from tasks.services.service_reports import report_state, service_report_command
from tasks.workorder_serializers import BaseCommandSerializer


class ServiceReportCommandSerializer(BaseCommandSerializer):
    """Never accept a provider acknowledgement or approval actor from the client."""

    action = serializers.ChoiceField(
        choices=['submit', 'approve', 'sap', 'return', 'end_test']
    )
    account = serializers.DictField(required=False)
    capture_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    report_id = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    confirmed = serializers.BooleanField(required=False, default=False)


class WorkServiceReport(CloseoutCommandView):
    """A retained report and commands under the existing scoped Work parent."""

    permission_classes = [
        *CloseoutCommandView.permission_classes,
        InvenTreeTokenMatchesOASRequirements,
    ]
    required_alternate_scopes = map_scope(roles=['work_order'])

    def get(self, request, pk):
        """Return retained content; a GET never creates a report."""
        response = Response(report_state(self.get_work_order(request, pk)))
        response['Cache-Control'] = 'private, no-store'
        return response

    def post(self, request, pk):
        """Delegate review, replay and return authority to the atomic service."""
        work_order = self.get_work_order(request, pk)
        serializer = ServiceReportCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = self.run(
            request, work_order, service_report_command, **serializer.validated_data
        )
        return result if isinstance(result, Response) else Response(asdict(result))
