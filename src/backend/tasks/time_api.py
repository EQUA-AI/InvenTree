"""Work-scoped time and downtime reads and commands."""

from django.shortcuts import get_object_or_404

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.permissions
from tasks.scope import scoped_work_orders
from tasks.services.work_orders import WorkOrderCommandError
from tasks.services.work_time import (
    command,
    downtime_projection,
    propose_downtime,
    time_projection,
)


class WorkTime(APIView):
    """Native Work access applies before looking up any recorded fact."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    rolemap = {'POST': 'change'}

    def get(self, request, pk):
        """Read server-observed time or explicitly request telemetry proposals."""
        work = get_object_or_404(scoped_work_orders(request.user), pk=pk)
        if request.query_params.get('propose') == 'true':
            from tasks.work_execution.common import record_instant

            start = (
                record_instant(request.query_params['from'])
                if request.query_params.get('from')
                else None
            )
            end = (
                record_instant(request.query_params['to'])
                if request.query_params.get('to')
                else None
            )
            return Response(propose_downtime(work, request.user, start, end))
        return Response({
            'version': work.lifecycle_version,
            'time': time_projection(work),
            'downtime': downtime_projection(work),
        })

    def post(self, request, pk):
        """An exact retry returns the original durable result."""
        work = get_object_or_404(scoped_work_orders(request.user), pk=pk)
        data = request.data
        if not isinstance(data, dict) or set(data) != {
            'operation',
            'fields',
            'expected_version',
            'idempotency_key',
        }:
            raise ValidationError(
                'Provide operation, fields, expected_version and idempotency_key.'
            )
        try:
            return Response(
                command(
                    work_order_id=work.pk,
                    actor=request.user,
                    reconcile_only=request.headers.get('X-AIMMS-Reconcile-Only') == '1',
                    **data,
                )
            )
        except WorkOrderCommandError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=409)
