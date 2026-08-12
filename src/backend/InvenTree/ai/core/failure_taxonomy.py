"""Typed turn-failure classification (S38).

Maps an exception (plus the optional pipeline stage) into one of four
failure classes so outages stop rendering as generic "turn failed":

- ``provider_outage`` — the model provider is unreachable/broken (5xx,
  timeouts, connection failures);
- ``rate_limited`` — the provider said 429;
- ``config_gate`` — a server-side configuration or gate rejected the turn;
- ``internal`` — everything else (the honest default).

Classification is name-based over the exception MRO so the openai/aiohttp/
httpx types are recognized without importing any provider SDK here, and it
never reads ``str(exc)`` — classes and status codes only, consistent with
the faults.py discipline.
"""

from __future__ import annotations

from enum import StrEnum


class FailureClass(StrEnum):
    """The four typed turn-failure classes."""

    PROVIDER_OUTAGE = "provider_outage"
    RATE_LIMITED = "rate_limited"
    CONFIG_GATE = "config_gate"
    INTERNAL = "internal"


_RATE_LIMIT_TYPES = frozenset({"RateLimitError", "TooManyRequests"})

_OUTAGE_TYPES = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "ServiceUnavailableError",
    "ClientConnectionError",
    "ClientConnectorError",
    "ServerDisconnectedError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
})

# Deliberately narrow: pydantic ValidationError is NOT here — in this
# codebase it is overwhelmingly a model-output shape failure (e.g. a
# whitespace-only reply failing CanonicalTurnResponse), and typing it as
# config_gate would tell users to call an administrator about a transient
# model flake. Unknown stays internal.
_CONFIG_GATE_TYPES = frozenset({
    "TrustedContextConfigurationError",
    "ImproperlyConfigured",
    "SettingsError",
})


def _status_code(exc: BaseException) -> int | None:
    """A provider HTTP status when the exception carries one."""
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    return None


#: How far down ``__cause__``/``__context__`` to look. Provider errors are
#: typically wrapped once or twice before the turn_service catch-all.
_CHAIN_DEPTH = 4


def _classify_one(exc: BaseException) -> FailureClass:
    names = {klass.__name__ for klass in type(exc).__mro__}
    status = _status_code(exc)

    if names & _RATE_LIMIT_TYPES or status == 429:
        return FailureClass.RATE_LIMITED
    if (
        names & _OUTAGE_TYPES
        or isinstance(exc, TimeoutError | ConnectionError)
        or (status is not None and status >= 500)
    ):
        return FailureClass.PROVIDER_OUTAGE
    if names & _CONFIG_GATE_TYPES:
        return FailureClass.CONFIG_GATE
    return FailureClass.INTERNAL


def classify_turn_failure(exc: BaseException, stage: str | None = None) -> FailureClass:
    """Classify one turn-ending exception; unknown shapes are ``internal``.

    A layer that saw the ORIGINAL exception before flattening it into a
    message string (a workflow reporting ``success=False``) can pre-classify
    by stamping a valid ``failure_class`` value on whatever it raises; that
    verdict wins here. Otherwise the exception and its cause/context chain
    are checked class-by-class — a wrapper ``raise ... from exc`` must not
    demote a provider outage to ``internal``.
    """
    stamped = getattr(exc, "failure_class", None)
    if isinstance(stamped, FailureClass):
        return stamped
    if isinstance(stamped, str):
        try:
            return FailureClass(stamped)
        except ValueError:
            pass

    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(_CHAIN_DEPTH):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        verdict = _classify_one(current)
        if verdict is not FailureClass.INTERNAL:
            return verdict
        current = current.__cause__ or current.__context__
    # ``stage`` is accepted for future refinement (root.py does not attach
    # its pipeline stage to raised exceptions today) but adds no signal the
    # class checks above have not already used.
    return FailureClass.INTERNAL


__all__ = ["FailureClass", "classify_turn_failure"]
