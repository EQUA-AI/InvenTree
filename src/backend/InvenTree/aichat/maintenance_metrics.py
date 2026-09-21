"""HTTP adapter for deterministic maintenance dashboard metrics."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai.core.integrations.retrieval_authority import fresh_actor, fresh_role


class MaintenanceMetricsView(APIView):
    """Expose only current-role, current-scope dashboard calculations."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(name, str, required=name == 'metric')
            for name in (
                'metric',
                'period',
                'from',
                'to',
                'horizon',
                'machine',
                'client',
                'assigned_to',
                'mine',
                'priority',
                'type',
                'criticality',
                'group',
                'offset',
            )
        ],
        responses={200: dict, 400: dict, 403: dict},
    )
    def get(self, request):
        """Read counts and contributors from one shared definition."""
        from tasks.dashboard_metrics import MetricOptions, dashboard_metric
        from tasks.scope import ScopeError

        actor = fresh_actor(request.user)
        if actor is None or not fresh_role(actor, 'work_order'):
            return Response(status=403)
        try:
            options = MetricOptions(request.query_params)
        except (ValueError, TypeError, OverflowError):
            return Response({'detail': 'Invalid metric filters'}, status=400)
        try:
            result = dashboard_metric(actor, options)
        except ScopeError:
            result = {
                'id': options.metric,
                'version': 1,
                'state': 'unavailable',
                'complete': False,
            }
        response = Response(result)
        response['Cache-Control'] = 'private, no-store'
        return response
