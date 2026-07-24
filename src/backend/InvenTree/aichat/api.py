"""Authenticated REST endpoints for chat proposals and scoped-chat governance.

Confirmation is a session-authenticated visual action on the normal Django
rail — CSRF-protected, owner-bound, and dispatching only the canonical
work-order command service. Voice, transcripts, and model output cannot
call these endpoints. Scoped conversations, citations, and tool calls are
equally owner-bound: the signed context token narrows, it never grants, and
every tool call re-authorizes the acting user against the pinned record.
"""

from __future__ import annotations

import hashlib
import uuid

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from aichat.models import ChatActionProposal, ScopedConversation
from aichat.services import context as context_service
from aichat.services import conversations as conversation_service
from aichat.services import proposals as proposal_service
from aichat.services import tools as tool_service


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


# ---------------------------------------------------------------------------
# Scoped chat (Feature #14): context resolution, conversations, citations,
# tool trace, and per-call-authorized tool invocation.
# ---------------------------------------------------------------------------

_SCOPED_ERROR_STATUS = {
    'CONTEXT_TYPE_UNKNOWN': status.HTTP_404_NOT_FOUND,
    'CONTEXT_FORBIDDEN': status.HTTP_404_NOT_FOUND,
    'CONTEXT_TOKEN_INVALID': status.HTTP_403_FORBIDDEN,
    'CONTEXT_TOKEN_EXPIRED': status.HTTP_409_CONFLICT,
    'CONVERSATION_NOT_FOUND': status.HTTP_404_NOT_FOUND,
    'CONVERSATION_READ_ONLY': status.HTTP_409_CONFLICT,
    'CONVERSATION_INVALID': status.HTTP_400_BAD_REQUEST,
    'TOOL_NOT_AVAILABLE': status.HTTP_404_NOT_FOUND,
    'TOOL_ARGUMENTS_INVALID': status.HTTP_400_BAD_REQUEST,
    'CHAT_RATE_LIMITED': status.HTTP_429_TOO_MANY_REQUESTS,
}


def _scoped_error(exc) -> Response:
    """Map a stable scoped-chat error code onto its HTTP status."""
    return Response(
        {'error': exc.code, 'detail': str(exc)},
        status=_SCOPED_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
    )


def _context_payload(context: context_service.ChatContext) -> dict:
    """Serialize a resolved context descriptor (the token is opaque)."""
    return {
        'context_type': context.context_type,
        'object_id': context.object_id,
        'display_label': context.display_label,
        'capabilities': list(context.capabilities),
        'source_revision': context.source_revision,
        'as_of': context.as_of.isoformat(),
        'snapshot': context.snapshot,
        'token': context.token,
        'expires_in_s': context_service.token_ttl_seconds(),
        'tools': list(tool_service.tools_for_context(context.context_type)),
    }


def _conversation_payload(
    conversation: ScopedConversation, *, context_state: str | None = None
) -> dict:
    """Serialize one governance row (never transcript content)."""
    payload = {
        'id': str(conversation.pk),
        'context_type': conversation.context_type,
        'object_id': conversation.object_id,
        'title': conversation.title,
        'status': conversation.status,
        'ai_thread_id': conversation.ai_thread_id,
        'last_context_revision': conversation.last_context_revision,
        'created_at': conversation.created_at.isoformat(),
        'updated_at': conversation.updated_at.isoformat(),
    }
    if context_state is not None:
        payload['context_state'] = context_state
    return payload


class ContextResolveView(APIView):
    """Resolve one record into a signed, short-lived scoped-chat context."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Resolve scope-safely and mint the context token."""
        data = request.data or {}
        try:
            context = context_service.resolve_context(
                request.user,
                context_type=str(data.get('context_type', '')),
                object_id=str(data.get('object_id', '')),
            )
        except context_service.ContextError as exc:
            return _scoped_error(exc)
        return Response(_context_payload(context))


class ConversationListCreateView(APIView):
    """List the actor's scoped conversations or open one from a valid token."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """List own conversations, optionally filtered to one record."""
        try:
            _, scope_hash = context_service.actor_scope_strings(request.user)
        except context_service.ContextError as exc:
            return _scoped_error(exc)
        rows = conversation_service.list_conversations(
            owner=request.user,
            scope_hash=scope_hash,
            context_type=request.query_params.get('context_type'),
            object_id=request.query_params.get('object_id'),
        )
        return Response({'results': [_conversation_payload(row) for row in rows]})

    def post(self, request):
        """Create a conversation after re-validating and re-resolving context."""
        data = request.data or {}
        token = str(data.get('token', ''))
        try:
            claims = context_service.validate_context_token(request.user, token)
            context = context_service.resolve_context(
                request.user,
                context_type=str(claims['context_type']),
                object_id=str(claims['object_id']),
            )
            conversation = conversation_service.create_conversation(
                owner=request.user,
                context=context,
                title=str(data.get('title', ''))[:255],
            )
        except context_service.ContextError as exc:
            return _scoped_error(exc)
        except conversation_service.ConversationError as exc:
            return _scoped_error(exc)
        return Response(
            _conversation_payload(conversation, context_state='authorized'),
            status=status.HTTP_201_CREATED,
        )


def _scoped_conversation(request, conversation_id) -> ScopedConversation:
    """Resolve one owned conversation fail-closed for the acting user."""
    try:
        _, scope_hash = context_service.actor_scope_strings(request.user)
    except context_service.ContextError as exc:
        raise conversation_service.ConversationNotFound('no such conversation') from exc
    return conversation_service.get_conversation(
        owner=request.user, scope_hash=scope_hash, conversation_id=conversation_id
    )


def _context_state(user, conversation: ScopedConversation) -> str:
    """Re-derive whether the pinned record is still authorized right now."""
    try:
        context_service.reauthorize_context(
            user,
            context_type=conversation.context_type,
            object_id=conversation.object_id,
        )
    except context_service.ContextError:
        return 'revoked'
    return 'authorized'


class ConversationDetailView(APIView):
    """Owner-safe conversation detail, rename/close, and tombstone delete."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        """Return the governance row plus the live context state."""
        try:
            conversation = _scoped_conversation(request, conversation_id)
        except conversation_service.ConversationError as exc:
            return _scoped_error(exc)
        return Response(
            _conversation_payload(
                conversation, context_state=_context_state(request.user, conversation)
            )
        )

    def patch(self, request, conversation_id):
        """Rename, or close via ``{"status": "closed"}``."""
        data = request.data or {}
        if data.get('status') == 'closed':
            operation = conversation_service.close_conversation
            extra = {}
        elif 'title' in data:
            operation = conversation_service.rename_conversation
            extra = {'title': str(data.get('title', ''))}
        else:
            return Response(
                {'error': 'CONVERSATION_INVALID', 'detail': 'nothing to update'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            _, scope_hash = context_service.actor_scope_strings(request.user)
            conversation = operation(
                owner=request.user,
                scope_hash=scope_hash,
                conversation_id=conversation_id,
                **extra,
            )
        except context_service.ContextError as exc:
            return _scoped_error(exc)
        except conversation_service.ConversationError as exc:
            return _scoped_error(exc)
        return Response(_conversation_payload(conversation))

    def delete(self, request, conversation_id):
        """Tombstone the row and delete the scoped transcript."""
        try:
            _, scope_hash = context_service.actor_scope_strings(request.user)
            conversation_service.delete_conversation(
                owner=request.user,
                scope_hash=scope_hash,
                conversation_id=conversation_id,
            )
        except context_service.ContextError as exc:
            return _scoped_error(exc)
        except conversation_service.ConversationError as exc:
            return _scoped_error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationCitationsView(APIView):
    """Citations for one conversation, re-filtered against current access."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        """Render citations; revoked access renders them as unavailable."""
        try:
            conversation = _scoped_conversation(request, conversation_id)
        except conversation_service.ConversationError as exc:
            return _scoped_error(exc)

        available = _context_state(request.user, conversation) == 'authorized'
        rows = conversation.citations.all()
        turn = request.query_params.get('turn')
        if turn:
            rows = rows.filter(turn_key=turn)

        results = []
        for row in rows:
            item = {
                'id': row.pk,
                'turn_key': row.turn_key,
                'source_type': row.source_type,
                'available': available,
                'as_of': row.as_of.isoformat(),
            }
            if available:
                item.update({
                    'source_id': row.source_id,
                    'source_revision': row.source_revision,
                    'locator': row.locator,
                    'excerpt_hash': row.excerpt_hash,
                })
            results.append(item)
        return Response({'results': results})


class ConversationToolTraceView(APIView):
    """Redacted tool-invocation trace for one owned conversation."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        """Return the audit trace (redacted arguments, never output)."""
        try:
            conversation = _scoped_conversation(request, conversation_id)
        except conversation_service.ConversationError as exc:
            return _scoped_error(exc)
        rows = conversation.tool_invocations.all()
        turn = request.query_params.get('turn')
        if turn:
            rows = rows.filter(turn_key=turn)
        return Response({
            'results': [
                {
                    'id': row.pk,
                    'turn_key': row.turn_key,
                    'tool': row.tool,
                    'tool_version': row.tool_version,
                    'arguments': row.arguments_redacted,
                    'authorization_result': row.authorization_result,
                    'duration_ms': row.duration_ms,
                    'created_at': row.created_at.isoformat(),
                }
                for row in rows
            ]
        })


class ConversationToolInvokeView(APIView):
    """Execute one read-only scoped tool with full per-call authorization."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id):
        """Validate the token binding, then invoke the typed tool."""
        data = request.data or {}
        try:
            conversation = _scoped_conversation(request, conversation_id)
        except conversation_service.ConversationError as exc:
            return _scoped_error(exc)
        try:
            context_service.validate_context_token(
                request.user,
                str(data.get('token', '')),
                expected_type=conversation.context_type,
                expected_object_id=conversation.object_id,
            )
        except context_service.ContextError as exc:
            return _scoped_error(exc)

        arguments = data.get('arguments')
        if arguments is not None and not isinstance(arguments, dict):
            return Response(
                {
                    'error': 'TOOL_ARGUMENTS_INVALID',
                    'detail': 'arguments must be an object',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            envelope = tool_service.invoke_tool(
                user=request.user,
                conversation=conversation,
                tool_name=str(data.get('tool', '')),
                arguments=arguments,
                turn_key=str(data.get('turn_key', '')),
            )
        except tool_service.ToolError as exc:
            return _scoped_error(exc)
        return Response(envelope)
