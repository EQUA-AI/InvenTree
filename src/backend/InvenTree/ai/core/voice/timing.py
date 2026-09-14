"""Optional content-free timing. No timing signal confers execution authority."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from ai.core.tracing import set_span_attrs, turn_span
from pydantic import BaseModel, ConfigDict, Field, field_validator


class VoiceClientTiming(BaseModel):
    """Same-browser-clock intervals; absent values are unknown, never zero."""

    model_config = ConfigDict(extra="forbid")
    speech_to_final_ms: float | None = None
    final_to_submit_ms: float | None = None
    ack_schedule_ms: float | None = None
    submit_to_observed_playback_ms: float | None = None
    local_stop_ms: float | None = None

    @field_validator("*", mode="before")
    @classmethod
    def numeric_interval(cls, value):
        if value is not None and (
            type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 300000
        ):
            raise ValueError("Invalid timing interval")
        return value


class VoiceTimingReport(BaseModel):
    """Fixed-size report, deliberately unable to carry content or authority."""

    model_config = ConfigDict(extra="forbid")
    epoch: str = Field(pattern=r"^[a-f0-9]{32}$")
    utterance_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    spoken_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance: Literal["rtp_energy_proxy", "local_pause_proxy"]
    first_playback_epoch_ms: float | None = None
    timing: VoiceClientTiming

    @field_validator("first_playback_epoch_ms", mode="before")
    @classmethod
    def wall_observation(cls, value):
        if value is not None and (
            type(value) not in (float, int)
            or not math.isfinite(value)
            or not 0 <= value <= 32503680000000
        ):
            raise ValueError("Invalid observation timestamp")
        return value


@dataclass
class Measurement:
    """Request-local, content-free clocks; shared across child async contexts."""

    span: object
    origin: float
    tool_ms: float = 0


_current: ContextVar[Measurement | None] = ContextVar("voice_timing", default=None)


@contextmanager
def measurement():
    """One root per voice request, scoped to this async context."""
    with turn_span("aimms.voice.turn", ms_server_receipt=0) as span:
        token = _current.set(Measurement(span, time.perf_counter()))
        try:
            yield
        finally:
            mark("ms_result")
            _current.reset(token)


def attributes(**values):
    """Set only the shared tracing allowlist; never serialize metadata wholesale."""
    current = _current.get()
    if current:
        set_span_attrs(current.span, **values)


def mark(name):
    """A server-clock offset, not a browser/server subtraction."""
    current = _current.get()
    if current:
        attributes(**{name: (time.perf_counter() - current.origin) * 1000})


@contextmanager
def stage(name):
    """Time known server stages only, without content or exception recording."""
    current = _current.get()
    if current is None or name not in {"route", "tool"}:
        yield
        return
    started = time.perf_counter()
    with turn_span(f"aimms.voice.{name}") as span:
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            values = {f"ms_{name}": elapsed}
            if name == "tool":
                current.tool_ms += elapsed
                attributes(ms_tool=current.tool_ms)
            else:
                attributes(**values)
            set_span_attrs(span, **values)
