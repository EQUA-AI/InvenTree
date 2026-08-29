"""Shared-cache active-turn admission control (S13, WP-B4).

Backpressure per §8.9: before a turn may start a provider call, it acquires
an admission lease against a per-user and a global active-turn counter. On
saturation the caller returns typed ``503 ai_capacity_busy`` with a short
jittered ``Retry-After`` — there is no durable queue in this release.

ADR — admission FAILS OPEN, even in enforce mode. This deliberately extends
the ``rate_limit_store`` posture one step further: budget enforcement fails
CLOSED (a spend ceiling that fails open is no ceiling), but admission is an
AVAILABILITY protection — failing closed would convert a cache blip into a
full outage of the very service the control exists to keep responsive. A
store failure is fault-logged and the turn proceeds.

Lease hygiene: ``cache.add`` seeds each counter with the lease TTL and every
successful acquire refreshes it (``cache.touch``), so a leaked lease (worker
crash between acquire and the ``finally`` release) self-clears within
``ai_admission_lease_ttl_s`` — configured above the hard turn deadline plus
grace. Releases swallow the expired-key error for the same reason.

Voice SESSION signaling stays outside admission — only HTTP turn submission
passes through ``NormalizedTurnService.process``.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from ai.core.faults import fault_location

logger = logging.getLogger(__name__)

_SCHEMA = "v1"


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """The acquire outcome; ``outcome`` feeds the allowlisted span attr."""

    admitted: bool
    outcome: str  # admitted | rejected_user | rejected_global | would_reject | store_error
    retry_after: int = 0


class AdmissionSaturated(Exception):
    """Raised by callers when an enforce-mode acquire was rejected."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("ai_capacity_busy")
        self.retry_after = int(retry_after)


def _keys(user_pk) -> tuple[str, str]:
    from ai.core.config import get_settings

    env = str(getattr(get_settings(), "ai_deployment_env", "default") or "default")
    return (
        f"aimms:adm:{_SCHEMA}:{env}:user:{user_pk}",
        f"aimms:adm:{_SCHEMA}:{env}:global",
    )


def _retry_after() -> int:
    from ai.core.config import get_settings

    base = int(getattr(get_settings(), "ai_admission_retry_after_s", 5) or 5)
    return base + random.randint(0, base)


def acquire_admission(user_pk) -> AdmissionDecision:
    """Try to take one active-turn slot at both levels (sync, off-loop).

    Shadow mode logs ``admission.would_reject`` and admits; enforce mode
    returns the rejection for the caller to convert into the typed 503.
    Store failures ADMIT (the availability ADR above).
    """
    from ai.core.config import get_settings

    settings = get_settings()
    shadow = bool(getattr(settings, "feature_ai_admission_control_shadow", False))
    enforce = bool(getattr(settings, "feature_ai_admission_control_enforce", False))
    if not shadow and not enforce:
        return AdmissionDecision(admitted=True, outcome="admitted")
    user_cap = int(getattr(settings, "ai_admission_max_active_per_user", 0) or 0)
    global_cap = int(getattr(settings, "ai_admission_max_active_global", 0) or 0)
    ttl = int(getattr(settings, "ai_admission_lease_ttl_s", 300) or 300)
    user_key, global_key = _keys(user_pk)

    try:
        from django.core.cache import cache

        cache.add(user_key, 0, timeout=ttl)
        active_user = int(cache.incr(user_key))
        if user_cap and active_user > user_cap:
            cache.decr(user_key)
            if enforce:
                return AdmissionDecision(
                    admitted=False, outcome="rejected_user", retry_after=_retry_after()
                )
            logger.warning("admission.would_reject level=user user=%s", user_pk)
            cache.incr(user_key)  # shadow admits: re-take the slot it counted
            active_user = None

        cache.add(global_key, 0, timeout=ttl)
        active_global = int(cache.incr(global_key))
        if global_cap and active_global > global_cap:
            cache.decr(global_key)
            if enforce:
                cache.decr(user_key)  # release the user slot taken above
                return AdmissionDecision(
                    admitted=False, outcome="rejected_global", retry_after=_retry_after()
                )
            logger.warning("admission.would_reject level=global user=%s", user_pk)
            cache.incr(global_key)

        # Refresh both leases so leaked slots self-clear.
        import contextlib

        for key in (user_key, global_key):
            with contextlib.suppress(Exception):
                cache.touch(key, ttl)
        outcome = "admitted"
        if active_user is None:
            outcome = "would_reject"
        return AdmissionDecision(admitted=True, outcome=outcome)
    except Exception as exc:
        logger.error("admission store failed (fail-open) %s", fault_location(exc))
        return AdmissionDecision(admitted=True, outcome="store_error")


def release_admission(user_pk) -> None:
    """Release both slots; expired keys are already released by TTL."""
    from ai.core.config import get_settings

    settings = get_settings()
    if not (
        getattr(settings, "feature_ai_admission_control_shadow", False)
        or getattr(settings, "feature_ai_admission_control_enforce", False)
    ):
        return
    try:
        from django.core.cache import cache

        for key in _keys(user_pk):
            try:
                if int(cache.get(key) or 0) > 0:
                    cache.decr(key)
            except ValueError:
                pass  # expired between get and decr — TTL already released it
    except Exception as exc:
        logger.error("admission release failed %s", fault_location(exc))


__all__ = [
    "AdmissionDecision",
    "AdmissionSaturated",
    "acquire_admission",
    "release_admission",
]
