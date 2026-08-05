"""Admin surface for the closeout permission set.

The closeout permissions (``capture_closeout`` and siblings) are additive
Django ``Meta`` permissions on :class:`~tasks.closeout_models.CloseoutCapture`.
They are deliberately OUTSIDE the generic role/ruleset table
(``users/ruleset.py`` exempts the EQUA fork apps), which meant the platform UI
had no way to grant them - the only path was the Django admin. This module
gives the frontend a narrow, staff-gated surface that manages EXACTLY this
permission set and nothing else: the codename allowlist comes from the model
``Meta`` itself, so nothing reachable here can grant an unrelated permission.

Grants are DIRECT user permissions. Group-conferred grants are reported but
never modified from here - editing a group fans out to every member and
belongs in the group admin, not a per-user panel.
"""

from __future__ import annotations

import json
import logging

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from users.permissions import check_user_role

from .closeout_models import CloseoutCapture

logger = logging.getLogger('inventree')


def closeout_permission_catalog() -> list[tuple[str, str]]:
    """The closeout permission set, read from the model Meta (single source)."""
    return list(CloseoutCapture._meta.permissions)


def _catalog_codenames() -> set[str]:
    return {codename for codename, _label in closeout_permission_catalog()}


class IsStaffWithAdminRole(permissions.BasePermission):
    """Staff-only surface; mutation additionally requires the ``admin`` role.

    Reads are staff-gated too: which colleagues hold closeout authority is
    administrative information, not general-user material.
    """

    def has_permission(self, request, view):
        """Staff may read; staff with the admin role (or superuser) may write."""
        user = request.user
        if not (user and user.is_authenticated and user.is_staff):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(user.is_superuser or check_user_role(user, 'admin', 'change'))


def _permission_rows(target) -> list[dict]:
    """Serialize the closeout permission state for one user."""
    content_type = ContentType.objects.get_for_model(CloseoutCapture)
    catalog = closeout_permission_catalog()
    direct = set(
        target.user_permissions.filter(content_type=content_type).values_list(
            'codename', flat=True
        )
    )
    via_groups: dict[str, list[str]] = {}
    group_rows = Permission.objects.filter(
        content_type=content_type, group__user=target
    ).values_list('codename', 'group__name')
    for codename, group_name in group_rows:
        via_groups.setdefault(codename, []).append(group_name)

    rows = []
    for codename, label in catalog:
        rows.append({
            'codename': codename,
            'name': str(label),
            'granted_direct': codename in direct,
            'via_groups': sorted(via_groups.get(codename, [])),
            'effective': bool(
                target.is_superuser or codename in direct or codename in via_groups
            ),
        })
    return rows


class CloseoutPermissionDetail(APIView):
    """Read and edit one user's closeout permission grants."""

    permission_classes = [IsStaffWithAdminRole]

    def get(self, request, user_pk):
        """Return the closeout permission state for the target user."""
        target = get_object_or_404(get_user_model(), pk=user_pk)
        return Response({
            'user': target.pk,
            'username': target.get_username(),
            'is_superuser': target.is_superuser,
            'permissions': _permission_rows(target),
        })

    def post(self, request, user_pk):
        """Grant or revoke one DIRECT closeout permission for the target user."""
        target = get_object_or_404(get_user_model(), pk=user_pk)
        codename = str(request.data.get('codename') or '')
        granted = request.data.get('granted')

        if codename not in _catalog_codenames():
            return Response(
                {'detail': f'Unknown closeout permission: {codename!r}'}, status=400
            )
        if not isinstance(granted, bool):
            return Response({'detail': "'granted' must be a boolean"}, status=400)

        content_type = ContentType.objects.get_for_model(CloseoutCapture)
        permission = Permission.objects.get(
            content_type=content_type, codename=codename
        )

        already = target.user_permissions.filter(pk=permission.pk).exists()
        changed = already != granted
        if changed:
            if granted:
                target.user_permissions.add(permission)
            else:
                target.user_permissions.remove(permission)

            # Auditable trail on the existing admin history plus the app log:
            # permission changes on a safety surface must be attributable.
            LogEntry.objects.log_actions(
                user_id=request.user.pk,
                queryset=[target],
                action_flag=CHANGE,
                change_message=json.dumps([
                    {
                        'changed': {
                            'name': 'closeout permission',
                            'object': codename,
                            'fields': ['granted' if granted else 'revoked'],
                        }
                    }
                ]),
                single_object=True,
            )
            logger.warning(
                'Closeout permission %s: %s for user %s by %s',
                'granted' if granted else 'revoked',
                codename,
                target.get_username(),
                request.user.get_username(),
            )

        return Response({
            'user': target.pk,
            'username': target.get_username(),
            'is_superuser': target.is_superuser,
            'changed': changed,
            'permissions': _permission_rows(target),
        })
