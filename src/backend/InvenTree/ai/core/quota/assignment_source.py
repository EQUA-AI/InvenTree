"""Lazy, swappable loader for durable quota assignments (S12).

The indirection exists so the ``ai/core`` pytest island stays DB-free
(tests monkeypatch ``load_assignment``) while production reads the real
``aichat`` rows. Callers must treat any exception as "source unavailable"
and apply their own shadow/enforce posture — this module never decides.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """One resolved policy, cache-friendly and ORM-free."""

    profile: str
    version: int
    user_cap: int
    tenant_cap: int
    global_cap: int
    requests_per_minute: int
    requests_per_hour: int


def load_assignment(user_pk) -> PolicySnapshot | None:
    """Read the user's live assignment from the durable store (Django ORM).

    None means "no assignment — use the standard default". Exceptions
    propagate: the caller owns the shadow (fail-open) vs enforce
    (fail-closed 503) decision.
    """
    from aichat.services.quota import active_assignment

    assignment = active_assignment(user_pk)
    if assignment is None:
        return None
    policy = assignment.policy
    return PolicySnapshot(
        profile=str(policy.profile),
        version=int(policy.version),
        user_cap=int(policy.user_daily_tokens),
        tenant_cap=int(policy.tenant_daily_tokens),
        global_cap=int(policy.deployment_daily_tokens),
        requests_per_minute=int(policy.requests_per_minute),
        requests_per_hour=int(policy.requests_per_hour),
    )


__all__ = ["PolicySnapshot", "load_assignment"]
