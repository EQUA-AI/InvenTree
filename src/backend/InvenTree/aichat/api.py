"""Authenticated REST endpoints for chat proposals and message feedback.

Confirmation is a session-authenticated visual action on the normal Django
rail — CSRF-protected, owner-bound, and dispatching only the canonical
work-order command service. Voice, transcripts, and model output cannot
call these endpoints. The scoped-chat rail (context resolution, scoped
conversations, per-call tool invocation) was removed in S14: its
functionality lives on the main rail, where every tool call re-authorizes
the acting user server-side.
"""

from __future__ import annotations

import hashlib
import uuid

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from aichat.models import ChatActionProposal
from aichat.services import proposals as proposal_service


def _scope_strings(user) -> tuple[str, str]:
    """Resolve the actor's maintenance scope fail-closed."""
    from tasks.scope import ScopeError, scope_for_actor

    try:
        scopes = scope_for_actor(user)
    except ScopeError as exc:
        raise proposal_service.ProposalError('scope unresolved') from exc
    if not scopes:
        raise proposal_service.ProposalError('scope unresolved')
    key = '|'.join(sorted(repr(scope) for scope in scopes))
    return key, hashlib.sha256(key.encode('utf-8')).hexdigest()


def _payload(proposal: ChatActionProposal) -> dict:
    """Payload."""
    return {
        'id': str(proposal.id),
        'action_type': proposal.action_type,
        'state': proposal.state,
        'work_order_id': proposal.target_work_order_id,
        'target_version': proposal.target_version,
        'intent': proposal.intent,
        'preview': proposal.preview,
        'reason': proposal.reason,
        'expires_at': proposal.expires_at.isoformat(),
        'confirmed_at': (
            proposal.confirmed_at.isoformat() if proposal.confirmed_at else None
        ),
        'receipt': proposal.receipt,
        'failure_code': proposal.failure_code or None,
        'created_at': proposal.created_at.isoformat(),
    }


_ERROR_STATUS = {
    'CAPABILITY_DENIED': status.HTTP_403_FORBIDDEN,
    'PROPOSAL_NOT_FOUND': status.HTTP_404_NOT_FOUND,
    'PROPOSAL_EXPIRED': status.HTTP_409_CONFLICT,
    'PROPOSAL_STATE_CONFLICT': status.HTTP_409_CONFLICT,
    'PROPOSAL_REVALIDATION_FAILED': status.HTTP_409_CONFLICT,
    'PROPOSAL_INVALID': status.HTTP_403_FORBIDDEN,
    'STRICT_CONFIRMATION_REQUIRED': status.HTTP_400_BAD_REQUEST,
    'DUPLICATE_OPEN_REPAIR': status.HTTP_409_CONFLICT,
}


def _error(exc: proposal_service.ProposalError) -> Response:
    """Error."""
    body = {'error': exc.code, 'detail': str(exc)}
    duplicates = getattr(exc, 'duplicates', None)
    if duplicates:
        # Same key the preview uses, so a client renders one list both times.
        body['duplicate_open_repairs'] = duplicates
    return Response(
        body, status=_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST)
    )


def _object_scope_hash(user) -> str:
    """Object scope hash."""
    try:
        return _scope_strings(user)[1]
    except proposal_service.ProposalError as exc:
        raise proposal_service.ProposalNotFound('no such proposal') from exc


class ProposalListCreateView(APIView):
    """List the actor's proposals or create one from server-derived data."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='aichat_proposals_list')
    def get(self, request):
        """Get."""
        try:
            _, scope_hash = _scope_strings(request.user)
            rows = proposal_service.list_owned_proposals(
                owner=request.user, scope_hash=scope_hash
            )
            return Response({'results': [_payload(row) for row in rows]})
        except proposal_service.ProposalError as exc:
            return _error(exc)

    def post(self, request):
        """Post."""
        data = request.data or {}
        action_type = str(data.get('action_type', ''))
        reason = str(data.get('reason', ''))
        idempotency_key = str(data.get('idempotency_key') or f'ui:{uuid.uuid4()}')[:128]
        try:
            work_order_id = int(data.get('work_order_id'))
        except (TypeError, ValueError):
            return Response(
                {'error': 'PROPOSAL_INVALID', 'detail': 'work_order_id required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Optional action parameters (schedule window, assignee, plan fields).
        # Untrusted display data: the service re-derives and the command
        # re-validates it at confirmation; here we only enforce the shape.
        intent = data.get('intent', {})
        if not isinstance(intent, dict):
            return Response(
                {'error': 'PROPOSAL_INVALID', 'detail': 'intent must be an object'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            scope_key, scope_hash = _scope_strings(request.user)
            proposal = proposal_service.create_proposal(
                owner=request.user,
                scope_key=scope_key,
                scope_hash=scope_hash,
                action_type=action_type,
                work_order_id=work_order_id,
                reason=reason,
                idempotency_key=idempotency_key,
                policy_version='ws7-v1',
                intent=intent,
                thread_id=str(data.get('thread_id', ''))[:255],
                source_turn_id=str(data.get('source_turn_id', ''))[:64],
            )
        except proposal_service.ProposalError as exc:
            return _error(exc)
        return Response(_payload(proposal), status=status.HTTP_201_CREATED)


class ProposalDetailView(APIView):
    """Owner-safe proposal detail."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, proposal_id):
        """Get."""
        try:
            scope_hash = _object_scope_hash(request.user)
            proposal = proposal_service.get_owned_proposal(
                owner=request.user, scope_hash=scope_hash, proposal_id=proposal_id
            )
        except proposal_service.ProposalError as exc:
            return _error(exc)
        return Response(_payload(proposal))


class ProposalConfirmView(APIView):
    """Execute one pending proposal through the canonical command service."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, proposal_id):
        """Post."""
        # Irreversible actions require an exact strict phrase (§5.3); the client
        # reads the required phrase from the proposal preview.
        confirm_phrase = str((request.data or {}).get('confirm_phrase', ''))
        try:
            scope_hash = _object_scope_hash(request.user)
            proposal = proposal_service.confirm_proposal(
                owner=request.user,
                scope_hash=scope_hash,
                proposal_id=proposal_id,
                confirm_phrase=confirm_phrase,
            )
        except proposal_service.ProposalError as exc:
            return _error(exc)
        return Response(_payload(proposal))


class ProposalRejectView(APIView):
    """Idempotently reject one pending proposal."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, proposal_id):
        """Post."""
        try:
            scope_hash = _object_scope_hash(request.user)
            proposal = proposal_service.reject_proposal(
                owner=request.user, scope_hash=scope_hash, proposal_id=proposal_id
            )
        except proposal_service.ProposalError as exc:
            return _error(exc)
        return Response(_payload(proposal))


class MessageFeedbackView(APIView):
    """Record the caller's rating of one assistant message in their thread."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    _STATUS = {
        'FEEDBACK_INVALID_RATING': status.HTTP_400_BAD_REQUEST,
        'FEEDBACK_THREAD_UNAVAILABLE': status.HTTP_404_NOT_FOUND,
        'FEEDBACK_MESSAGE_UNAVAILABLE': status.HTTP_404_NOT_FOUND,
    }

    def post(self, request):
        """Post."""
        from aichat.services import feedback as feedback_service

        data = request.data if isinstance(request.data, dict) else {}
        rating = str(data.get('rating', ''))
        try:
            if rating == 'none':
                # Retraction: the latest verdict is "no verdict".
                cleared = feedback_service.clear_feedback(
                    owner=request.user,
                    thread_id=str(data.get('thread_id', '')),
                    message_id=str(data.get('message_id', '')),
                    content_sha256=str(data.get('content_sha256', '')),
                )
                return Response({'rating': None, 'cleared': cleared})
            row = feedback_service.record_feedback(
                owner=request.user,
                thread_id=str(data.get('thread_id', '')),
                message_id=str(data.get('message_id', '')),
                rating=rating,
                reason=str(data.get('reason', '')),
                content_sha256=str(data.get('content_sha256', '')),
            )
        except feedback_service.FeedbackError as exc:
            return Response(
                {'error': exc.code, 'detail': str(exc)},
                status=self._STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
            )
        return Response({
            'message_id': row.message_id,
            'rating': row.rating,
            'updated_at': row.updated_at.isoformat(),
        })
