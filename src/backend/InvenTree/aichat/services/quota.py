"""Quota policy assignment and validation (S12 Migration 4, Django plane).

Assignments are server-side, expiring, versioned, and auditable — managed
only under the dedicated ``aichat.assign_quota_policy`` permission. The AI
plane never imports this module on its hot path: it resolves policy through
``ai.core.quota.assignment_source`` (a lazy loader) and caches the snapshot.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from aichat.models import (
    AIQuotaAssignment,
    AIQuotaAuditAction,
    AIQuotaAuditEvent,
    AIQuotaPolicy,
    AIQuotaProfile,
)

logger = logging.getLogger(__name__)


class QuotaPolicyInvalid(ValueError):  # noqa: N818 - house style (ScopeRejected precedent)
    """A policy that must not be enforced (missing/zero-legged caps)."""


def _require_manager(actor) -> None:
    if actor is None or not actor.has_perm('aichat.assign_quota_policy'):
        raise PermissionDenied('quota policy management requires assign_quota_policy')


def create_policy(
    actor,
    *,
    profile: str,
    user_daily_tokens: int,
    tenant_daily_tokens: int,
    deployment_daily_tokens: int,
    requests_per_minute: int,
    requests_per_hour: int,
) -> AIQuotaPolicy:
    """Create the next version of ``profile``; audited, permission-checked."""
    _require_manager(actor)
    if profile not in AIQuotaProfile.values:
        raise QuotaPolicyInvalid('unknown quota profile')
    with transaction.atomic():
        latest = (
            AIQuotaPolicy.objects
            .filter(profile=profile)
            .order_by('-version')
            .values_list('version', flat=True)
            .first()
        )
        policy = AIQuotaPolicy.objects.create(
            profile=profile,
            version=(latest or 0) + 1,
            user_daily_tokens=user_daily_tokens,
            tenant_daily_tokens=tenant_daily_tokens,
            deployment_daily_tokens=deployment_daily_tokens,
            requests_per_minute=requests_per_minute,
            requests_per_hour=requests_per_hour,
            created_by=actor if getattr(actor, 'pk', None) else None,
        )
        AIQuotaAuditEvent.objects.create(
            action=AIQuotaAuditAction.POLICY_CREATED,
            actor=actor if getattr(actor, 'pk', None) else None,
            policy=policy,
            detail=f'{profile} v{policy.version}',
        )
    return policy


def assign_policy(
    actor, *, user, policy: AIQuotaPolicy, expires_at, reason: str = ''
) -> AIQuotaAssignment:
    """Assign ``policy`` to ``user`` until ``expires_at``; audited."""
    _require_manager(actor)
    if expires_at is None or expires_at <= timezone.now():
        raise QuotaPolicyInvalid('assignments must expire in the future')
    if not policy.active:
        raise QuotaPolicyInvalid('cannot assign an inactive policy')
    with transaction.atomic():
        assignment = AIQuotaAssignment.objects.create(
            user=user,
            policy=policy,
            expires_at=expires_at,
            reason=str(reason)[:255],
            assigned_by=actor if getattr(actor, 'pk', None) else None,
        )
        AIQuotaAuditEvent.objects.create(
            action=AIQuotaAuditAction.ASSIGNED,
            actor=actor if getattr(actor, 'pk', None) else None,
            target_user=user,
            policy=policy,
            detail=f'until {expires_at.isoformat()}',
        )
    return assignment


def revoke_assignment(
    actor, *, assignment: AIQuotaAssignment, reason: str = ''
) -> None:
    """Revoke one live assignment; audited."""
    _require_manager(actor)
    with transaction.atomic():
        assignment.revoked_at = timezone.now()
        assignment.save(update_fields=['revoked_at'])
        AIQuotaAuditEvent.objects.create(
            action=AIQuotaAuditAction.REVOKED,
            actor=actor if getattr(actor, 'pk', None) else None,
            target_user=assignment.user,
            policy=assignment.policy,
            detail=str(reason)[:255],
        )


def active_assignment(user_pk) -> AIQuotaAssignment | None:
    """The user's newest live (unexpired, unrevoked, active-policy) assignment."""
    try:
        return (
            AIQuotaAssignment.objects
            .filter(
                user_id=user_pk,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
                policy__active=True,
            )
            .select_related('policy')
            .order_by('-created_at')
            .first()
        )
    except Exception:
        logger.warning('quota assignment lookup failed', exc_info=False)
        return None


def validate_enforceable_policies() -> None:
    """Fail-closed gate for enforce mode: every active policy carries every cap.

    The model makes the caps non-null, so the residual risks are a zero-legged
    cap created by accident and rows written outside the service. Raising here
    keeps §8.9's rule — a missing level fails, it never inherits unlimited.
    """
    broken = [
        f'{policy.profile} v{policy.version}'
        for policy in AIQuotaPolicy.objects.filter(active=True)
        if not all((
            policy.user_daily_tokens,
            policy.tenant_daily_tokens,
            policy.deployment_daily_tokens,
            policy.requests_per_minute,
            policy.requests_per_hour,
        ))
    ]
    if broken:
        raise QuotaPolicyInvalid(f'unenforceable quota policies: {", ".join(broken)}')


__all__ = [
    'QuotaPolicyInvalid',
    'active_assignment',
    'assign_policy',
    'create_policy',
    'revoke_assignment',
    'validate_enforceable_policies',
]
