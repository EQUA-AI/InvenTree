"""OTel span helpers for the AI plane (S36).

Spans ship dark: ``trace.get_tracer`` rides whatever tracer provider the
process has configured (``InvenTree/tracing.py:setup_tracing`` when the
human sets the OTLP endpoint env). With no provider — the default — every
span here is a no-op proxy. With the library absent entirely (bare local
venvs), the helpers degrade to null context managers. Nothing on the
request path may ever fail because of tracing.

Attribute fault discipline: only keys registered in ``_ALLOWED_ATTRS`` are
attached, and values are coerced to short scalars. Ids, enum codes, counts —
never prompt text, error text, tool arguments, or user content.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

TRACER_NAME = "aimms.ai"

#: The complete attribute vocabulary. A key not listed here is dropped, so a
#: content-bearing value cannot reach a span by accident.
_ALLOWED_ATTRS = frozenset({
    "aimms.correlation_id",
    "aimms.session_correlation_id",
    "aimms.thread_id",
    "aimms.turn_id",
    "aimms.modality",
    "aimms.workflow_id",
    "aimms.response_state",
    "aimms.outcome_code",
    "aimms.route_mode",
    "aimms.tool_name",
    "aimms.decision_code",
    "aimms.proposal_id",
    "aimms.action_type",
    "aimms.turn_sequence",
    # S0 (analysis rail): content-free scope/intent/validation telemetry.
    # Enum codes, versions, counts, and hash PREFIXES only — never machine
    # names, filters, claims, or any other scope/answer content.
    "aimms.task_intent",
    "aimms.effect_intent",
    "aimms.scope_mode",
    "aimms.scope_version",
    "aimms.scope_hash_prefix",
    "aimms.scope_machine_count",
    "aimms.scope_rejections",
    "aimms.validator_outcome",
    "aimms.coverage_complete",
    "aimms.quota_profile",
    "aimms.admission_outcome",
    # S15: the content-free Q50 reason code on a pilot-stop rejection span.
    "aimms.pilot_stop_reason",
    # S13: latency class + worst threshold crossed (enum codes, §8.9 table).
    "aimms.slo_class",
    "aimms.slo_breach",
})

_MAX_ATTR_LEN = 128


def _tracer():
    """The shared tracer, or None when opentelemetry is not installed."""
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - bare venv without otel
        return None
    return trace.get_tracer(TRACER_NAME)


def span_attrs(**kwargs: Any) -> dict[str, Any]:
    """Filter attributes through the allowlist and scalar coercion.

    Unknown keys are dropped (and counted in a debug log, never raised);
    values become short strings or ints. This is the single choke point the
    fault discipline relies on — bypassing it to call ``set_attribute``
    directly is a review error.
    """
    allowed: dict[str, Any] = {}
    for key, value in kwargs.items():
        full_key = key if key.startswith("aimms.") else f"aimms.{key}"
        if full_key not in _ALLOWED_ATTRS or value is None:
            continue
        if isinstance(value, bool | int):
            allowed[full_key] = int(value)
        else:
            allowed[full_key] = str(value)[:_MAX_ATTR_LEN]
    return allowed


@contextmanager
def turn_span(name: str, **attrs: Any):
    """Open one span with allowlisted attributes; a no-op without a provider.

    Yields the span (or None). Callers may add more attributes later via
    ``set_span_attrs`` — never via raw ``set_attribute``. SDK failures on
    enter/exit are swallowed (the work continues untraced); exceptions from
    the traced body always propagate unchanged.
    """
    tracer = _tracer()
    if tracer is None:
        yield None
        return
    ctx = None
    span = None
    try:
        ctx = tracer.start_as_current_span(name, attributes=span_attrs(**attrs))
        span = ctx.__enter__()
    except Exception:
        logger.warning("tracing span start failed name=%s", name)
        ctx = None
    try:
        yield span
    except BaseException as exc:
        if ctx is not None:
            try:
                ctx.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:  # pragma: no cover - SDK exit failure
                logger.debug("tracing span exit failed name=%s", name)
        raise
    else:
        if ctx is not None:
            try:
                ctx.__exit__(None, None, None)
            except Exception:  # pragma: no cover - SDK exit failure
                logger.debug("tracing span exit failed name=%s", name)


def set_span_attrs(span: Any, **attrs: Any) -> None:
    """Attach allowlisted attributes to an open span; no-op on None."""
    if span is None:
        return
    try:
        for key, value in span_attrs(**attrs).items():
            span.set_attribute(key, value)
    except Exception:  # pragma: no cover - SDK failure must stay silent
        logger.debug("span attribute set failed")


def instrument_fastapi(app: Any) -> None:
    """Best-effort FastAPI auto-instrumentation; absent lib is normal."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        logger.info("opentelemetry fastapi instrumentation not installed; skipping")
    except Exception:
        logger.warning("fastapi instrumentation failed; continuing untraced")


__all__ = [
    "TRACER_NAME",
    "instrument_fastapi",
    "set_span_attrs",
    "span_attrs",
    "turn_span",
]
