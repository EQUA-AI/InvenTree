"""Permissions and throttle classes for the approvals API.

Implements:
- A-5: approvals.review permission for write endpoints
- D-4: Per-user rate limiting per spec §15
"""

from django.conf import settings

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.throttling import UserRateThrottle


class ApprovalRateThrottle(UserRateThrottle):
    """Base throttle for the approvals API - disabled while running tests."""

    def allow_request(self, request, view):
        """Bypass rate limiting entirely in test mode."""
        if settings.TESTING:
            return True
        return super().allow_request(request, view)


class HasApprovalReviewPermission(BasePermission):
    """Require approvals.review permission for write operations.

    Read endpoints (GET, HEAD, OPTIONS) require only authentication.
    Write endpoints require the 'approvals.review' permission or superuser status.

    Spec reference: §15
    """

    def has_permission(self, request, view):
        """Allow authenticated reads; require review permission for writes."""
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_superuser or request.user.has_perm('approvals.review')


class ApprovalCreationThrottle(ApprovalRateThrottle):
    """Rate limit for approval creation: 20/min per agent/user. Spec §15."""

    rate = '20/min'
    scope = 'approval_creation'


class ApprovalDecisionThrottle(ApprovalRateThrottle):
    """Rate limit for decision endpoints (approve/deny/cancel): 30/min per user. Spec §15."""

    rate = '30/min'
    scope = 'approval_decision'


class ApprovalReviseThrottle(ApprovalRateThrottle):
    """Rate limit for revise endpoint: 20/min per user. Spec §15."""

    rate = '20/min'
    scope = 'approval_revise'


class ApprovalReadThrottle(ApprovalRateThrottle):
    """Rate limit for read endpoints: 120/min per user. Spec §15."""

    rate = '120/min'
    scope = 'approval_read'
