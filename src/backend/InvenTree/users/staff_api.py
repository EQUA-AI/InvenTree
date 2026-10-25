"""Narrow occupational profile and assignment API adapters."""

import uuid

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers, status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import Trade
from users.permissions import check_user_role
from users.serializers import OccupationalProfileSerializer, TradeSerializer
from users.staff_directory import directory_rows, require_directory


def require_profile_admin(actor):
    """Directory read access must never grant account administration."""
    if not actor.is_active or not (
        actor.is_superuser
        or (actor.is_staff and check_user_role(actor, 'admin', 'change'))
    ):
        raise PermissionDenied(
            _('Administrator user-management permission is required.')
        )


class StaffPagination(LimitOffsetPagination):
    """Always bound personnel and catalogue collections."""

    default_limit = 50
    max_limit = 100


class TradeList(ListCreateAPIView):
    """Manage the small occupational trade catalogue."""

    permission_classes = [IsAuthenticated]
    serializer_class = TradeSerializer
    pagination_class = StaffPagination
    queryset = Trade.objects.all()

    def initial(self, request, *args, **kwargs):
        """Catalogue modification is administrator-only."""
        super().initial(request, *args, **kwargs)
        if request.method in {'GET', 'HEAD', 'OPTIONS'}:
            require_directory(request.user)
        else:
            require_profile_admin(request.user)


class TradeDetail(RetrieveUpdateAPIView):
    """Rename or deactivate a trade without erasing assignment history."""

    permission_classes = [IsAuthenticated]
    serializer_class = TradeSerializer
    queryset = Trade.objects.all()

    def initial(self, request, *args, **kwargs):
        """Require occupational administration for edits."""
        super().initial(request, *args, **kwargs)
        require_profile_admin(request.user)


class OccupationalProfileDetail(RetrieveUpdateAPIView):
    """Only position and approved trades can be changed through this adapter."""

    permission_classes = [IsAuthenticated]
    serializer_class = OccupationalProfileSerializer

    def initial(self, request, *args, **kwargs):
        """Check administrator authority even for schema/OPTIONS access."""
        super().initial(request, *args, **kwargs)
        require_profile_admin(request.user)

    def get_object(self):
        """Resolve one account, without making all profile fields writable."""
        return get_object_or_404(User, pk=self.kwargs['pk']).profile

    @transaction.atomic
    def perform_update(self, serializer):
        """Attribute administrator-managed changes using the existing admin log."""
        from django.contrib.admin.models import CHANGE, LogEntry

        profile = serializer.save()
        LogEntry.objects.log_actions(
            user_id=self.request.user.pk,
            queryset=User.objects.filter(pk=profile.user_id),
            action_flag=CHANGE,
            change_message=[{'changed': {'fields': list(serializer.validated_data)}}],
        )


class StaffQuerySerializer(serializers.Serializer):
    """Reject invalid selectors rather than silently ignoring malformed input."""

    machine_id = serializers.IntegerField(min_value=1, required=False)
    work_order_id = serializers.IntegerField(min_value=1, required=False)
    user_id = serializers.IntegerField(min_value=1, required=False)
    kanban_card_id = serializers.IntegerField(min_value=1, required=False)
    trade = serializers.CharField(max_length=64, required=False)
    search = serializers.CharField(max_length=255, required=False, allow_blank=True)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=50)
    offset = serializers.IntegerField(min_value=0, default=0)

    def validate(self, attrs):
        """A query refers to at most one assignment target."""
        if attrs.get('machine_id') and attrs.get('work_order_id'):
            raise serializers.ValidationError(
                'Choose a machine or work order, not both.'
            )
        if attrs.get('kanban_card_id') and not attrs.get('work_order_id'):
            raise serializers.ValidationError(
                'A board task lookup requires a work order.'
            )
        return attrs


class StaffDirectory(APIView):
    """Minimal scoped directory, shared with the AI read tool."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """List names, job titles and approved trades inside caller scope."""
        require_directory(request.user)
        query = StaffQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(directory_rows(request.user, **query.validated_data))


class StaffAssignmentSerializer(serializers.Serializer):
    """Explicit human assignment intent, never an automated override switch."""

    work_order_id = serializers.IntegerField(min_value=1)
    kanban_card_id = serializers.IntegerField(min_value=1, required=False)
    assigned_to = serializers.IntegerField(min_value=1, allow_null=True)
    expected_version = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.CharField(max_length=128)
    reason = serializers.CharField(max_length=1000, allow_blank=True, default='')
    override_reason = serializers.CharField(
        max_length=1000, allow_blank=True, default=''
    )
    required_trades = serializers.ListField(
        child=serializers.CharField(max_length=64), max_length=20, required=False
    )
    trade_requirements_confirmed = serializers.BooleanField(required=False)


class StaffAssignment(APIView):
    """Always-available personnel command for the maintenance workspace."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Apply an explicit human selection through the audited domain command."""
        from dataclasses import asdict

        from tasks.models import WorkOrder
        from tasks.scope import ScopeError, require_work_order_scope
        from tasks.services.work_orders import WorkOrderCommandError, assign_work_order

        require_directory(request.user)
        intent = StaffAssignmentSerializer(data=request.data)
        intent.is_valid(raise_exception=True)
        values = dict(intent.validated_data)
        work = get_object_or_404(
            WorkOrder.objects.select_related('machine'), pk=values['work_order_id']
        )
        try:
            require_work_order_scope(request.user, work)
        except ScopeError as exc:
            raise Http404 from exc
        correlation_id = uuid.uuid4()
        try:
            result = assign_work_order(
                actor=request.user,
                allow_override=True,
                correlation_id=correlation_id,
                **values,
            )
        except WorkOrderCommandError as exc:
            work.refresh_from_db(fields=['lifecycle_version'])
            return Response(
                {
                    'code': getattr(exc, 'code', 'ASSIGNMENT_INVALID'),
                    'detail': str(exc),
                    'correlation_id': correlation_id,
                    'current_version': work.lifecycle_version,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(asdict(result))
