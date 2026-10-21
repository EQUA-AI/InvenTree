"""Native Work-scoped original media and immutable accepted-report reads."""

from urllib.parse import quote

from django.db import transaction
from django.http import FileResponse, Http404
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.validators import validate_attachment_file
from tasks.permissions import require_permission
from tasks.report_models import AcceptedCloseoutReport, CaptureOriginal
from tasks.scope import ScopeError
from tasks.services.accepted_reports import canonical_export, report_summary
from tasks.services.capture_originals import original_summary, retain_original
from tasks.services.closeout_capture import CAPTURE_CLOSEOUT
from tasks.services.work_orders import (
    WorkOrderCommandError,
    WorkOrderScopeError,
    _locked_work_order,
    _require_scope,
    _require_version,
)
from tasks.workorder_api import WorkOrderEnabledMixin, _work_order_queryset


class OriginalUploadSerializer(serializers.Serializer):
    """An explicit recording plus optional client timing."""

    file = serializers.FileField(validators=[validate_attachment_file])
    voice_capture = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=1)
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    duration_ms = serializers.IntegerField(required=False, allow_null=True, min_value=0)

    def validate_started_at(self, value):
        """Do not assign the server timezone to a client recording timestamp."""
        if value is not None:
            raw = parse_datetime(str(self.initial_data['started_at']))
            if raw is None or not is_aware(raw):
                raise serializers.ValidationError(
                    'Recording start time must include a timezone.'
                )
            return raw
        return value


class OriginalMetadataSerializer(serializers.Serializer):
    """Protected media identity and supplied timing, with no storage URL."""

    id = serializers.IntegerField()
    filename = serializers.CharField()
    content_type = serializers.CharField()
    content_hash = serializers.CharField()
    byte_size = serializers.IntegerField()
    started_at = serializers.DateTimeField(allow_null=True)
    duration_ms = serializers.IntegerField(allow_null=True)
    url = serializers.CharField()


class AcceptedReportSerializer(serializers.Serializer):
    """Frozen payload, verifiable export, completion and native verification."""

    id = serializers.IntegerField()
    revision = serializers.IntegerField()
    url = serializers.CharField()
    content_hash = serializers.CharField()
    completed_by = serializers.IntegerField()
    completed_by_name = serializers.CharField()
    completed_at = serializers.DateTimeField()
    frozen_by = serializers.IntegerField()
    frozen_by_name = serializers.CharField()
    frozen_at = serializers.DateTimeField()
    verified_by = serializers.IntegerField(allow_null=True)
    verified_by_name = serializers.CharField(allow_null=True)
    verified_at = serializers.DateTimeField(allow_null=True)
    amendment_id = serializers.IntegerField(allow_null=True)
    document = serializers.JSONField()
    text = serializers.CharField()
    canonical_export = serializers.CharField()


class ScopedRecordView(WorkOrderEnabledMixin, APIView):
    """Reuse the canonical native Work read boundary for retained records."""

    permission_classes = [IsAuthenticated]

    def get_work_order(self, request, pk):
        """Resolve Work before resolving any child identity."""
        return get_object_or_404(_work_order_queryset(request.user), pk=pk)


class WorkCaptureMedia(ScopedRecordView):
    """Persist a consented owned session; authorize every byte read by Work."""

    @extend_schema(responses=OpenApiTypes.BINARY)
    def get(self, request, pk, media_id):
        """Foreign ids and missing bytes do not expose storage paths."""
        work = self.get_work_order(request, pk)
        row = (
            CaptureOriginal.objects
            .select_related('voice_capture')
            .filter(pk=media_id, work_order=work)
            .first()
        )
        if row is None:
            raise Http404
        if (
            row.voice_capture_id
            and row.voice_capture.state != 'committed'
            and row.voice_capture.owner_id != request.user.pk
        ):
            raise Http404
        try:
            stream = row.file.open('rb')
        except OSError as exc:
            raise Http404 from exc
        response = FileResponse(
            stream, content_type=row.content_type or 'application/octet-stream'
        )
        response['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(
            row.filename
        )
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class WorkCaptureMediaUpload(ScopedRecordView):
    """Upload on the collection route; the original route remains read-only."""

    @extend_schema(
        request=OriginalUploadSerializer, responses={201: OriginalMetadataSerializer}
    )
    @transaction.atomic
    def post(self, request, pk):
        """Only the session owner may retain bytes before transcript acceptance."""
        from voice.api import _policy_scope_key
        from voice.models import CaptureState, VoiceCaptureSession

        self.get_work_order(request, pk)
        data = OriginalUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        work = _locked_work_order(pk)
        require_permission(request.user, CAPTURE_CLOSEOUT)
        try:
            _require_scope(request.user, work)
        except (ScopeError, WorkOrderScopeError) as exc:
            raise Http404 from exc
        try:
            _require_version(work, values['expected_version'])
        except WorkOrderCommandError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=409)
        capture = (
            VoiceCaptureSession.objects
            .select_for_update()
            .filter(
                pk=values['voice_capture'],
                owner=request.user,
                scope_key=_policy_scope_key(),
                target_work_order_id=work.pk,
            )
            .first()
        )
        if not _policy_scope_key() or capture is None:
            raise Http404
        if capture.consent_version != 'closeout-originals-v1':
            return Response({'code': 'ORIGINAL_MEDIA_CONSENT_REQUIRED'}, status=403)
        if capture.target_version != work.lifecycle_version:
            return Response({'code': 'STALE_VERSION'}, status=409)
        if capture.state not in (CaptureState.ACTIVE, CaptureState.REVIEW):
            raise serializers.ValidationError(
                'Retain the original before accepting the transcript'
            )
        try:
            row = retain_original(
                work_order=work,
                actor=request.user,
                file=values['file'],
                content_type=values['file'].content_type,
                voice_capture=capture,
                started_at=values.get('started_at'),
                duration_ms=values.get('duration_ms'),
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(original_summary(row), status=201)


class WorkAcceptedReport(ScopedRecordView):
    """Read exact frozen bytes by revision; GET never creates a legacy snapshot."""

    @extend_schema(responses=AcceptedReportSerializer)
    def get(self, request, pk, report_id):
        """Apply current native scope even to historical accepted reports."""
        work = self.get_work_order(request, pk)
        row = AcceptedCloseoutReport.objects.filter(
            pk=report_id, closeout__work_order=work
        ).first()
        if row is None:
            raise Http404
        response = Response({
            **report_summary(row),
            'document': row.document,
            'text': row.text,
            'canonical_export': canonical_export(row),
        })
        response['Cache-Control'] = 'private, no-store'
        return response
