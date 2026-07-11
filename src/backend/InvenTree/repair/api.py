"""REST API endpoints for the Repair Packet application."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.urls import include, path

from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI

from . import services
from .models import LockoutPoint, PacketStatus, RepairPacket, RepairPacketGate, SafetyGateTemplate
from .serializers import (
    LockoutPointSerializer,
    RepairPacketGenerationRunSerializer,
    RepairPacketGateSerializer,
    RepairPacketSerializer,
    SafetyEvidenceProofSerializer,
    SafetyGateTemplateSerializer,
)


def _truthy(value) -> bool:
    """Interpret a query/body flag as boolean."""
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _request_user(request):
    """Return the authenticated request user or None."""
    return request.user if request.user.is_authenticated else None


class SafetyGateTemplateList(ListCreateAPI):
    """List and create reusable safety gate templates."""

    queryset = SafetyGateTemplate.objects.all()
    serializer_class = SafetyGateTemplateSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['active', 'gate_type', 'risk_tier', 'is_blocking']
    search_fields = ['name', 'instructions']
    ordering_fields = ['default_sequence', 'name', 'risk_tier', 'updated_at']
    ordering = 'default_sequence'


class SafetyGateTemplateDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete a safety gate template."""

    queryset = SafetyGateTemplate.objects.all()
    serializer_class = SafetyGateTemplateSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]


class RepairPacketList(ListCreateAPI):
    """List and create repair packets."""

    queryset = RepairPacket.objects.all()
    serializer_class = RepairPacketSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]
    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['status', 'criticality', 'machine', 'generation_status']
    search_fields = ['reference', 'fault_summary', 'symptom']
    ordering_fields = ['created_at', 'updated_at', 'criticality', 'status']
    ordering = '-created_at'

    def perform_create(self, serializer):
        """Attach the requesting user as the creator."""
        serializer.save(created_by=_request_user(self.request))


class RepairPacketDetail(RetrieveUpdateDestroyAPI):
    """Retrieve, update, or delete a single repair packet."""

    queryset = RepairPacket.objects.all()
    serializer_class = RepairPacketSerializer
    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]


class RepairPacketGenerate(APIView):
    """Run the AI generation layer to (re)generate diagnosis + parts + gates."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk):
        packet = get_object_or_404(RepairPacket, pk=pk)
        params = dict(request.data or {})
        if request.user.is_authenticated:
            params.setdefault('user_id', request.user.pk)

        if _truthy(params.get('async')) or _truthy(request.query_params.get('async')):
            if self._offload(packet, params):
                return Response(RepairPacketSerializer(packet).data, status=202)

        packet = services.run_repair_packet_workflow(packet, params)
        return Response(RepairPacketSerializer(packet).data)

    @staticmethod
    def _offload(packet, params) -> bool:
        """Best-effort background offload; returns True if enqueued."""
        try:
            from InvenTree.tasks import offload_task

            from .models import GenerationStatus

            packet.generation_status = GenerationStatus.PENDING
            packet.save(update_fields=['generation_status', 'updated_at'])
            params.pop('async', None)
            return bool(
                offload_task(
                    'repair.services.run_generation_by_id', packet.pk, params
                )
            )
        except Exception:
            return False


class RepairPacketGenerationStatus(APIView):
    """Return the current generation status + latest provenance run."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def get(self, request, pk):
        packet = get_object_or_404(RepairPacket, pk=pk)
        run = packet.generation_runs.first()
        return Response(
            {
                'generation_status': packet.generation_status,
                'status': packet.status,
                'latest_generation_run': (
                    RepairPacketGenerationRunSerializer(run).data if run else None
                ),
            }
        )


class RepairPacketResolveGates(APIView):
    """Resolve applicable safety templates onto a packet."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk):
        packet = get_object_or_404(RepairPacket, pk=pk)
        created = services.resolve_safety_gates(packet, actor=_request_user(request))
        data = RepairPacketSerializer(packet).data
        data['created'] = created
        return Response(data)


class RepairPacketGateList(APIView):
    """Return the ordered safety gate checklist for a packet."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def get(self, request, pk):
        packet = get_object_or_404(RepairPacket, pk=pk)
        gates = packet.gates.order_by('sequence', 'created_at')
        return Response(RepairPacketGateSerializer(gates, many=True).data)


class RepairPacketGateConfirm(APIView):
    """Confirm a safety gate."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        gate = _get_gate(pk, gate_pk)
        ok, detail = services.confirm_gate(
            gate,
            user=_request_user(request),
            note=request.data.get('note', ''),
        )
        return _gate_response(gate, ok, detail)


class RepairPacketGateVerify(APIView):
    """Record second-person verification for a safety gate."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        gate = _get_gate(pk, gate_pk)
        ok, detail = services.verify_gate(
            gate,
            user=_request_user(request),
            note=request.data.get('note', ''),
        )
        return _gate_response(gate, ok, detail)


class RepairPacketGateWaive(APIView):
    """Waive a safety gate with reason and authority."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        gate = _get_gate(pk, gate_pk)
        ok, detail = services.waive_gate(
            gate,
            user=_request_user(request),
            reason=request.data.get('reason', ''),
            authority=request.data.get('authority', ''),
        )
        return _gate_response(gate, ok, detail)


class RepairPacketGateProof(APIView):
    """Attach structured proof to a gate."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        gate = _get_gate(pk, gate_pk)
        lockout_point = None
        if request.data.get('lockout_point'):
            lockout_point = get_object_or_404(
                LockoutPoint,
                pk=request.data['lockout_point'],
                gate=gate,
            )
        proof = services.add_gate_proof(
            gate,
            request.data.get('proof_type', 'reading'),
            value=request.data.get('value', {}),
            user=_request_user(request),
            lockout_point=lockout_point,
        )
        return Response(SafetyEvidenceProofSerializer(proof).data, status=201)


class RepairPacketGateLockout(APIView):
    """Create or update a LOTO energy-control point."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        gate = _get_gate(pk, gate_pk)
        point = services.upsert_lockout_point(
            gate,
            dict(request.data or {}),
            user=_request_user(request),
        )
        return Response(LockoutPointSerializer(point).data)


class RepairPacketAdvance(APIView):
    """Attempt a lifecycle transition (enforces FSM + safety gates + revalidation)."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk):
        packet = get_object_or_404(RepairPacket, pk=pk)
        to = request.data.get('to')
        reason = request.data.get('reason', '')
        ok, detail = services.advance_packet(
            packet, to, _request_user(request), reason=reason
        )
        data = RepairPacketSerializer(packet).data
        data['ok'] = ok
        data['detail'] = detail
        return Response(data, status=200 if ok else 400)


class RepairPacketCancel(APIView):
    """Convenience endpoint to cancel a packet with a reason."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk):
        packet = get_object_or_404(RepairPacket, pk=pk)
        reason = request.data.get('reason', '')
        ok, detail = services.advance_packet(
            packet, PacketStatus.CANCELED, _request_user(request), reason=reason
        )
        data = RepairPacketSerializer(packet).data
        data['ok'] = ok
        data['detail'] = detail
        return Response(data, status=200 if ok else 400)


def _get_gate(packet_pk, gate_pk):
    """Load a gate constrained to its parent packet."""
    return get_object_or_404(RepairPacketGate, pk=gate_pk, packet_id=packet_pk)


def _gate_response(gate, ok: bool, detail: str):
    """Return a gate action response with ok/detail."""
    gate.refresh_from_db()
    data = RepairPacketGateSerializer(gate).data
    data['ok'] = ok
    data['detail'] = detail
    return Response(data, status=200 if ok else 400)


repair_api_urls = [
    path(
        'gate-templates/',
        include([
            path(
                '<int:pk>/',
                SafetyGateTemplateDetail.as_view(),
                name='safety-gate-template-detail',
            ),
            path('', SafetyGateTemplateList.as_view(), name='safety-gate-template-list'),
        ]),
    ),
    path(
        'packets/',
        include([
            path(
                '<int:pk>/generate/',
                RepairPacketGenerate.as_view(),
                name='repair-packet-generate',
            ),
            path(
                '<int:pk>/generation-status/',
                RepairPacketGenerationStatus.as_view(),
                name='repair-packet-generation-status',
            ),
            path(
                '<int:pk>/resolve-gates/',
                RepairPacketResolveGates.as_view(),
                name='repair-packet-resolve-gates',
            ),
            path(
                '<int:pk>/gates/',
                RepairPacketGateList.as_view(),
                name='repair-packet-gate-list',
            ),
            path(
                '<int:pk>/gates/<int:gate_pk>/confirm/',
                RepairPacketGateConfirm.as_view(),
                name='repair-packet-gate-confirm',
            ),
            path(
                '<int:pk>/gates/<int:gate_pk>/verify/',
                RepairPacketGateVerify.as_view(),
                name='repair-packet-gate-verify',
            ),
            path(
                '<int:pk>/gates/<int:gate_pk>/waive/',
                RepairPacketGateWaive.as_view(),
                name='repair-packet-gate-waive',
            ),
            path(
                '<int:pk>/gates/<int:gate_pk>/proofs/',
                RepairPacketGateProof.as_view(),
                name='repair-packet-gate-proof',
            ),
            path(
                '<int:pk>/gates/<int:gate_pk>/lockout/',
                RepairPacketGateLockout.as_view(),
                name='repair-packet-gate-lockout',
            ),
            path(
                '<int:pk>/advance/',
                RepairPacketAdvance.as_view(),
                name='repair-packet-advance',
            ),
            path(
                '<int:pk>/cancel/',
                RepairPacketCancel.as_view(),
                name='repair-packet-cancel',
            ),
            path(
                '<int:pk>/',
                RepairPacketDetail.as_view(),
                name='repair-packet-detail',
            ),
            path('', RepairPacketList.as_view(), name='repair-packet-list'),
        ]),
    ),
]
