"""Reserve-then-settle token accounting over the shared cache (S12, WP-B2).

Flow per turn (all inside ``asyncio.to_thread`` — never on the event loop):

1. **Reserve** before execution: ``incr(reserved, worst_case)`` at every
   level (user / tenant / deployment), guarded by a per-turn idempotency
   marker so a replayed submission can never double-reserve.
2. **Settle** at terminal, in the same ``finally`` as the v1 counter:
   ``incr(used, charge)`` and ``incr(reserved, -worst_case)``.

The charge rule is the load-bearing part (§8.9 "counted or explicitly
bounded"): a replayed or preempted turn charges zero; an executed turn
charges its ledger actuals; an executed turn whose ledger is EMPTY charges
the full worst case — which conservatively bounds every uninstrumented rail
(legacy wf1-wf6, failed provider calls) without instrumenting them.

Posture: counters are an abuse envelope, not billing (the S37 ADR). Cache
errors here fail open with a fault log; the fail-closed enforce decision
lives in ``check_budget`` where the caps are compared. ``used`` is never
compensated downward — the no-rollback rule stands.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ai.core.faults import fault_location

if TYPE_CHECKING:
    from ai.core.quota.assignment_source import PolicySnapshot

logger = logging.getLogger(__name__)

_SCHEMA = "v2"
#: Two UTC days, mirroring the v1 budget counter TTL rationale.
_COUNTER_TTL_SECONDS = 48 * 3600

#: Per-call worst-case bounds by ModelPurpose value, for callers that
#: reserve a single provider call rather than a whole turn. The whole-turn
#: envelope uses ``ai_quota_turn_reserve_tokens``. Configured bounds, never
#: p95 (§8.9 — p95 x 300 x 2 sizes evaluation CAPACITY, not reservations).
WORST_CASE_TOKENS: dict[str, int] = {
    "wf8_primary": 24_000,
    "fallback_classifier": 2_000,
    "grounding_audit": 4_000,
    "closeout_binding": 8_000,
    "summarization": 8_000,
    "extraction": 8_000,
    "media_caption": 4_000,
}

_LEVEL_USER = "user"
_LEVEL_TENANT = "tenant"
_LEVEL_GLOBAL = "global"


def _day_stamp(now: float | None = None) -> str:
    return time.strftime("%Y%m%d", time.gmtime(time.time() if now is None else now))


def _counter_key(env: str, policy_version: int, level: str, level_id: str, kind: str) -> str:
    day = _day_stamp()
    return f"aimms:quota:{_SCHEMA}:{env}:{policy_version}:{level}:{level_id}:{day}:{kind}"


def _marker_key(idempotency_key: str, suffix: str) -> str:
    return f"aimms:quota:resv:{idempotency_key}:{suffix}"


@dataclass(frozen=True, slots=True)
class TurnReservation:
    """One turn's live reservation handle."""

    idempotency_key: str
    user_pk: str
    tenant_id: str
    env: str
    policy_version: int
    worst_case: int
    #: Resolved profile name, carried for the quota_profile span attr.
    profile: str = ""


@dataclass(frozen=True, slots=True)
class LevelUsage:
    """used/reserved for one accounting level."""

    level: str
    used: int
    reserved: int
    cap: int

    @property
    def committed(self) -> int:
        return self.used + self.reserved


def _levels(reservation_or_env, policy_version=None, user_pk=None, tenant_id=None):
    if isinstance(reservation_or_env, TurnReservation):
        r = reservation_or_env
        env, policy_version, user_pk, tenant_id = r.env, r.policy_version, r.user_pk, r.tenant_id
    else:
        env = reservation_or_env
    return (
        (
            (_LEVEL_USER, str(user_pk)),
            (_LEVEL_TENANT, str(tenant_id or "default")),
            (_LEVEL_GLOBAL, "all"),
        ),
        env,
        policy_version,
    )


def _incr(cache, key: str, delta: int) -> int | None:
    cache.add(key, 0, timeout=_COUNTER_TTL_SECONDS)
    try:
        return int(cache.incr(key, delta))
    except ValueError:
        # Key expired between add and incr — reseed once.
        cache.add(key, 0, timeout=_COUNTER_TTL_SECONDS)
        return int(cache.incr(key, delta))


def reserve_turn(
    *,
    user_pk,
    tenant_id: str,
    snapshot: PolicySnapshot,
    idempotency_key: str,
) -> TurnReservation | None:
    """Reserve the worst-case turn envelope at every level; idempotent.

    Returns None when this turn already holds a reservation (replayed
    submission) or the cache is unavailable (fail-open: the turn proceeds
    uncounted-forward, and the settle path degrades the same way).
    """
    from ai.core.config import get_settings

    settings = get_settings()
    worst_case = int(getattr(settings, "ai_quota_turn_reserve_tokens", 0) or 0)
    env = str(getattr(settings, "ai_deployment_env", "default") or "default")
    if worst_case <= 0 or not idempotency_key:
        return None
    try:
        from django.core.cache import cache

        if not cache.add(
            _marker_key(idempotency_key, "reserved"), worst_case, timeout=_COUNTER_TTL_SECONDS
        ):
            return None  # already reserved by this turn
        levels, env, pver = _levels(env, snapshot.version, user_pk, tenant_id)
        for level, level_id in levels:
            _incr(cache, _counter_key(env, pver, level, level_id, "reserved"), worst_case)
        reservation = TurnReservation(
            idempotency_key=str(idempotency_key),
            user_pk=str(user_pk),
            tenant_id=str(tenant_id or "default"),
            env=env,
            policy_version=snapshot.version,
            worst_case=worst_case,
            profile=snapshot.profile,
        )
        _record_durable(reservation, action="reserve")
        return reservation
    except Exception as exc:
        logger.error("quota reserve failed (fail-open) %s", fault_location(exc))
        return None


def settle_turn(
    reservation: TurnReservation | None,
    *,
    ledger_tokens: int,
    outcome: str,
) -> int:
    """Settle one reservation; idempotent; returns the charged tokens.

    ``outcome``: ``"executed"`` (charge actuals, or the full worst case when
    the ledger is empty — the uninstrumented-rail bound), ``"replayed"`` or
    ``"preempted"`` (charge zero — no execution happened).
    """
    if reservation is None:
        return 0
    try:
        from django.core.cache import cache

        if not cache.add(
            _marker_key(reservation.idempotency_key, "settled"), 1, timeout=_COUNTER_TTL_SECONDS
        ):
            return 0  # double-settle guard (duplicate finally)
        if outcome in ("replayed", "preempted"):
            charge = 0
        elif ledger_tokens > 0:
            charge = int(ledger_tokens)
        else:
            charge = reservation.worst_case
        levels, env, pver = _levels(reservation)
        for level, level_id in levels:
            if charge:
                _incr(cache, _counter_key(env, pver, level, level_id, "used"), charge)
            remaining = _incr(
                cache,
                _counter_key(env, pver, level, level_id, "reserved"),
                -reservation.worst_case,
            )
            if remaining is not None and remaining < 0:
                # Expired/flushed counter drifted; clamp. Racy but this is
                # an abuse envelope, and drift is reconciliation's job.
                cache.set(
                    _counter_key(env, pver, level, level_id, "reserved"),
                    0,
                    timeout=_COUNTER_TTL_SECONDS,
                )
        _record_durable(reservation, action="settle", settled_tokens=charge)
        return charge
    except Exception as exc:
        logger.error("quota settle failed (fail-open) %s", fault_location(exc))
        return 0


def level_usage(
    *, user_pk, tenant_id: str, snapshot: PolicySnapshot
) -> tuple[LevelUsage, ...] | None:
    """Read used+reserved vs cap at every level; None on cache failure."""
    from ai.core.config import get_settings

    env = str(getattr(get_settings(), "ai_deployment_env", "default") or "default")
    caps = {
        _LEVEL_USER: snapshot.user_cap,
        _LEVEL_TENANT: snapshot.tenant_cap,
        _LEVEL_GLOBAL: snapshot.global_cap,
    }
    try:
        from django.core.cache import cache

        levels, env, pver = _levels(env, snapshot.version, user_pk, tenant_id)
        usages = []
        for level, level_id in levels:
            used = int(cache.get(_counter_key(env, pver, level, level_id, "used")) or 0)
            reserved = int(cache.get(_counter_key(env, pver, level, level_id, "reserved")) or 0)
            usages.append(
                LevelUsage(level=level, used=used, reserved=max(0, reserved), cap=caps[level])
            )
        return tuple(usages)
    except Exception as exc:
        logger.error("quota usage read failed %s", fault_location(exc))
        return None


def _record_durable(reservation: TurnReservation, *, action: str, settled_tokens: int = 0) -> None:
    """Best-effort durable mirror for reconciliation; never blocks a turn."""
    try:
        import datetime

        from aichat.models import AIQuotaReservation, AIQuotaReservationState
        from django.utils import timezone

        if action == "reserve":
            AIQuotaReservation.objects.get_or_create(
                idempotency_key=reservation.idempotency_key[:255],
                defaults={
                    "user_id": int(reservation.user_pk)
                    if str(reservation.user_pk).isdigit()
                    else None,
                    "policy_version": reservation.policy_version,
                    "purpose": "turn",
                    "reserved_tokens": reservation.worst_case,
                    "expires_at": timezone.now() + datetime.timedelta(seconds=_COUNTER_TTL_SECONDS),
                },
            )
        else:
            AIQuotaReservation.objects.filter(
                idempotency_key=reservation.idempotency_key[:255]
            ).update(
                state=AIQuotaReservationState.SETTLED,
                settled_tokens=settled_tokens,
                settled_at=timezone.now(),
            )
    except Exception:
        logger.debug("durable reservation mirror failed", exc_info=False)


__all__ = [
    "WORST_CASE_TOKENS",
    "LevelUsage",
    "TurnReservation",
    "level_usage",
    "reserve_turn",
    "settle_turn",
]
