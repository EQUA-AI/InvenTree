"""Probe-only I/O limits. Ordinary embedding/chat retry policy is unchanged."""

import time
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("ai_boot_deadline", default=None)


@contextmanager
def probe_budget(deadline: float):
    """Carry the supervisor's cycle deadline inside its sole probe thread."""
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def probe_timeout() -> float | None:
    """At most ten seconds per transport operation, within the cycle budget.

    Transport timeouts are not thread cancellation or hard wall-clock limits
    (DNS/credential libraries may take longer). The supervisor always waits for
    that thread to finish before retrying or finishing shutdown.
    """
    deadline = _deadline.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("AI boot probe budget exhausted")
    return min(10.0, remaining)


def openai_probe_options() -> dict:
    timeout = probe_timeout()
    return {"timeout": timeout, "max_retries": 0} if timeout is not None else {}


def azure_probe_options() -> dict:
    timeout = probe_timeout()
    return (
        {"connection_timeout": timeout, "read_timeout": timeout, "retry_total": 0}
        if timeout is not None
        else {}
    )


def bounded_google_request():
    """Bound credential discovery/refresh as well as the embedding request."""
    from google.auth.transport.requests import Request

    class ProbeRequest(Request):
        def __call__(self, *args, **kwargs):
            kwargs["timeout"] = probe_timeout()
            return super().__call__(*args, **kwargs)

    return ProbeRequest()
