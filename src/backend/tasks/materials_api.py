"""R6 P22: read-only material readout for one scoped work order."""

from django.shortcuts import get_object_or_404

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from InvenTree.permissions import RolePermission

from .materials_readout import material_readout
from .workorder_api import WorkOrderEnabledMixin, _work_order_queryset


class WorkOrderMaterials(WorkOrderEnabledMixin, APIView):
    """Requirements, reservations, custody and recorded use; never a command."""

    permission_classes = [IsAuthenticated, RolePermission]
    role_required = 'work_order'

    def get(self, request, pk):
        """Resolve the job in the reader's scope (404 outside it), then read."""
        work_order = get_object_or_404(_work_order_queryset(request.user), pk=pk)
        response = Response(material_readout(request.user, work_order))
        response['Cache-Control'] = 'no-store'
        return response
