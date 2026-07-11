"""Permissions and throttle classes for the approvals API.

Implements:
- A-5: approvals.review permission for write endpoints
- D-4: Per-user rate limiting per spec §15
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.throttling import UserRateThrottle


class HasApprovalReviewPermission(BasePermission):
    """Require approvals.review permission for write operations.

    Read endpoints (GET, HEAD, OPTIONS) require only authentication.
    Write endpoints require the 'approvals.review' permission or superuser status.

    Spec reference: §15
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_superuser or request.user.has_perm('approvals.review')


class ApprovalCreationThrottle(UserRateThrottle):
    """Rate limit for approval creation: 20/min per agent/user. Spec §15."""

    rate = '20/min'
    scope = 'approval_creation'


class ApprovalDecisionThrottle(UserRateThrottle):
    """Rate limit for decision endpoints (approve/deny/cancel): 30/min per user. Spec §15."""

    rate = '30/min'
    scope = 'approval_decision'


class ApprovalReviseThrottle(UserRateThrottle):
    """Rate limit for revise endpoint: 20/min per user. Spec §15."""

    rate = '20/min'
    scope = 'approval_revise'


class ApprovalReadThrottle(UserRateThrottle):
    """Rate limit for read endpoints: 120/min per user. Spec §15."""

    rate = '120/min'
    scope = 'approval_read'
