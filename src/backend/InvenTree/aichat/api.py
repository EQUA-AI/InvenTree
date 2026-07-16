"""Authenticated REST endpoints for governed chat action proposals (WS7).

Confirmation is a session-authenticated visual action on the normal Django
rail — CSRF-protected, owner-bound, and dispatching only the canonical
work-order command service. Voice, transcripts, and model output cannot
call these endpoints.
"""

from __future__ import annotations

import hashlib
import uuid

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
}


def _error(exc: proposal_service.ProposalError) -> Response:
    """Error."""
    return Response(
        {'error': exc.code, 'detail': str(exc)},
        status=_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
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
        try:
            scope_hash = _object_scope_hash(request.user)
            proposal = proposal_service.confirm_proposal(
                owner=request.user, scope_hash=scope_hash, proposal_id=proposal_id
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
