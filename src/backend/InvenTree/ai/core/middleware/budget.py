"""Per-user daily token budgets over the shared cache (S37).

Counters live at ``aimms:budget:v1:{user_pk}:{yyyymmdd}`` (UTC day), seeded
with ``cache.add`` and advanced with atomic ``cache.incr`` — the same
fixed-window shape as the S35 rate-limit store. The increment happens once
per turn at terminal (from the ledger's canonical totals); the check happens
pre-turn in the rate-limit middleware, one cache GET.

Budgets are an abuse control, not billing: a cache flush resetting the day's
counter is documented-acceptable, and cache errors fail OPEN with a fault
log (the S35 ADR posture). Rollout ladder: ``FEATURE_TOKEN_BUDGET_SHADOW``
(default on) logs ``budget.would_block``; ``FEATURE_TOKEN_BUDGET_ENFORCE``
(default off) turns the block into a typed 429 whose ``retry_after`` is the
seconds until UTC midnight.

Scope note: both chat and voice turns are covered — voice turns are
submitted over HTTP POST (``/sessions/{id}/turns``), which passes through
the same middleware; the voice WebSocket carries audio signaling only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ai.core.faults import fault_location

logger = logging.getLogger(__name__)

BUDGET_SCHEMA_VERSION = "v1"

#: Two UTC days: the active day plus enough slack that a counter never
#: expires mid-day regardless of when it was seeded.
_COUNTER_TTL_SECONDS = 48 * 3600

_DAY_SECONDS = 24 * 3600


def _day_stamp(now: float) -> str:
    """UTC yyyymmdd for the counter key."""
    return time.strftime("%Y%m%d", time.gmtime(now))


def budget_key(user_pk, now: float | None = None) -> str:
    """The user's counter key for the current UTC day."""
    now = time.time() if now is None else now
    return f"aimms:budget:{BUDGET_SCHEMA_VERSION}:{user_pk}:{_day_stamp(now)}"


def seconds_to_utc_midnight(now: float | None = None) -> int:
    """Honest Retry-After for a budget 429: when the UTC day rolls over."""
    now = time.time() if now is None else now
    return int(_DAY_SECONDS - (now % _DAY_SECONDS)) or _DAY_SECONDS


def add_spend(user_pk, tokens: int, now: float | None = None) -> None:
    """Count tokens against the user's day; best-effort, fault-logged."""
    if not tokens or tokens <= 0 or user_pk in (None, ""):
        return
    key = budget_key(user_pk, now)
    try:
        from django.core.cache import cache

        cache.add(key, 0, timeout=_COUNTER_TTL_SECONDS)
        cache.incr(key, int(tokens))
    except Exception as exc:
        logger.error("budget spend write failed (fail-open) %s", fault_location(exc))


def current_spend(user_pk, now: float | None = None) -> int | None:
    """The user's tokens spent today, or None on cache failure (fail-open)."""
    try:
        from django.core.cache import cache

        value = cache.get(budget_key(user_pk, now))
        return int(value) if value is not None else 0
    except Exception as exc:
        logger.error("budget spend read failed (fail-open) %s", fault_location(exc))
        return None


def record_turn_spend(user_pk) -> None:
    """Drain the bound ledger's canonical total into the user's day counter.

    Called once per turn at terminal (success, failed, canceled alike — the
    tokens were spent either way). ``total_tokens`` is preferred; absent
    that, input+output. An empty ledger writes nothing.
    """
    try:
        from ai.core.usage import turn_usage_ledger

        ledger = turn_usage_ledger.get()
        if ledger is None:
            return
        totals = ledger.totals()
        tokens = totals.get("total_tokens") or (
            totals.get("input_tokens", 0) + totals.get("output_tokens", 0)
        )
        add_spend(user_pk, tokens)
        # The terminal-metadata drain has already read these events. Clear
        # so a later call in the same task context (turn ledgers are
        # rebound, never reset) can never count the same spend twice.
        ledger.events.clear()
    except Exception as exc:  # pragma: no cover - telemetry must never raise
        logger.error("budget turn spend failed %s", fault_location(exc))


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of a pre-turn budget check."""

    blocked: bool
    used: int
    cap: int
    retry_after: int
    #: S12 enforce-mode fail-closed marker: the quota store/policy source is
    #: down and enforcement cannot be evaluated — the middleware returns a
    #: typed 503 ``quota_store_unavailable``, never a silent pass.
    store_unavailable: bool = False


def check_budget(user_pk, now: float | None = None, tenant_id: str | None = None) -> BudgetDecision:
    """Evaluate the user's spend against the configured daily cap.

    ``blocked`` already folds in the enforce flag, exemptions, an unlimited
    cap (0), and the fail-open posture; the middleware only has to honor it.
    Shadow logging happens here so every caller gets it for free.

    With ``FEATURE_AI_QUOTA_PROFILES`` on, the check switches to the
    profile-resolved caps at all three levels (user/tenant/deployment)
    against the reserve-then-settle counters; the v1 single-cap path stays
    byte-identical when the flag is off. In BOTH paths, whether a block is
    real is governed by the same shadow/enforce pair; the profile path adds
    the fail-closed posture — under enforce, a store failure is
    ``store_unavailable``, never a pass.
    """
    try:
        from ai.core.config import get_settings

        settings = get_settings()
        cap = int(getattr(settings, "ai_user_daily_token_budget", 0) or 0)
        shadow = bool(getattr(settings, "feature_token_budget_shadow", False))
        enforce = bool(getattr(settings, "feature_token_budget_enforce", False))
        exempt_raw = str(getattr(settings, "ai_budget_exempt_user_ids", "") or "")
        profiles_on = bool(getattr(settings, "feature_ai_quota_profiles", False))
    except Exception:  # pragma: no cover - config absent in minimal envs
        return BudgetDecision(blocked=False, used=0, cap=0, retry_after=0)

    if not shadow and not enforce:
        return BudgetDecision(blocked=False, used=0, cap=cap, retry_after=0)
    # Exemptions survive the profile flip as a migration bridge; retiring
    # them in favor of assigned profiles is a deployment cleanup step.
    exempt = {part.strip() for part in exempt_raw.split(",") if part.strip()}
    if str(user_pk) in exempt:
        return BudgetDecision(blocked=False, used=0, cap=cap, retry_after=0)

    if profiles_on:
        return _check_profile_budget(
            user_pk, tenant_id=tenant_id, shadow=shadow, enforce=enforce, now=now
        )

    if cap <= 0:
        return BudgetDecision(blocked=False, used=0, cap=cap, retry_after=0)
    used = current_spend(user_pk, now)
    if used is None:
        # Cache failure: fail open, already fault-logged by current_spend.
        # S15/Q50(b): under ENFORCE that fail-open is itself a critical
        # event (a regression of the Q44 posture) — report it; hardening
        # the v1 path to fail closed stays S12 scope, so the request
        # behavior here is unchanged.
        if enforce:
            try:
                from ai.core.pilot_latch import report_critical_event

                report_critical_event(
                    "enforce_fail_open", "token budget store unreadable under enforce"
                )
            except Exception:  # pragma: no cover - reporting is best-effort
                pass
        return BudgetDecision(blocked=False, used=0, cap=cap, retry_after=0)
    if used < cap:
        return BudgetDecision(blocked=False, used=used, cap=cap, retry_after=0)

    retry_after = seconds_to_utc_midnight(now)
    if shadow and not enforce:
        logger.warning("budget.would_block user=%s used=%d cap=%d", user_pk, used, cap)
    return BudgetDecision(blocked=enforce, used=used, cap=cap, retry_after=retry_after)


def _check_profile_budget(
    user_pk, *, tenant_id: str | None, shadow: bool, enforce: bool, now: float | None
) -> BudgetDecision:
    """S12: committed (used+reserved) vs the profile caps at every level."""
    from ai.core.quota.profiles import QuotaSourceUnavailable, resolve_profile
    from ai.core.quota.reservation import level_usage

    try:
        snapshot = resolve_profile(user_pk)
    except QuotaSourceUnavailable as exc:
        logger.error("quota policy resolution failed %s", fault_location(exc))
        if enforce:
            return BudgetDecision(
                blocked=True, used=0, cap=0, retry_after=60, store_unavailable=True
            )
        return BudgetDecision(blocked=False, used=0, cap=0, retry_after=0)

    usages = level_usage(user_pk=user_pk, tenant_id=tenant_id or "default", snapshot=snapshot)
    if usages is None:
        if enforce:
            return BudgetDecision(
                blocked=True, used=0, cap=0, retry_after=60, store_unavailable=True
            )
        return BudgetDecision(blocked=False, used=0, cap=snapshot.user_cap, retry_after=0)

    user_usage = next(usage for usage in usages if usage.level == "user")
    for usage in usages:
        if usage.cap > 0 and usage.committed >= usage.cap:
            retry_after = seconds_to_utc_midnight(now)
            if shadow and not enforce:
                logger.warning(
                    "quota.would_block level=%s user=%s committed=%d cap=%d profile=%s",
                    usage.level,
                    user_pk,
                    usage.committed,
                    usage.cap,
                    snapshot.profile,
                )
            return BudgetDecision(
                blocked=enforce,
                used=user_usage.used,
                cap=user_usage.cap,
                retry_after=retry_after,
            )
    return BudgetDecision(blocked=False, used=user_usage.used, cap=user_usage.cap, retry_after=0)


__all__ = [
    "BUDGET_SCHEMA_VERSION",
    "BudgetDecision",
    "add_spend",
    "budget_key",
    "check_budget",
    "current_spend",
    "record_turn_spend",
    "seconds_to_utc_midnight",
]
