"""Session-authenticated consent controls for the memory settings page."""

from django.db import transaction
from django.utils.decorators import method_decorator

from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from aichat.services.memory_controls import NOTICE_COPY, memory_status
from aichat.services.memory_eligibility import acknowledge_notice
from aichat.services.memory_lifecycle import set_opt_out


class MemorySettingsView(APIView):
    """Current notice and choice; no content, implicit consent or enrollment."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        """Keep consent state out of browser/proxy caches."""
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response

    def get(self, request):
        """Read the authenticated owner's status."""
        try:
            return Response(memory_status(request.user))
        except ValueError:
            return Response({'error': 'memory_controls_unavailable'}, status=409)


class MemoryNoticeView(MemorySettingsView):
    """Acknowledge only the exact current, displayed version."""

    def post(self, request):
        """An explicit session/CSRF protected action supplies the version."""
        version = request.data.get('notice_version')
        if not isinstance(version, str) or version not in NOTICE_COPY:
            return Response({'error': 'memory_notice_unavailable'}, status=400)
        try:
            row = acknowledge_notice(request.user, version)
        except ValueError:
            return Response({'error': 'memory_notice_unavailable'}, status=409)
        return Response({'notice_version': row.notice_version, 'acknowledged': True})


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class MemoryOptOutView(MemorySettingsView):
    """Commit withdrawal before attempting its retryable cleanup pass."""

    def put(self, request):
        """Only a JSON boolean changes the owner's choice."""
        value = request.data.get('opted_out')
        if type(value) is not bool:
            return Response({'error': 'invalid_opt_out'}, status=400)
        try:
            result = set_opt_out(request.user, opted_out=value)
        except ValueError:
            return Response({'error': 'memory_controls_unavailable'}, status=409)
        return Response(
            result, status=202 if result['status'] == 'purge_incomplete' else 200
        )


class MemoryProposalListView(MemorySettingsView):
    """Prepare actions only from already governed facts and server-owned scope."""

    def get(self, request):
        """List current accessible previews; another rail's scope is never used."""
        from aichat.api import _error, _payload
        from aichat.models import ChatActionProposal
        from aichat.services import memory_commands, proposals

        try:
            owner = memory_commands.require_permission(request.user)
            _, scope_hash = memory_commands.owner_scope(owner)
            rows = ChatActionProposal.objects.filter(
                owner=owner, scope_hash=scope_hash
            )[:100]
            visible = []
            for proposal in rows:
                try:
                    memory_commands.authorize_preview(owner, proposal)
                except proposals.ProposalNotFound:
                    continue
                visible.append(_payload(proposal))
            return Response({'results': visible})
        except proposals.ProposalError as exc:
            return _error(exc)

    def post(self, request):
        """No candidate text, provider verdict or confirmation comes from a model."""
        from django.utils import timezone

        from aichat.api import _error, _payload
        from aichat.models import ChatActionProposal
        from aichat.services import memory_commands, proposals

        data = request.data
        if not isinstance(data, dict) or set(data) - {
            'action_type',
            'memory_fact_id',
            'expected_version',
            'topics',
            'replacement_fact_id',
            'idempotency_key',
        }:
            return Response({'error': 'invalid_memory_action'}, status=400)
        action, key = data.get('action_type'), data.get('idempotency_key')
        if (
            not isinstance(action, str)
            or action not in memory_commands.ACTIONS
            or not isinstance(key, str)
            or not 1 <= len(key) <= 128
        ):
            return Response({'error': 'invalid_memory_action'}, status=400)
        try:
            owner = memory_commands.require_permission(request.user)
            scope_key, scope_hash = memory_commands.owner_scope(owner)
            intent = {
                name: value
                for name, value in data.items()
                if name not in {'action_type', 'idempotency_key'}
            }
            if action == 'memory.forget_all':
                if intent:
                    return Response({'error': 'invalid_memory_action'}, status=400)
                existing = ChatActionProposal.objects.filter(
                    owner=owner,
                    idempotency_key=key,
                    action_type=action,
                    scope_hash=scope_hash,
                ).first()
                intent = (
                    existing.intent
                    if existing
                    else {'before': timezone.now().isoformat()}
                )
            proposal = proposals.create_proposal(
                owner=owner,
                scope_key=scope_key,
                scope_hash=scope_hash,
                action_type=action,
                work_order_id=None,
                reason='',
                idempotency_key=key,
                policy_version='memory-actions-v1',
                intent=intent,
            )
            return Response(_payload(proposal), status=201)
        except proposals.ProposalError as exc:
            return _error(exc)


class MemoryProposalDecisionView(MemorySettingsView):
    """Session/CSRF protected visual confirmation or rejection, one fact at a time."""

    def post(self, request, proposal_id):
        """Route both decisions through the shared canonical proposal service."""
        from aichat.api import _error, _payload
        from aichat.services import memory_commands, proposals

        data = request.data
        if not isinstance(data, dict) or set(data) - {
            'decision',
            'expected_preview_hash',
            'confirm_phrase',
        }:
            return Response({'error': 'invalid_memory_decision'}, status=400)
        if not isinstance(data.get('confirm_phrase', ''), str):
            return Response({'error': 'invalid_memory_decision'}, status=400)
        try:
            owner = memory_commands.require_permission(request.user)
            _, scope_hash = memory_commands.owner_scope(owner)
            if data.get('decision') == 'confirm':
                proposal = proposals.confirm_proposal(
                    owner=owner,
                    scope_hash=scope_hash,
                    proposal_id=proposal_id,
                    expected_preview_hash=data.get('expected_preview_hash'),
                    confirm_phrase=data.get('confirm_phrase', ''),
                )
            elif data.get('decision') == 'reject':
                proposal = proposals.reject_proposal(
                    owner=owner, scope_hash=scope_hash, proposal_id=proposal_id
                )
            else:
                return Response({'error': 'invalid_memory_decision'}, status=400)
            return Response(_payload(proposal))
        except proposals.ProposalError as exc:
            return _error(exc)


class MemoryFactListView(MemorySettingsView):
    """Paged inspection and export share the exact current authorization path."""

    def get(self, request):
        """Return bounded plain records with an opaque, owner-bound continuation."""
        from aichat.services.memory_reads import list_facts

        if set(request.query_params) - {'state', 'memory_type', 'topic', 'cursor'}:
            return Response({'error': 'invalid_memory_selection'}, status=400)
        try:
            return Response(list_facts(request.user, **request.query_params.dict()))
        except ValueError:
            return Response({'error': 'memory_list_unavailable'}, status=400)
