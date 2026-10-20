"""Work-scoped investigation reads and explicit authenticated commands."""

from django.shortcuts import get_object_or_404

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.permissions
from tasks.investigation_models import InvestigationRequest
from tasks.investigation_serializers import InvestigationRequestSerializer
from tasks.scope import scoped_work_orders
from tasks.services.investigation_requests import (
    command,
    project_requests,
    request_queryset,
)
from tasks.services.work_orders import WorkOrderCommandError


class WorkInvestigationRequests(APIView):
    """Keep request ids inside the same native Work authorization boundary."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    rolemap = {'POST': 'change'}

    def get(self, request, pk, request_id=None):
        """Unknown or foreign Work/request ids return the same 404."""
        work = get_object_or_404(scoped_work_orders(request.user), pk=pk)
        if request_id is not None:
            row = get_object_or_404(request_queryset(), pk=request_id, work_order=work)
            return Response(InvestigationRequestSerializer(row).data)
        return Response(project_requests(work))

    def post(self, request, pk, request_id=None):
        """Write an exact versioned intent; no draft is treated as saved."""
        work = get_object_or_404(scoped_work_orders(request.user), pk=pk)
        if request_id is not None:
            get_object_or_404(InvestigationRequest, pk=request_id, work_order=work)
        data = request.data
        if not isinstance(data, dict) or set(data) - {
            'operation',
            'fields',
            'expected_version',
            'request_version',
            'idempotency_key',
        }:
            raise ValidationError('Unsupported investigation command fields.')
        try:
            result = command(
                work_order_id=work.pk,
                actor=request.user,
                expected_version=data.get('expected_version'),
                idempotency_key=data.get('idempotency_key'),
                operation=data.get('operation'),
                fields=data.get('fields'),
                request_id=request_id,
                request_version=data.get('request_version'),
            )
        except WorkOrderCommandError as exc:
            return Response(
                {
                    'code': getattr(exc, 'code', 'INVESTIGATION_INVALID'),
                    'detail': str(exc),
                },
                status=409,
            )
        return Response(result)
