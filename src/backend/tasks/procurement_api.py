"""Scoped R8 evidence commands; all execution stays in transactional services."""

from django.core.exceptions import PermissionDenied
from django.http import Http404

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tasks.jobkit_api import JobKitEnabledMixin, _scoped_work_order
from tasks.procurement_readout import event_fact
from tasks.procurement_serializers import (
    AcknowledgmentSerializer,
    ProcurementEventSerializer,
    SourceSelectionSerializer,
)
from tasks.scope import ScopeError
from tasks.services.procurement import acknowledge_order, select_source
from tasks.services.work_orders import WorkOrderCommandError, WorkOrderScopeError
from tasks.workorder_api import WorkOrderEnabledMixin


class ProcurementEvidenceCommand(WorkOrderEnabledMixin, JobKitEnabledMixin, APIView):
    """Authenticate and scope before validating or disclosing child evidence."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """Append one immutable fact or return its original durable result."""
        work_order = _scoped_work_order(request, pk)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            event = self.service(
                work_order_id=work_order.pk, actor=request.user, **dict(serializer.data)
            )
        except (ScopeError, WorkOrderScopeError):
            raise Http404
        except PermissionDenied as exc:
            return Response(
                {'code': 'PERMISSION_DENIED', 'detail': str(exc)}, status=403
            )
        except WorkOrderCommandError as exc:
            return Response(
                {
                    'code': getattr(exc, 'code', 'PROCUREMENT_INVALID'),
                    'detail': str(exc),
                },
                status=409,
            )
        response = Response(event_fact(event), status=200)
        response['Cache-Control'] = 'no-store'
        return response


class ProcurementSourceSelection(ProcurementEvidenceCommand):
    """Record a versioned, source-backed decision without purchasing effects."""

    serializer_class = SourceSelectionSerializer
    service = staticmethod(select_source)

    @extend_schema(
        request=SourceSelectionSerializer, responses={200: ProcurementEventSerializer}
    )
    def post(self, request, pk):
        """Append a source-selection snapshot."""
        return super().post(request, pk)


class ProcurementAcknowledgment(ProcurementEvidenceCommand):
    """Record explicit supplier acceptance against the actual native PO line."""

    serializer_class = AcknowledgmentSerializer
    service = staticmethod(acknowledge_order)

    @extend_schema(
        request=AcknowledgmentSerializer, responses={200: ProcurementEventSerializer}
    )
    def post(self, request, pk):
        """Append an acknowledgment snapshot."""
        return super().post(request, pk)
