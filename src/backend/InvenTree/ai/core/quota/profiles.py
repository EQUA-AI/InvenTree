"""Cached quota-policy resolution for the AI-plane hot path (S12).

Per §8.9: cache only policy RESOLUTION — the durable assignment/audit rows
stay the source of truth in ``aichat``. The hot path (budget check inside
``asyncio.to_thread``) reads one cache key; a miss runs the lazy loader once
per TTL. A loader failure raises ``QuotaSourceUnavailable`` and the CALLER
applies the posture: shadow fails open, enforce returns the typed 503
(``quota_store_unavailable``) — resolution itself never decides.
"""

from __future__ import annotations

import logging

from ai.core.quota.assignment_source import PolicySnapshot, load_assignment

logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "aimms:quota:policy:v2"
#: Sentinel cached for "no assignment" so absent rows don't re-query per turn.
_NO_ASSIGNMENT = "none"


class QuotaSourceUnavailable(Exception):
    """Neither the cache nor the durable store could resolve a policy."""


def standard_snapshot() -> PolicySnapshot:
    """The built-in default: the v1 budget/rate limits as a policy.

    Mirrors ``ai_user_daily_token_budget`` (1M/day) and the standard chat
    route windows (the ai_rate_chat_* knobs) so flipping ``FEATURE_AI_QUOTA_PROFILES``
    without any policy rows changes no user-visible limit.
    """
    from ai.core.config import get_settings

    settings = get_settings()
    user_cap = int(getattr(settings, "ai_user_daily_token_budget", 1_000_000))
    return PolicySnapshot(
        profile="standard",
        version=0,
        user_cap=user_cap,
        # §8.9: tenant/deployment ceilings sized for the six-user pilot
        # envelope — finite, high enough to be invisible in normal use.
        tenant_cap=user_cap * 10 if user_cap else 0,
        global_cap=user_cap * 10 if user_cap else 0,
        requests_per_minute=int(getattr(settings, "ai_rate_chat_per_minute", 10)),
        requests_per_hour=int(getattr(settings, "ai_rate_chat_per_hour", 100)),
    )


def _cache_key(user_pk) -> str:
    return f"{_CACHE_KEY_PREFIX}:{user_pk}"


def resolve_profile(user_pk) -> PolicySnapshot:
    """The user's effective policy snapshot (cache -> loader -> default).

    Raises ``QuotaSourceUnavailable`` only when BOTH the cache read and the
    durable loader fail — an absent assignment is not a failure, it is the
    standard profile.
    """
    from django.core.cache import cache

    key = _cache_key(user_pk)
    cache_error = False
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
        cache_error = True
    if isinstance(cached, PolicySnapshot):
        return cached
    if cached == _NO_ASSIGNMENT:
        return standard_snapshot()

    try:
        snapshot = load_assignment(user_pk)
    except Exception as exc:
        if cache_error:
            raise QuotaSourceUnavailable(str(exc)) from exc
        # Cache is healthy but the durable store is not: same posture —
        # the caller decides shadow vs enforce.
        raise QuotaSourceUnavailable(str(exc)) from exc

    from ai.core.config import get_settings

    ttl = int(getattr(get_settings(), "ai_quota_policy_cache_ttl_s", 60))
    try:
        cache.set(key, snapshot if snapshot is not None else _NO_ASSIGNMENT, timeout=ttl)
    except Exception:
        logger.debug("quota policy cache write failed", exc_info=False)
    return snapshot if snapshot is not None else standard_snapshot()


def invalidate_profile(user_pk) -> None:
    """Drop the cached resolution (called after assign/revoke)."""
    from django.core.cache import cache

    try:
        cache.delete(_cache_key(user_pk))
    except Exception:
        logger.debug("quota policy cache delete failed", exc_info=False)


__all__ = [
    "PolicySnapshot",
    "QuotaSourceUnavailable",
    "invalidate_profile",
    "resolve_profile",
    "standard_snapshot",
]
