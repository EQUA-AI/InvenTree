"""Human-only authority recording inside the native Work role/scope boundary."""

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.permissions
from tasks.scope import scoped_work_orders
from tasks.services.operational_authority import command, project
from tasks.services.work_orders import WorkOrderCommandError


class WorkOperationalAuthority(APIView):
    """No OT action, gate confirmation or lifecycle transition is performed."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    rolemap = {'POST': 'change'}

    def get(self, request, pk):
        """Read only the authorized parent's records and recording permissions."""
        if not getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False):
            raise Http404
        work = get_object_or_404(scoped_work_orders(request.user), pk=pk)
        return Response(project(work, request.user))

    def post(self, request, pk):
        """The service rehydrates role grants, scope and record-specific authority."""
        if not getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False):
            raise Http404
        work = get_object_or_404(scoped_work_orders(request.user), pk=pk)
        if not isinstance(request.data, dict) or set(request.data) - {
            'expected_version',
            'expected_context',
            'idempotency_key',
            'operation',
            'fields',
            'supersedes',
        }:
            raise ValidationError('Unsupported authority command fields.')
        if not {
            'expected_version',
            'expected_context',
            'idempotency_key',
            'operation',
            'fields',
        }.issubset(request.data):
            raise ValidationError('A complete versioned authority command is required.')
        try:
            return Response(
                command(work_order_id=work.pk, actor=request.user, **request.data)
            )
        except WorkOrderCommandError as exc:
            return Response(
                {
                    'code': getattr(exc, 'code', 'AUTHORITY_CONFLICT'),
                    'detail': str(exc),
                },
                status=409,
            )
