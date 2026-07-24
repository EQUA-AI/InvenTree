"""Authenticated REST endpoints for transcript-only capture (WS8 re-cut).

Mounted at /api/voice/. The pilot is single-tenant: the scope authority is
the same deployment policy key the AI boundary uses; multi-customer needs
the resolver enablement gate before these views may serve a second site.
Purposes are a fail-closed intersection of deployment configuration —
listing a purpose is necessary but never sufficient (plan §rollout).
"""

from __future__ import annotations

import os
import uuid

from django.db import transaction

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from voice.models import VoiceCaptureSession
from voice.services import capture as capture_service


def _policy_scope_key() -> str:
    """Policy scope key."""
    return os.environ.get('AIMMS_SINGLE_SITE_POLICY_KEY', '').strip()


def _enabled_purposes() -> tuple[str, ...]:
    """Enabled purposes."""
    if os.environ.get('AIMMS_VOICE_CAPTURE_ENABLED', '') != '1':
        return ()
    raw = os.environ.get('AIMMS_VOICE_PURPOSES', '')
    return tuple(purpose.strip() for purpose in raw.split(',') if purpose.strip())


def _consent_version() -> str:
    """Consent version."""
    return os.environ.get('AIMMS_VOICE_CONSENT_VERSION', 'consent-v1')


def _payload(capture: VoiceCaptureSession) -> dict:
    """Payload."""
    revisions = [
        {
            'id': str(revision.id),
            'revision': revision.revision,
            'full_text': revision.full_text,
            'content_hash': revision.content_hash,
            'language': revision.language,
            'edit_reason': revision.edit_reason,
            'accepted': hasattr(revision, 'acceptance'),
            'created_at': revision.created_at.isoformat(),
        }
        for revision in capture.revisions.all()
    ]
    return {
        'id': str(capture.id),
        'purpose': capture.purpose,
        'state': capture.state,
        'work_order_id': capture.target_work_order_id,
        'work_order_version': capture.target_version,
        'consent_version': capture.consent_version,
        'consented_at': capture.consented_at.isoformat(),
        'accepted_revision_id': (
            str(capture.accepted_revision_id) if capture.accepted_revision_id else None
        ),
        'terminal_reason': capture.terminal_reason or None,
        'revisions': revisions,
        'created_at': capture.created_at.isoformat(),
    }


_ERROR_STATUS = {
    'CAPTURE_NOT_FOUND': status.HTTP_404_NOT_FOUND,
    'CAPTURE_PURPOSE_UNSUPPORTED': status.HTTP_403_FORBIDDEN,
    'TRANSCRIPT_REVISION_STALE': status.HTTP_409_CONFLICT,
    'CAPTURE_STATE_CONFLICT': status.HTTP_409_CONFLICT,
    'CAPTURE_TARGET_INVALID': status.HTTP_404_NOT_FOUND,
    'DESTINATION_STALE': status.HTTP_409_CONFLICT,
    'DESTINATION_UNAVAILABLE': status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _error(exc: capture_service.CaptureError) -> Response:
    """Error."""
    return Response(
        {'error': exc.code, 'detail': str(exc)},
        status=_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
    )


def _lock_capture_target(work_order_id: int, expected_version: int) -> None:
    """Lock capture target."""
    from tasks.models import KanbanCard

    try:
        work_order = (
            KanbanCard.objects
            .select_for_update()
            .only('lifecycle_version')
            .get(pk=work_order_id)
        )
    except KanbanCard.DoesNotExist as exc:
        raise capture_service.CaptureTargetInvalid(
            'capture target unavailable'
        ) from exc
    if work_order.lifecycle_version != expected_version:
        raise capture_service.DestinationStale('capture target version is stale')


class CaptureListCreateView(APIView):
    """Create one consented, purpose-bound capture, or list the actor's."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='voice_captures_list')
    def get(self, request):
        """Get."""
        scope_key = _policy_scope_key()
        if not scope_key:
            return Response(
                {'error': 'SCOPE_UNRESOLVED', 'detail': 'deployment scope unset'},
                status=status.HTTP_403_FORBIDDEN,
            )
        rows = VoiceCaptureSession.objects.filter(
            owner=request.user, scope_key=scope_key
        )[:20]
        return Response({'results': [_payload(row) for row in rows]})

    def post(self, request):
        """Post."""
        data = request.data or {}
        scope_key = _policy_scope_key()
        if not scope_key:
            return Response(
                {'error': 'SCOPE_UNRESOLVED', 'detail': 'deployment scope unset'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            work_order_id = int(data.get('work_order_id'))
            work_order_version = int(data.get('work_order_version'))
        except (TypeError, ValueError):
            return Response(
                {
                    'error': 'CAPTURE_TARGET_INVALID',
                    'detail': 'work_order_id and work_order_version required',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                _lock_capture_target(work_order_id, work_order_version)
                capture = capture_service.create_capture(
                    owner=request.user,
                    scope_key=scope_key,
                    purpose=str(data.get('purpose', '')),
                    work_order_id=work_order_id,
                    work_order_version=work_order_version,
                    consent_version=_consent_version(),
                    idempotency_key=str(
                        data.get('idempotency_key') or f'ui:{uuid.uuid4()}'
                    )[:128],
                    policy_version='ws8-v1',
                    enabled_purposes=_enabled_purposes(),
                )
        except capture_service.CaptureError as exc:
            return _error(exc)
        return Response(_payload(capture), status=status.HTTP_201_CREATED)


class _OwnedCaptureView(APIView):
    """OwnedCaptureView."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def _capture(self, request, capture_id) -> VoiceCaptureSession:
        """Capture."""
        scope_key = _policy_scope_key()
        if not scope_key:
            raise capture_service.CaptureNotFound('no such capture')
        return capture_service.get_owned_capture(
            owner=request.user, scope_key=scope_key, capture_id=capture_id
        )


class CaptureDetailView(_OwnedCaptureView):
    """Owner-safe capture state and transcript history."""

    def get(self, request, capture_id):
        """Get."""
        try:
            return Response(_payload(self._capture(request, capture_id)))
        except capture_service.CaptureError as exc:
            return _error(exc)


class CaptureReviseView(_OwnedCaptureView):
    """Append one immutable correction revision."""

    def post(self, request, capture_id):
        """Post."""
        data = request.data or {}
        try:
            capture = self._capture(request, capture_id)
            capture_service.append_revision(
                capture=capture,
                full_text=str(data.get('full_text', '')),
                created_by=request.user,
                edit_reason=str(data.get('edit_reason', ''))[:128],
                provider='human_edit',
            )
            capture.refresh_from_db()
            return Response(_payload(capture))
        except capture_service.CaptureError as exc:
            return _error(exc)


class CaptureAcceptView(_OwnedCaptureView):
    """Accept exactly one reviewed revision, verified by hash."""

    def post(self, request, capture_id):
        """Post."""
        data = request.data or {}
        try:
            capture = self._capture(request, capture_id)
            capture_service.accept_revision(
                capture=capture,
                revision_id=str(data.get('revision_id', '')),
                content_hash=str(data.get('content_hash', '')),
                accepted_by=request.user,
            )
            capture.refresh_from_db()
            return Response(_payload(capture))
        except capture_service.CaptureError as exc:
            return _error(exc)


class CaptureCancelView(_OwnedCaptureView):
    """Cancel future processing; revisions remain auditable."""

    def post(self, request, capture_id):
        """Post."""
        try:
            capture = self._capture(request, capture_id)
            capture_service.cancel_capture(capture=capture)
            return Response(_payload(capture))
        except capture_service.CaptureError as exc:
            return _error(exc)


class CaptureCommitView(_OwnedCaptureView):
    """Hand the accepted revision to its canonical destination.

    Fails closed (503) until the WS11 Repair intake service and the
    Feature #15 closeout substrate exist; nothing is written meanwhile.
    """

    def post(self, request, capture_id):
        """Post."""
        try:
            capture = self._capture(request, capture_id)
            capture_service.handoff_capture(capture=capture)
            return Response(_payload(capture))
        except capture_service.CaptureError as exc:
            return _error(exc)
