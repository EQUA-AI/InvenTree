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
