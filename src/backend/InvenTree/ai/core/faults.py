"""Locate a failure without repeating what it said.

Provider exceptions can carry credentials, connection strings and customer
content in their messages and args, so the AI error paths deliberately do not
log them. The 2026-07-28 outage showed the cost of stopping there: logging only
the exception's class name meant a dead embedding client had to be diagnosed by
matching a 2-millisecond timing signature against the source tree.

These helpers are the bargain between those two failure modes: never the
message, always the location. Everything returned here comes from code
coordinates (module, function, line number) and the exception's type - data
that exists in the source tree already and cannot contain runtime secrets.
"""

from __future__ import annotations

import logging
from typing import Any

_UNKNOWN = "unknown"

#: Module prefixes that count as "our code" when picking the actionable frame.
#: The innermost frame of a provider failure is usually deep inside an SDK;
#: the last frame we own is the one a reader can act on.
_OWN_PREFIXES = ("ai.", "aichat", "assets", "InvenTree", "repair", "tasks", "voice")


def _frame_label(tb: Any) -> str:
    """Render one traceback entry as ``module:function:lineno``."""
    frame = tb.tb_frame
    module = frame.f_globals.get("__name__", _UNKNOWN)
    function = getattr(frame.f_code, "co_qualname", None) or frame.f_code.co_name
    return f"{module}:{function}:{tb.tb_lineno}"


def fault_location(exc: BaseException) -> dict[str, str]:
    """Return non-sensitive coordinates for ``exc``.

    ``error_type``
        The exception class name.
    ``raised_at``
        The innermost frame - where the exception actually happened, which for
        provider failures is typically inside the SDK.
    ``via``
        The innermost frame belonging to this codebase - the call site a
        reader should open first.

    Never touches ``str(exc)`` or ``exc.args``, so nothing here can leak what
    the exception was carrying.
    """
    location = {
        "error_type": type(exc).__name__,
        "raised_at": _UNKNOWN,
        "via": _UNKNOWN,
    }

    tb = exc.__traceback__
    while tb is not None:
        label = _frame_label(tb)
        location["raised_at"] = label
        if label.startswith(_OWN_PREFIXES):
            location["via"] = label
        tb = tb.tb_next

    return location


def log_fault(
    logger: logging.Logger,
    event: str,
    exc: BaseException,
    *,
    stage: str | None = None,
    level: int = logging.ERROR,
) -> None:
    """Log ``event`` with enough structure to locate ``exc``, and nothing more.

    ``event`` must be a fixed description written by the caller, never derived
    from the exception. ``stage`` names the pipeline phase that was active so
    an outage can be placed without correlating timestamps.
    """
    location = fault_location(exc)
    logger.log(
        level,
        "%s (stage=%s error_type=%s raised_at=%s via=%s)",
        event,
        stage or _UNKNOWN,
        location["error_type"],
        location["raised_at"],
        location["via"],
    )
