"""Scoped read/draft endpoints for the native work-package creation form."""

from django.core.exceptions import PermissionDenied

from rest_framework import serializers
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from tasks.scope import scoped_machines
from tasks.workorder_mutations import object_data

import InvenTree.permissions

from .intake_binding import IntakeUnavailableError
from .work_intake import assignable_staff, intake_context, make_ai_draft
from .work_packages import WorkPackageError


class IntakeInput(serializers.Serializer):
    """Only stable source identities cross the drafting boundary."""

    machine_id = serializers.IntegerField(min_value=1)
    anomaly_id = serializers.IntegerField(min_value=1)


class IntakeDraftThrottle(UserRateThrottle):
    """Bound the separate REST drafting lane, including unsuccessful calls."""

    rate = '2/minute'
    scope = 'work_intake_draft'


class WorkIntake(APIView):
    """Read facts and prepare text; never create, approve or start Work."""

    permission_classes = [
        InvenTree.permissions.IsAuthenticatedOrReadScope,
        InvenTree.permissions.RolePermission,
    ]
    role_required = 'work_order.add'

    def _source(self, request, data):
        serializer = IntakeInput(data=data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        machine = get_object_or_404(
            scoped_machines(request.user).select_related('parent', 'client'),
            pk=values['machine_id'],
        )
        return machine, values['anomaly_id']

    def get(self, request):
        """Return current source, duplicate and provider facts without inference."""
        machine, anomaly_id = self._source(request, request.query_params)
        try:
            result = intake_context(request.user, machine, anomaly_id)
        except WorkPackageError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)
        response = Response(result)
        response['Cache-Control'] = 'no-store'
        return response


class WorkIntakeDraft(WorkIntake):
    """A bounded explicitly enabled model request producing only a review draft."""

    throttle_classes = [IntakeDraftThrottle]

    def post(self, request):
        """Generate whitelisted form text, preserving actual provider provenance."""
        machine, anomaly_id = self._source(request, object_data(request.data))
        try:
            result = make_ai_draft(request.user, machine, anomaly_id)
        except WorkPackageError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)
        except IntakeUnavailableError as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=503)
        response = Response(result)
        response['Cache-Control'] = 'no-store'
        return response


class WorkIntakeAssignees(WorkIntake):
    """Minimal active executable personnel inside the target machine scope."""

    def get(self, request):
        """Return names only; a directory listing is not qualification evidence."""
        serializer = serializers.IntegerField(min_value=1)
        machine_id = serializer.run_validation(request.query_params.get('machine_id'))
        machine = get_object_or_404(scoped_machines(request.user), pk=machine_id)
        try:
            result = assignable_staff(
                request.user,
                machine,
                request.query_params.get('search', '')[:100],
                exact_username=serializers.BooleanField().run_validation(
                    request.query_params.get('exact_username', False)
                ),
            )
        except PermissionDenied as exc:
            return Response({'detail': str(exc)}, status=403)
        response = Response({'results': result})
        response['Cache-Control'] = 'no-store'
        return response
