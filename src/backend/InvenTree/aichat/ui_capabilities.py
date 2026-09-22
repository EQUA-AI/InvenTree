"""Small authenticated contract for optional frontend surfaces."""

from django.conf import settings

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from InvenTree.version import inventreeCommitHash


class UICapabilitiesView(APIView):
    """Advertise supported reads; every data endpoint still authorizes its actor."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: dict, 401: dict, 403: dict})
    def get(self, request):
        """Return contract versions and deployment availability, never grants."""
        response = Response({
            'version': 1,
            'backend_commit': inventreeCommitHash(),
            'maintenance_metrics': 1,
            'proposals': {'version': 1, 'session_required': True},
            'risk_radar': bool(getattr(settings, 'AIMMS_RISK_RADAR_ENABLED', False)),
            'command_center': bool(
                getattr(settings, 'AIMMS_RISK_RADAR_ENABLED', False)
                and getattr(settings, 'AIMMS_COMMAND_CENTER_ENABLED', False)
            ),
        })
        response['Cache-Control'] = 'private, no-store'
        return response
