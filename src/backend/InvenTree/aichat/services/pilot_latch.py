"""Pilot-stop latch service (S15, §14/§15.4/§16, Q43/Q50).

The Django-plane authority for the durable latch: engage (any one owner,
or an automatic Q50 trigger), record restart approvals (clearing requires
ALL FIVE roles), and read state. The AI plane's fail-closed admission
gate (``ai.core.pilot_latch``) consumes :func:`current_state` through a
cached loader; :func:`engage_latch` also writes the shared cache directly
so an automatic stop propagates without waiting out the TTL.

Alerting is honest about what exists in this codebase: a targeted
InvenTree notification to the five named owners (UI + email where the
deployment configures email), a CRITICAL structured log, and latch
visibility on ``/quota/preflight`` and in ``pilot_ops_report``. There is
no pager integration; that limitation is an owner-ack item.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger('inventree')

#: Shared-cache key the AI-plane gate reads (value: the reason code).
LATCH_CACHE_KEY = 'aimms:pilot:latch:v1'
#: Long TTL — the DB row is the truth; the cache only accelerates reads.
LATCH_CACHE_TTL_S = 24 * 3600

NOTIFICATION_CATEGORY = 'aichat.pilot_stop'


def _roles() -> list[str]:
    from aichat.models import AIPilotStopRole

    return [choice[0] for choice in AIPilotStopRole.choices]


def current_state() -> dict[str, Any]:
    """The latch state, shaped for gates, reports, and preflight."""
    from aichat.models import AIPilotStopLatch

    latch = AIPilotStopLatch.objects.filter(active=True).first()
    if latch is None:
        return {
            'latched': False,
            'reason_code': '',
            'engaged_at': None,
            'approvals': [],
            'missing_roles': [],
        }
    approvals = sorted(latch.approvals.values_list('role', flat=True))
    return {
        'latched': True,
        'reason_code': latch.reason_code,
        'engaged_at': latch.engaged_at.isoformat(),
        'approvals': approvals,
        'missing_roles': [role for role in _roles() if role not in approvals],
    }


def _cache():
    from django.core.cache import cache

    return cache


def engage_latch(
    *,
    reason_code: str,
    source: str = 'manual',
    engaged_by=None,
    engaged_role: str = '',
    detail: str = '',
) -> Any:
    """Set the latch (idempotent). Any single owner or trigger suffices.

    Returns the active latch row. A second engage while latched returns
    the existing episode — one active row keeps the five-approval
    arithmetic unambiguous — and logs the repeat at WARNING.
    """
    from django.db import IntegrityError

    from aichat.models import AIPilotStopLatch

    existing = AIPilotStopLatch.objects.filter(active=True).first()
    if existing is not None:
        logger.warning(
            'pilot latch already engaged (%s); repeat stop %s recorded in log only',
            existing.reason_code,
            reason_code,
        )
        return existing
    try:
        latch = AIPilotStopLatch.objects.create(
            reason_code=reason_code,
            source=source,
            engaged_by=engaged_by,
            engaged_role=engaged_role,
            detail=detail[:255],
        )
    except IntegrityError:
        # A concurrent engage won the single-active constraint.
        return AIPilotStopLatch.objects.filter(active=True).first()
    try:
        _cache().set(LATCH_CACHE_KEY, reason_code, LATCH_CACHE_TTL_S)
    except Exception:  # pragma: no cover - cache outage must not mask the stop
        logger.warning('pilot latch cache write failed; DB row is authoritative')
    _alert_owners(latch)
    logger.critical('PILOT STOP LATCH ENGAGED reason=%s source=%s', reason_code, source)
    return latch


def record_resume_approval(
    *, role: str, approved_by, reference: str = ''
) -> dict[str, Any]:
    """Record one role's restart approval; the fifth distinct role clears.

    Raises ``ValueError`` when nothing is latched or the role is unknown;
    a repeated approval for the same role is idempotent.
    """
    from django.utils import timezone as django_timezone

    from aichat.models import AIPilotStopApproval, AIPilotStopLatch

    if role not in _roles():
        raise ValueError(f'unknown stop-authority role {role!r}')
    latch = AIPilotStopLatch.objects.filter(active=True).first()
    if latch is None:
        raise ValueError('no active pilot-stop latch')
    AIPilotStopApproval.objects.get_or_create(
        latch=latch,
        role=role,
        defaults={'approved_by': approved_by, 'reference': reference[:100]},
    )
    state = current_state()
    if not state['missing_roles']:
        latch.active = False
        latch.cleared_at = django_timezone.now()
        latch.save(update_fields=['active', 'cleared_at'])
        try:
            _cache().delete(LATCH_CACHE_KEY)
        except Exception:  # pragma: no cover
            pass
        logger.info('pilot latch CLEARED with all five approvals')
        state = current_state()
        state['cleared'] = True
    return state


def _alert_owners(latch) -> None:
    """Notify the five named owners; fail soft (the latch already holds)."""
    try:
        from django.conf import settings as django_settings
        from django.contrib.auth import get_user_model

        targets = []
        pairs = getattr(django_settings, 'AIMMS_PILOT_STOP_OWNERS', None) or []
        usernames = [pair.split(':', 1)[1] for pair in pairs if ':' in pair]
        if usernames:
            targets = list(get_user_model().objects.filter(username__in=usernames))
        if not targets:
            # Fallback: everyone holding the manage permission.
            targets = list(
                get_user_model().objects.filter(
                    user_permissions__codename='manage_pilot_stop'
                )
            )
        if not targets:
            logger.critical(
                'pilot latch has NO notifiable owners; set AIMMS_PILOT_STOP_OWNERS'
            )
            return
        from common.notifications import trigger_notification

        trigger_notification(
            latch,
            category=NOTIFICATION_CATEGORY,
            targets=targets,
            check_recent=False,
            context={
                'name': 'AI pilot stopped',
                'message': (
                    f'The AI pilot-stop latch is engaged '
                    f'(reason code: {latch.reason_code}). New pilot turns are '
                    f'blocked; clearing requires all five stop-authority '
                    f'approvals via manage.py pilot_resume.'
                ),
            },
        )
    except Exception:  # pragma: no cover - alerting must never mask the stop
        logger.critical(
            'pilot latch owner notification FAILED (latch holds regardless)',
            exc_info=True,
        )


__all__ = [
    'LATCH_CACHE_KEY',
    'LATCH_CACHE_TTL_S',
    'NOTIFICATION_CATEGORY',
    'current_state',
    'engage_latch',
    'record_resume_approval',
]
