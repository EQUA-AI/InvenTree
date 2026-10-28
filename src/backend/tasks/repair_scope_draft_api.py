"""Native scoped read/save endpoint for proposed repair-scope drafts."""

from django.core.exceptions import PermissionDenied
from django.http import Http404

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tasks.models import WorkOrder
from tasks.procedure_execution_api import _scoped_work_order
from tasks.services.repair_scope_drafts import (
    RepairScopeDraftError,
    read_repair_scope_draft,
    save_repair_scope_draft,
)
from tasks.services.work_orders import (
    IdempotencyConflict,
    StaleVersion,
    WorkOrderScopeError,
)
from tasks.workorder_api import WorkOrderEnabledMixin
from tasks.workorder_mutations import object_data

_FIELDS = {
    'expected_version',
    'idempotency_key',
    'machine_id',
    'repair_packet_id',
    'source_event_id',
    'component_code',
    'e03_attachment_id',
    'manual_attachment_ids',
    'scope',
}


class RepairScopeDraft(WorkOrderEnabledMixin, APIView):
    """Expose actual draft receipts without any approval or lifecycle action."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """Read a persisted proposal with fresh authorization and no writes."""
        _scoped_work_order(request, pk)
        try:
            result = read_repair_scope_draft(pk, request.user)
        except (PermissionDenied, WorkOrderScopeError):
            raise Http404
        response = Response(result)
        response['Cache-Control'] = 'no-store'
        return response

    def post(self, request, pk):
        """Save exactly one scoped proposal through the native command service."""
        _scoped_work_order(request, pk)
        data = object_data(request.data)
        if set(data) != _FIELDS:
            raise ValidationError(
                'Supply exactly the proposed repair-scope command fields.'
            )
        try:
            draft = save_repair_scope_draft(
                work_order_id=pk, actor=request.user, **data
            )
        except PermissionDenied as exc:
            return Response({'detail': str(exc)}, status=403)
        except WorkOrder.DoesNotExist:
            raise Http404
        except (IdempotencyConflict, StaleVersion) as exc:
            return Response({'detail': str(exc), 'code': exc.code}, status=409)
        except (RepairScopeDraftError, WorkOrderScopeError) as exc:
            return Response({'detail': str(exc), 'code': exc.code}, status=400)
        response = Response({
            'work_order_id': draft['work_order_id'],
            'machine_id': draft['machine_id'],
            'repair_packet_id': draft['repair_packet_id'],
            'lifecycle_version': draft['lifecycle_version'],
            'draft': draft,
        })
        response['Cache-Control'] = 'no-store'
        return response
