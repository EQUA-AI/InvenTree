"""REST API for machine health.

Read endpoints are nested under a machine so scope is applied before lookup: the
machine is resolved and authorized first, and every signal, anomaly and snapshot
is filtered by that machine. A client never names a source, a tag or a snapshot
id directly, so it cannot reach another machine's telemetry by guessing one.

Ingestion is separate and authenticated by signature, not by session.
"""

from __future__ import annotations

import json

from django.shortcuts import get_object_or_404
from django.urls import include, path

from rest_framework.response import Response
from rest_framework.views import APIView

import InvenTree.permissions
from assets.models import AssetMachine
from InvenTree.mixins import ListAPI

from .connectors.webhook import WebhookAuthError, verify_delivery
from .models import (
    ACTIVE_ANOMALY_STATUSES,
    AnomalyStatus,
    HealthSource,
    MachineAnomaly,
    MachineSignalBinding,
    SnapshotReason,
)
from .serializers import (
    HealthEvidenceSnapshotSerializer,
    MachineAnomalySerializer,
    MachineHealthSummarySerializer,
    MachineSignalBindingSerializer,
    MachineSignalSerializer,
)
from .services import anomalies as anomaly_services
from .services import snapshots as snapshot_services
from .services.ingestion import IngestionError, coerce_datetime, ingest_readings
from .services.preliminary import analyze_anomaly
from .services.summary import health_summary, signal_rows
from .services.trends import TrendError, read_trend

#: Anomaly lists are bounded; the blade shows the active set, not a history dump.
MAX_ANOMALY_PAGE = 200


def _machine(pk):
    """Resolve the machine before anything else is read."""
    return get_object_or_404(AssetMachine, pk=pk)


class _MachineHealthView(APIView):
    """Shared permission wiring for machine-scoped health reads."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order'


class MachineHealthSummary(_MachineHealthView):
    """Current condition, freshness, anomaly counts and source status."""

    def get(self, request, pk):
        """Return the machine's health summary."""
        machine = _machine(pk)
        data = health_summary(machine)
        return Response(MachineHealthSummarySerializer(data).data)


class MachineHealthSignals(_MachineHealthView):
    """Mapped signals with their current values and freshness."""

    def get(self, request, pk):
        """Return every active binding for the machine."""
        machine = _machine(pk)
        rows = signal_rows(machine)
        return Response({
            'count': len(rows),
            'results': MachineSignalSerializer(rows, many=True).data,
        })


class MachineHealthAnomalies(_MachineHealthView):
    """Anomalies for one machine, active by default."""

    def get(self, request, pk):
        """Return the machine's anomalies, filtered by status and severity."""
        machine = _machine(pk)

        queryset = (
            MachineAnomaly.objects
            .filter(machine=machine)
            .select_related('source')
            .prefetch_related('bindings')
        )

        status_filter = request.query_params.get('status')
        if status_filter == 'all':
            pass
        elif status_filter:
            if status_filter not in AnomalyStatus.values:
                return Response(
                    {'code': 'INVALID_STATUS', 'detail': 'Unknown anomaly status.'},
                    status=400,
                )
            queryset = queryset.filter(status=status_filter)
        else:
            queryset = queryset.filter(
                status__in=[value.value for value in ACTIVE_ANOMALY_STATUSES]
            )

        severity = request.query_params.get('severity')
        if severity:
            queryset = queryset.filter(severity=severity)

        queryset = queryset[:MAX_ANOMALY_PAGE]
        return Response({
            'count': len(queryset),
            'results': MachineAnomalySerializer(queryset, many=True).data,
        })


class MachineHealthTrend(_MachineHealthView):
    """A bounded historical window for one mapped signal.

    The caller names a *binding*, never an external tag: the tag comes from the
    mapping row, so a client cannot reach an arbitrary point in the source system
    by supplying its name.
    """

    def get(self, request, pk):
        """Read a bounded trend, or say plainly why one is unavailable."""
        machine = _machine(pk)

        binding_id = request.query_params.get('binding')
        if not binding_id or not str(binding_id).isdigit():
            return Response(
                {
                    'code': 'BINDING_REQUIRED',
                    'detail': 'A numeric binding id is required.',
                },
                status=400,
            )

        try:
            start = (
                coerce_datetime(request.query_params['from'])
                if request.query_params.get('from')
                else None
            )
            end = (
                coerce_datetime(request.query_params['to'])
                if request.query_params.get('to')
                else None
            )
        except IngestionError as exc:
            return Response({'code': 'INVALID_WINDOW', 'detail': str(exc)}, status=400)

        max_samples = request.query_params.get('max_samples')
        try:
            result = read_trend(
                machine,
                binding_id=int(binding_id),
                start=start,
                end=end,
                max_samples=int(max_samples) if max_samples else None,
            )
        except (TrendError, ValueError) as exc:
            return Response(
                {'code': getattr(exc, 'code', 'TREND_INVALID'), 'detail': str(exc)},
                status=400,
            )

        return Response(result)


class MachineHealthSnapshots(_MachineHealthView):
    """Evidence snapshots captured for one machine."""

    def get(self, request, pk):
        """Return the machine's most recent evidence snapshots."""
        machine = _machine(pk)
        snapshots = snapshot_services.snapshots_for_machine(machine)
        return Response({
            'count': len(snapshots),
            'results': HealthEvidenceSnapshotSerializer(snapshots, many=True).data,
        })


class MachineAnomalyAcknowledge(APIView):
    """Record that a human has seen an anomaly.

    Acknowledging is not resolving, and it satisfies no safety gate: it changes
    the anomaly's status and nothing else about the machine or its repairs.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'

    def post(self, request, pk, anomaly_pk):
        """Acknowledge one anomaly belonging to this machine."""
        machine = _machine(pk)
        anomaly = get_object_or_404(MachineAnomaly, pk=anomaly_pk, machine=machine)

        try:
            anomaly = anomaly_services.acknowledge_anomaly(
                anomaly.pk, actor=request.user, note=request.data.get('note', '')
            )
        except anomaly_services.AnomalyError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)

        return Response(MachineAnomalySerializer(anomaly).data)


class MachineAnomalyEvidence(APIView):
    """Capture immutable evidence for the signals behind an anomaly."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'

    def post(self, request, pk, anomaly_pk):
        """Snapshot each implicated signal and return the citations."""
        machine = _machine(pk)
        anomaly = get_object_or_404(MachineAnomaly, pk=anomaly_pk, machine=machine)

        reason = request.data.get('reason') or SnapshotReason.ANOMALY_REPAIR
        try:
            snapshots = snapshot_services.capture_anomaly_evidence(
                anomaly, reason=reason, actor=request.user
            )
        except snapshot_services.SnapshotError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)

        return Response(
            {
                'count': len(snapshots),
                'results': HealthEvidenceSnapshotSerializer(snapshots, many=True).data,
            },
            status=201,
        )


class MachineAnomalyPreliminaryAnalysis(APIView):
    """Produce an evidence-cited preliminary result for one anomaly.

    Preliminary, always: the response restates measurements with their citations
    and is never a verified diagnosis. It is generated on request rather than
    automatically, so an operator chooses when to spend the analysis and knows
    exactly which instant it describes.
    """

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.change'

    def post(self, request, pk, anomaly_pk):
        """Analyze the anomaly's current evidence and return the result."""
        machine = _machine(pk)
        anomaly = get_object_or_404(MachineAnomaly, pk=anomaly_pk, machine=machine)

        result = analyze_anomaly(anomaly, actor=request.user)
        return Response({'anomaly': anomaly.pk, 'preliminary_results': result})


class MachineSignalBindingList(ListAPI):
    """Administrative view of tag mappings.

    Separated from the operator surfaces above: reading a machine's condition and
    configuring where that condition comes from are different authorities.
    """

    serializer_class = MachineSignalBindingSerializer
    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'admin'

    def get_queryset(self):
        """Return bindings, optionally filtered to one machine or source."""
        queryset = MachineSignalBinding.objects.select_related('source', 'machine')
        machine = self.request.query_params.get('machine')
        source = self.request.query_params.get('source')
        if machine:
            queryset = queryset.filter(machine_id=machine)
        if source:
            queryset = queryset.filter(source_id=source)
        return queryset.order_by('machine__name', 'display_name')


class HealthWebhookIngest(APIView):
    """Accept a signed batch of readings or alarms from an external gateway.

    Authenticated by HMAC signature over the raw body, not by session: the caller
    is a machine, not a user. Permission classes are intentionally empty because
    :func:`verify_delivery` is the authentication boundary, and it fails closed on
    a missing secret, a bad signature, a stale timestamp or a repeated delivery.
    """

    permission_classes = []
    authentication_classes = []

    def post(self, request, source_id):
        """Verify the delivery, then ingest its readings and alarms."""
        source = get_object_or_404(HealthSource, pk=source_id, active=True)

        try:
            verify_delivery(source, request.META, request.body)
        except WebhookAuthError as exc:
            # Deliberately terse: a probing caller learns only that it failed.
            return Response({'code': exc.code, 'detail': str(exc)}, status=401)

        try:
            payload = json.loads(request.body or b'{}')
        except json.JSONDecodeError:
            return Response(
                {'code': 'MALFORMED_BODY', 'detail': 'Body is not valid JSON.'},
                status=400,
            )

        try:
            result = ingest_readings(source, payload.get('readings') or [])
        except IngestionError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)

        alarms_recorded = self._ingest_alarms(source, payload.get('alarms') or [])

        # Threshold rules run after the batch lands so one evaluation sees the
        # whole update rather than firing per reading.
        for machine_id in result.machine_ids:
            machine = AssetMachine.objects.filter(pk=machine_id).first()
            if machine is not None:
                anomaly_services.evaluate_thresholds(machine)

        response = result.as_dict()
        response['alarms_recorded'] = alarms_recorded
        return Response(response, status=202)

    @staticmethod
    def _ingest_alarms(source, alarms) -> int:
        """Record source-declared alarms against their mapped machines."""
        if not isinstance(alarms, list):
            raise IngestionError('alarms must be a list.')

        recorded = 0
        for entry in alarms[:100]:
            if not isinstance(entry, dict):
                continue

            binding = (
                MachineSignalBinding.objects
                .select_related('machine')
                .filter(source=source, external_key=entry.get('external_key') or '')
                .first()
            )
            machine = binding.machine if binding else None
            if machine is None:
                # An alarm about a tag this deployment has not mapped is dropped:
                # the source may not invent machines.
                continue

            observed_at = entry.get('observed_at')
            if observed_at:
                observed_at = coerce_datetime(observed_at)

            try:
                anomaly_services.ingest_source_alarm(
                    machine=machine,
                    source=source,
                    alarm_code=str(entry.get('alarm_code') or '')[:64],
                    title=str(entry.get('title') or 'Source alarm')[:255],
                    severity=entry.get('severity') or 'warning',
                    observed_at=observed_at,
                    external_id=str(entry.get('external_id') or '')[:128],
                    external_key=str(entry.get('external_key') or '')[:255],
                    evidence_summary=str(entry.get('message') or '')[:2000],
                    metrics=entry.get('metrics')
                    if isinstance(entry.get('metrics'), dict)
                    else {},
                )
            except anomaly_services.AnomalyError:
                continue
            recorded += 1

        return recorded


machine_health_api_urls = [
    path(
        'machines/<int:pk>/health/',
        include([
            path('', MachineHealthSummary.as_view(), name='machine-health-summary'),
            path(
                'signals/',
                MachineHealthSignals.as_view(),
                name='machine-health-signals',
            ),
            path(
                'anomalies/',
                MachineHealthAnomalies.as_view(),
                name='machine-health-anomalies',
            ),
            path(
                'anomalies/<int:anomaly_pk>/acknowledge/',
                MachineAnomalyAcknowledge.as_view(),
                name='machine-health-anomaly-acknowledge',
            ),
            path(
                'anomalies/<int:anomaly_pk>/evidence/',
                MachineAnomalyEvidence.as_view(),
                name='machine-health-anomaly-evidence',
            ),
            path(
                'anomalies/<int:anomaly_pk>/preliminary-analysis/',
                MachineAnomalyPreliminaryAnalysis.as_view(),
                name='machine-health-anomaly-preliminary-analysis',
            ),
            path('trend/', MachineHealthTrend.as_view(), name='machine-health-trend'),
            path(
                'snapshots/',
                MachineHealthSnapshots.as_view(),
                name='machine-health-snapshots',
            ),
        ]),
    ),
    path(
        'bindings/',
        MachineSignalBindingList.as_view(),
        name='machine-health-binding-list',
    ),
    path(
        'ingest/<int:source_id>/',
        HealthWebhookIngest.as_view(),
        name='machine-health-ingest',
    ),
]
