"""REST API endpoints for the Repair Packet application."""

from __future__ import annotations

import uuid

from django.shortcuts import get_object_or_404
from django.urls import include, path

from rest_framework.response import Response
from rest_framework.views import APIView
from tasks.services import scheduling
from tasks.services.work_orders import ReadinessBlocked, WorkOrderCommandError

import InvenTree.permissions
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.mixins import ListCreateAPI, RetrieveUpdateDestroyAPI

from . import investigation, services
from .models import (
    LockoutPoint,
    PacketStatus,
    RepairInvestigationFinding,
    RepairPacket,
    RepairPacketGate,
    SafetyGateTemplate,
)
from .serializers import (
    ApprovedRepairScopeSerializer,
    LockoutPointSerializer,
    RepairInvestigationFindingSerializer,
    RepairPacketGateSerializer,
    RepairPacketGenerationRunSerializer,
    RepairPacketSerializer,
    SafetyEvidenceProofSerializer,
    SafetyGateTemplateSerializer,
)
from .work_packages import (
    DuplicateRepairConflict,
    WorkPackageError,
    create_repair_work_package,
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
        """Run generation for the packet, offloading to a background task if requested."""
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
                offload_task('repair.services.run_generation_by_id', packet.pk, params)
            )
        except Exception:
            return False


class RepairPacketGenerationStatus(APIView):
    """Return the current generation status + latest provenance run."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def get(self, request, pk):
        """Return the packet's generation status and its latest generation run."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        run = packet.generation_runs.first()
        return Response({
            'generation_status': packet.generation_status,
            'status': packet.status,
            'latest_generation_run': (
                RepairPacketGenerationRunSerializer(run).data if run else None
            ),
        })


class RepairPacketResolveGates(APIView):
    """Resolve applicable safety templates onto a packet."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk):
        """Resolve applicable safety gate templates onto the packet."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        created = services.resolve_safety_gates(packet, actor=_request_user(request))
        data = RepairPacketSerializer(packet).data
        data['created'] = created
        return Response(data)


class RepairPacketGateList(APIView):
    """Return the ordered safety gate checklist for a packet."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def get(self, request, pk):
        """Return the packet's safety gates ordered by sequence."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        gates = packet.gates.order_by('sequence', 'created_at')
        return Response(RepairPacketGateSerializer(gates, many=True).data)


class RepairPacketGateConfirm(APIView):
    """Confirm a safety gate."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        """Confirm the gate as completed by the requesting user."""
        gate = _get_gate(pk, gate_pk)
        ok, detail = services.confirm_gate(
            gate, user=_request_user(request), note=request.data.get('note', '')
        )
        return _gate_response(gate, ok, detail)


class RepairPacketGateVerify(APIView):
    """Record second-person verification for a safety gate."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        """Record second-person verification of the gate."""
        gate = _get_gate(pk, gate_pk)
        ok, detail = services.verify_gate(
            gate, user=_request_user(request), note=request.data.get('note', '')
        )
        return _gate_response(gate, ok, detail)


class RepairPacketGateWaive(APIView):
    """Waive a safety gate with reason and authority."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk, gate_pk):
        """Waive the gate, recording the reason and approving authority."""
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
        """Attach a structured evidence proof to the gate."""
        gate = _get_gate(pk, gate_pk)
        lockout_point = None
        if request.data.get('lockout_point'):
            lockout_point = get_object_or_404(
                LockoutPoint, pk=request.data['lockout_point'], gate=gate
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
        """Create or update a LOTO energy-control point for the gate."""
        gate = _get_gate(pk, gate_pk)
        point = services.upsert_lockout_point(
            gate, dict(request.data or {}), user=_request_user(request)
        )
        return Response(LockoutPointSerializer(point).data)


class RepairPacketAdvance(APIView):
    """Attempt a lifecycle transition (enforces FSM + safety gates + revalidation)."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk):
        """Attempt to advance the packet to the requested lifecycle status."""
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


class RepairPacketClose(APIView):
    """Finalize a packet-owned repair through the shared closeout service.

    Requires work-order completion authority, not merely packet access: this
    writes the structured closeout, the terminal work-order transition and the
    machine maintenance-history row in one transaction.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'

    def post(self, request, pk):
        """Close the packet and complete its work order atomically."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        data = dict(request.data or {})

        expected_version = data.get('expected_version')
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            return Response(
                {
                    'code': 'EXPECTED_VERSION_REQUIRED',
                    'detail': 'An integer expected_version is required.',
                },
                status=400,
            )

        try:
            result = services.close_repair_packet(
                packet,
                actor=request.user,
                closeout=data.get('closeout') or {},
                expected_version=expected_version,
                idempotency_key=data.get('idempotency_key') or uuid.uuid4().hex,
                reason=data.get('reason', ''),
            )
        except services.RepairCloseoutError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)
        except WorkOrderCommandError as exc:
            return Response(
                {'code': getattr(exc, 'code', 'COMMAND_ERROR'), 'detail': str(exc)},
                status=400,
            )

        payload = RepairPacketSerializer(packet).data
        payload['ok'] = True
        payload['work_order_id'] = result.work_order_id
        payload['lifecycle_status'] = result.lifecycle_status
        return Response(payload)


class RepairPacketFindings(APIView):
    """Typed investigation findings for one packet.

    Findings are rows, not prose: recording one keeps its category, unit and
    verification state, so the page can distinguish a SCADA reading from a
    technician's measurement rather than flattening both into a description.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    rolemap = {'POST': 'change'}

    def get(self, request, pk):
        """Return the packet's findings in recorded order."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        findings = packet.findings.select_related('snapshot').all()
        return Response({
            'count': findings.count(),
            'results': RepairInvestigationFindingSerializer(findings, many=True).data,
        })

    def post(self, request, pk):
        """Record or update one finding, keyed stably within the packet."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        data = dict(request.data or {})

        snapshot = None
        snapshot_id = data.get('snapshot_id')
        if snapshot_id:
            from assets.health_models import HealthEvidenceSnapshot

            snapshot = get_object_or_404(HealthEvidenceSnapshot, pk=snapshot_id)

        try:
            finding, created = investigation.record_finding(
                packet,
                finding_key=str(data.get('finding_key') or '')[:64],
                observation=str(data.get('observation') or ''),
                category=data.get('category')
                or RepairInvestigationFinding.Category.OTHER,
                value=data.get('value'),
                unit=str(data.get('unit') or ''),
                evidence_source=str(data.get('evidence_source') or ''),
                snapshot=snapshot,
                observed_at=data.get('observed_at') or None,
                verification=data.get('verification')
                or RepairInvestigationFinding.Verification.UNVERIFIED,
                actor=_request_user(request),
            )
        except investigation.InvestigationError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)

        return Response(
            RepairInvestigationFindingSerializer(finding).data,
            status=201 if created else 200,
        )


class RepairPacketApprovedScope(APIView):
    """The versioned scope that was approved for one packet.

    Approving freezes a version. A later AI regeneration produces new preliminary
    content but cannot rewrite what an approver signed off, so what was agreed
    stays reconstructable.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'
    rolemap = {'POST': 'change'}

    def get(self, request, pk):
        """Return every approved version, newest first."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        scopes = packet.approved_scopes.all()
        return Response({
            'count': scopes.count(),
            'results': ApprovedRepairScopeSerializer(scopes, many=True).data,
        })

    def post(self, request, pk):
        """Approve a scope, superseding any previous version."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        data = dict(request.data or {})

        try:
            scope = investigation.approve_repair_scope(
                packet,
                scope_lines=data.get('scope_lines'),
                verified_cause=str(data.get('verified_cause') or ''),
                failure_codes=data.get('failure_codes'),
                crew_size=data.get('crew_size'),
                planned_elapsed_minutes=data.get('planned_elapsed_minutes'),
                actor=_request_user(request),
                note=str(data.get('note') or ''),
            )
        except investigation.InvestigationError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)

        return Response(ApprovedRepairScopeSerializer(scope).data, status=201)


class RepairPacketStart(APIView):
    """Start a packet-owned repair through the canonical transition service.

    Starting is a readiness-gated lifecycle transition. It is never a board edit:
    moving a card into an "in progress" column cannot be allowed to start work
    with safety gates, parts readiness and assignment unevaluated.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'

    def get(self, request, pk):
        """Explain whether this repair can start, and what is blocking it."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        return Response(services.repair_start_readiness(packet, actor=request.user))

    def post(self, request, pk):
        """Transition the packet and its work order into execution."""
        packet = get_object_or_404(RepairPacket, pk=pk)
        data = dict(request.data or {})

        expected_version = data.get('expected_version')
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            return Response(
                {
                    'code': 'EXPECTED_VERSION_REQUIRED',
                    'detail': 'An integer expected_version is required.',
                },
                status=400,
            )

        try:
            result = services.start_repair_packet(
                packet,
                actor=request.user,
                expected_version=expected_version,
                idempotency_key=data.get('idempotency_key') or uuid.uuid4().hex,
                reason=data.get('reason', ''),
            )
        except services.RepairStartError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)
        except ReadinessBlocked as exc:
            # The authoritative blocker list, so the UI shows the same reasons
            # the server used to refuse rather than a generic failure.
            return Response(
                {
                    'code': 'READINESS_BLOCKED',
                    'detail': str(exc),
                    'readiness': services.repair_start_readiness(
                        packet, actor=request.user
                    ),
                },
                status=409,
            )
        except WorkOrderCommandError as exc:
            return Response(
                {'code': getattr(exc, 'code', 'COMMAND_ERROR'), 'detail': str(exc)},
                status=400,
            )

        payload = RepairPacketSerializer(packet).data
        payload['ok'] = True
        payload['work_order_id'] = result.work_order_id
        payload['lifecycle_status'] = result.lifecycle_status
        return Response(payload)


class MachineOpenRepairs(APIView):
    """Open repairs on one machine, each with its start readiness.

    The machine page never guesses which repair a "Start" click meant: when more
    than one is open it shows this list and makes the operator choose.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'

    def get(self, request, machine_id):
        """Return every non-terminal packet-backed repair for the machine."""
        packets = (
            RepairPacket.objects
            .filter(machine_id=machine_id, work_order__isnull=False)
            .exclude(status__in=[PacketStatus.CLOSED, PacketStatus.CANCELED])
            .select_related('work_order')
            .order_by('-created_at')
        )

        results = [
            {
                **services.repair_start_readiness(packet, actor=request.user),
                'fault_summary': packet.fault_summary,
                'criticality': packet.criticality,
                'work_order_title': packet.work_order.title,
            }
            for packet in packets
        ]
        return Response({'count': len(results), 'results': results})


class RepairPacketCancel(APIView):
    """Convenience endpoint to cancel a packet with a reason."""

    permission_classes = [InvenTree.permissions.IsAuthenticatedOrReadScope]

    def post(self, request, pk):
        """Cancel the packet, recording the supplied reason."""
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


class RepairWorkPackageCreate(APIView):
    """Create one machine-linked work order and optional repair packet.

    The single write path for every maintenance intake entry point: the
    Maintenance workspace button, a machine Repair action, a health anomaly and
    (later) an approved AI proposal all post the same versioned draft here. It
    plans work; it never starts it.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.add'

    def post(self, request):
        """Validate the draft and execute the atomic create command."""
        data = dict(request.data or {})
        idempotency_key = data.pop('idempotency_key', None) or uuid.uuid4().hex

        try:
            result = create_repair_work_package(
                actor=request.user, draft=data, idempotency_key=idempotency_key
            )
        except DuplicateRepairConflict as exc:
            # 409, not 400: the request is well-formed, the world disagrees.
            return Response(
                {'code': exc.code, 'detail': str(exc), 'duplicates': exc.duplicates},
                status=409,
            )
        except WorkPackageError as exc:
            return Response(
                {
                    'code': getattr(exc, 'code', 'WORK_PACKAGE_INVALID'),
                    'detail': str(exc),
                },
                status=400,
            )
        except scheduling.IdempotencyConflict as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=409)
        except scheduling.WorkOrderCommandError as exc:
            return Response(
                {'code': getattr(exc, 'code', 'COMMAND_ERROR'), 'detail': str(exc)},
                status=400,
            )

        return Response(result.as_dict(), status=201)


maintenance_api_urls = [
    path(
        'work-packages/',
        include([
            path(
                'create/',
                RepairWorkPackageCreate.as_view(),
                name='maintenance-work-package-create',
            )
        ]),
    )
]


repair_api_urls = [
    path(
        'machines/<int:machine_id>/open-repairs/',
        MachineOpenRepairs.as_view(),
        name='machine-open-repairs',
    ),
    path(
        'gate-templates/',
        include([
            path(
                '<int:pk>/',
                SafetyGateTemplateDetail.as_view(),
                name='safety-gate-template-detail',
            ),
            path(
                '', SafetyGateTemplateList.as_view(), name='safety-gate-template-list'
            ),
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
                '<int:pk>/close/',
                RepairPacketClose.as_view(),
                name='repair-packet-close',
            ),
            path(
                '<int:pk>/start/',
                RepairPacketStart.as_view(),
                name='repair-packet-start',
            ),
            path(
                '<int:pk>/findings/',
                RepairPacketFindings.as_view(),
                name='repair-packet-findings',
            ),
            path(
                '<int:pk>/approved-scope/',
                RepairPacketApprovedScope.as_view(),
                name='repair-packet-approved-scope',
            ),
            path(
                '<int:pk>/cancel/',
                RepairPacketCancel.as_view(),
                name='repair-packet-cancel',
            ),
            path(
                '<int:pk>/', RepairPacketDetail.as_view(), name='repair-packet-detail'
            ),
            path('', RepairPacketList.as_view(), name='repair-packet-list'),
        ]),
    ),
]

# Risk Radar / Command Center routes (Features #4 / #16); appended so the
# module keeps one authoritative URL list for /api/repair/.
from .risk_api import risk_api_urls

repair_api_urls += risk_api_urls
