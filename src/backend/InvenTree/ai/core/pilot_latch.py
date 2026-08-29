"""The fail-closed pilot-stop admission gate (S15, §15.4/§16).

One check in ``NormalizedTurnService.process`` — BEFORE the admission
lease — covers every rail (HTTP, SSE, AG-UI, voice). Deliberately the
INVERSE of admission control's fail-open availability ADR: §16 says a
latched pilot "is never averaged into a quality score" and §15.4 blocks
new admissions outright, so when ``FEATURE_AI_PILOT_STOP_LATCH`` is armed
and the latch state cannot be read at all, the gate REFUSES
(``pilot_latch_unavailable``) rather than admits.

Q50 note: admission control's own ``store_error`` outcome stays exempt
from the fail-open trigger below — Q50 names the budget/rate stores, and
admission's fail-open is a recorded availability decision, not a
regression.

State flows Django -> AI plane through the shared cache
(``aichat.services.pilot_latch`` writes it on engage, so automatic stops
propagate near-instantly) with a short-TTL cached loader fallback; the
loader is swappable for island tests (the ``assignment_source`` idiom).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Typed rejection codes (emitted into the generated wire contract).
PILOT_ERROR_CODES: tuple[str, ...] = ("pilot_stopped", "pilot_latch_unavailable")

#: Mirrors aichat.services.pilot_latch.LATCH_CACHE_KEY (kept literal so the
#: island never imports the Django service module at import time).
LATCH_CACHE_KEY = "aimms:pilot:latch:v1"

#: Sentinel cached for a confirmed-clear latch (cache stores no None).
_CLEAR = "__clear__"

#: In-process dedup for automatic engage attempts: a critical event storm
#: must not hammer the DB. Deliberately NOT the shared cache — a cache
#: outage is exactly when these triggers fire.
_ENGAGE_DEDUP_S = 60.0
_dedup_lock = threading.Lock()
_last_engage_attempt: dict[str, float] = {}


class PilotStopped(Exception):
    """The pilot-stop latch is engaged; the turn is refused (non-retryable)."""

    code = "pilot_stopped"

    def __init__(self, reason_code: str = ""):
        super().__init__(f"pilot stopped ({reason_code or 'latched'})")
        self.reason_code = reason_code


class PilotLatchUnavailable(Exception):
    """The latch state is unreadable while armed; refuse (fail CLOSED)."""

    code = "pilot_latch_unavailable"

    def __init__(self, retry_after: int = 30):
        super().__init__("pilot latch state unavailable")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class LatchState:
    """The singleton latch fact the gate needs."""

    latched: bool
    reason_code: str = ""


def _default_loader() -> LatchState:
    from aichat.services.pilot_latch import current_state

    state = current_state()
    return LatchState(
        latched=bool(state.get("latched")), reason_code=str(state.get("reason_code") or "")
    )


#: Swappable for island tests; the default reads the Django service.
load_latch_state: Callable[[], LatchState] = _default_loader


def _default_engage(reason_code: str, detail: str) -> None:
    from aichat.services.pilot_latch import engage_latch

    engage_latch(reason_code=reason_code, source="automatic", detail=detail)


#: Swappable for island tests; the default writes through the Django service.
_engage: Callable[[str, str], None] = _default_engage


def invalidate_latch_cache() -> None:
    """Drop the cached read (tests, manual DB edits)."""
    try:
        from django.core.cache import cache

        cache.delete(LATCH_CACHE_KEY)
    except Exception:  # pragma: no cover
        pass


def check_pilot_admission() -> None:
    """Refuse the turn when the latch is set — or unreadable while armed.

    Inert (returns immediately) while ``FEATURE_AI_PILOT_STOP_LATCH`` is
    dark. Sync ORM/cache work — call via ``asyncio.to_thread``.
    """
    from ai.core.config import get_settings

    settings = get_settings()
    if not getattr(settings, "feature_ai_pilot_stop_latch", False):
        return

    cached = None
    cache_ok = True
    try:
        from django.core.cache import cache

        cached = cache.get(LATCH_CACHE_KEY)
    except Exception:
        cache_ok = False

    if cached is not None:
        if cached == _CLEAR:
            return
        raise PilotStopped(str(cached))

    try:
        state = load_latch_state()
    except Exception:
        if not cache_ok:
            raise PilotLatchUnavailable() from None
        # The cache works but holds nothing and the DB read failed: still
        # unreadable-while-armed -> fail closed.
        raise PilotLatchUnavailable() from None

    ttl = int(getattr(settings, "ai_pilot_latch_cache_ttl_s", 10))
    if cache_ok:
        try:
            from django.core.cache import cache

            cache.set(LATCH_CACHE_KEY, state.reason_code if state.latched else _CLEAR, ttl)
        except Exception:  # pragma: no cover - the decision below still holds
            pass
    if state.latched:
        raise PilotStopped(state.reason_code)


def report_critical_event(reason_code: str, detail: str = "") -> None:
    """The automatic-set path for mechanically detected Q50 triggers.

    NEVER raises (it must not mask the fault being reported) and logs
    CRITICAL always, flag-armed or not. The engage itself is flag-gated:
    a dark deployment records the event in logs only.
    """
    logger.critical("PILOT CRITICAL EVENT reason=%s detail=%s", reason_code, detail[:120])
    try:
        from ai.core.config import get_settings

        if not getattr(get_settings(), "feature_ai_pilot_stop_latch", False):
            return
        now = time.monotonic()
        with _dedup_lock:
            last = _last_engage_attempt.get(reason_code, 0.0)
            if now - last < _ENGAGE_DEDUP_S:
                return
            _last_engage_attempt[reason_code] = now
        _engage(reason_code, detail)
    except Exception:  # pragma: no cover - reporting must never raise
        logger.critical(
            "pilot latch automatic engage FAILED for %s (event stands in logs)",
            reason_code,
            exc_info=True,
        )


def record_request_rejection(code: str, user_pk=None) -> None:
    """Best-effort content-free rejection ledger row (§8.10 denominator).

    Never raises, and bounds any DB stall to a short join so the typed
    rejection response is never held hostage. The write runs on its own
    thread because the rejection paths are async handlers (sync ORM is
    forbidden on the event loop).
    """

    def _write() -> None:
        try:
            from aichat.models import AIRequestRejection

            AIRequestRejection.objects.create(code=code[:40], user_id=user_pk or None)
        except Exception:  # pragma: no cover - telemetry only
            logger.debug("request rejection row skipped (%s)", code)

    try:
        writer = threading.Thread(target=_write, name="aimms-rejection", daemon=True)
        writer.start()
        writer.join(timeout=2.0)
    except Exception:  # pragma: no cover - telemetry only
        logger.debug("request rejection thread skipped (%s)", code)


__all__ = [
    "LATCH_CACHE_KEY",
    "PILOT_ERROR_CODES",
    "LatchState",
    "PilotLatchUnavailable",
    "PilotStopped",
    "check_pilot_admission",
    "invalidate_latch_cache",
    "load_latch_state",
    "record_request_rejection",
    "report_critical_event",
]
