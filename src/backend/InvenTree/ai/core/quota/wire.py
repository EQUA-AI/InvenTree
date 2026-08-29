"""Wire mirrors for the quota/admission rail (S12/S13, WP-B0).

Follows ``ai/core/analysis/wire.py``: pydantic mirrors with
``extra="forbid"`` plus a pinned error-code tuple the generator emits as a
TypeScript union. The codes are the machine-readable ``code`` values every
limiter response carries (A13):

- ``token_budget_exhausted`` — daily token budget spent; the client must NOT
  auto-retry (reset is at UTC midnight; ``Retry-After`` carries the seconds).
- ``rate_limited`` — request-rate window exceeded; bounded retry honoring
  ``Retry-After`` is correct.
- ``ai_capacity_busy`` — admission control saturated (503); bounded retry
  after the short jittered ``Retry-After``; there is no durable queue.
- ``quota_store_unavailable`` — enforce-mode quota store outage (503);
  terminal for the client, never auto-retried.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

#: Machine-readable limiter ``code`` values (the ``SCOPE_ERROR_CODES``
#: precedent). Pinned to the route/middleware literals by
#: ``test_quota_wire.py``.
QUOTA_ERROR_CODES: tuple[str, ...] = (
    "token_budget_exhausted",
    "rate_limited",
    "ai_capacity_busy",
    "quota_store_unavailable",
)


class QuotaWindowStatus(BaseModel):
    """One request-rate window's live status."""

    model_config = ConfigDict(extra="forbid")

    limit: int
    used: int
    remaining: int
    reset_after_s: int


class QuotaTokenLevel(BaseModel):
    """One accounting level's (user/tenant/global) token status."""

    model_config = ConfigDict(extra="forbid")

    used: int
    reserved: int
    remaining: int
    cap: int
    reset_after_s: int


class QuotaStoreStatus(BaseModel):
    """Counter-store health as observed by the preflight read."""

    model_config = ConfigDict(extra="forbid")

    healthy: bool
    #: False under a per-process store (LocMem) — enforcement-disqualifying.
    shared: bool


class QuotaPreflightPayload(BaseModel):
    """The ``GET /quota/preflight`` response (S12 task 4)."""

    model_config = ConfigDict(extra="forbid")

    profile: str
    policy_version: int
    tokens: dict[str, QuotaTokenLevel]
    requests: dict[str, QuotaWindowStatus]
    store: QuotaStoreStatus
    #: Whether the caller-supplied estimate fits; null when no estimate given.
    fits: bool | None = None
    #: S15: the pilot-stop latch state (fail-soft read; null when the state
    #: could not be read). The battery runner refuses to start on true.
    pilot_stopped: bool | None = None


__all__ = [
    "QUOTA_ERROR_CODES",
    "QuotaPreflightPayload",
    "QuotaStoreStatus",
    "QuotaTokenLevel",
    "QuotaWindowStatus",
]
