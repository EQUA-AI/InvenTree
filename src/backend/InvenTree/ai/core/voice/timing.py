"""Optional content-free timing. No timing signal confers execution authority."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from ai.core.tracing import set_span_attrs, turn_span
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VoiceClientTiming(BaseModel):
    """Same-browser-clock intervals; absent values are unknown, never zero."""

    model_config = ConfigDict(extra="forbid")
    speech_to_final_ms: float | None = None
    final_to_submit_ms: float | None = None
    #: Client queue wait (enqueue to actual submit).
    ms_queue: float | None = None
    ack_schedule_ms: float | None = None
    submit_to_observed_playback_ms: float | None = None
    #: First audible feedback onset (may be a filler), relative to submit.
    feedback_onset_ms: float | None = None
    #: Useful-answer onset, relative to submit. Never populated by filler.
    answer_onset_ms: float | None = None
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
    #: What ``first_playback_epoch_ms`` observed: filler onset and useful
    #: answer onset are separate observations and never interchangeable.
    onset_kind: Literal["filler", "answer"] | None = None
    #: Why an onset observation is absent. Missing observations stay
    #: missing (null) with this reason -- never a fabricated timestamp.
    coverage_reason: (
        Literal["not_observed", "hidden_page", "canceled", "no_audio_proxy", "late_or_out_of_order"]
        | None
    ) = None
    #: Client-local output generation stamp for honest late/out-of-order
    #: binding. Telemetry only.
    generation: int | None = None
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

    @field_validator("generation", mode="before")
    @classmethod
    def bounded_generation(cls, value):
        if value is not None and (type(value) is not int or not 0 <= value <= 2**31 - 1):
            raise ValueError("Invalid generation")
        return value

    @model_validator(mode="after")
    def onset_honesty(self):
        """Separate filler onset from useful answer; keep absence honest."""
        if self.onset_kind is not None and self.first_playback_epoch_ms is None:
            raise ValueError("onset kind requires an onset observation")
        if self.coverage_reason is not None and self.first_playback_epoch_ms is not None:
            raise ValueError("coverage reason only explains a missing onset")
        return self


@dataclass
class Measurement:
    """Request-local, content-free clocks; shared across child async contexts."""

    span: object
    origin: float
    tool_ms: float = 0
    model_ms: float = 0
    #: Content-free phase ledger (stage/duration name -> milliseconds).
    phases: dict[str, float] = field(default_factory=dict)
    #: Content-free identity binding: opaque ids, kind and versions only.
    bindings: dict[str, object] = field(default_factory=dict)

    def record_phase(self, name: str, elapsed_ms: float) -> None:
        """Accumulate one content-free phase duration (milliseconds)."""
        if name not in STAGE_NAMES | DURATION_MARKS:
            return
        if type(elapsed_ms) not in (int, float) or not math.isfinite(elapsed_ms):
            return
        self.phases[name] = self.phases.get(name, 0.0) + float(elapsed_ms)

    def bind(self, **values) -> None:
        """Retain content-free id/kind/version bindings; drop everything else."""
        from ai.core.tracing import opaque_voice_id

        for key, value in values.items():
            if key not in _BIND_KEYS:
                continue
            if key in _ID_KEYS:
                identifier = opaque_voice_id(value)
                if identifier:
                    self.bindings[key] = identifier
            elif key == "kind":
                if type(value) is str and value in _BIND_KINDS:
                    self.bindings[key] = value
            elif type(value) is int and 0 <= value <= 10**9:
                self.bindings[key] = value
        attributes(**self.bindings)

    def as_dict(self) -> dict:
        """Content-free telemetry snapshot (never content or exceptions)."""
        return {"phases": dict(self.phases), "bindings": dict(self.bindings)}


#: Server stage names with a monotonic origin-to-completion measurement.
#: ``lock_wait`` is receipt-to-session-lock: open it BEFORE acquiring the
#: session lock so the origin is the request receipt (integration hook).
STAGE_NAMES = frozenset({
    "route",
    "tool",
    "lock_wait",
    "admission",
    "routing",
    "model",
    "final_persist",
    "cancellation",
})
#: Event-driven durations recorded from correlated provider events.
DURATION_MARKS = frozenset({"dispatch_ack", "dispatch_terminal", "cancel_terminal"})

#: Content-free binding keys and the closed kind vocabulary.
_BIND_KEYS = frozenset({
    "utterance_id",
    "dispatch_id",
    "turn_id",
    "kind",
    "generation",
    "focus_version",
    "scope_version",
    "turn_sequence",
})
_ID_KEYS = frozenset({"utterance_id", "dispatch_id", "turn_id"})
_BIND_KINDS = frozenset({
    "answer",
    "status",
    "prompt",
    "interim_status",
    "completed_answer",
    "failure_status",
})

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


def capture_measurement() -> Measurement | None:
    """The request-local measurement, for event-driven durations elsewhere."""
    return _current.get()


def bind(**values) -> None:
    """Bind content-free identity to the current measurement, if any."""
    current = _current.get()
    if current:
        current.bind(**values)


def record_duration(name: str, duration_ms: float, measurement: Measurement | None = None) -> None:
    """Record one event-driven duration (e.g. dispatch->ack) in milliseconds.

    The caller's own monotonic clock measured it; nothing here subtracts
    across clocks. Telemetry only: no timing signal confers authority.
    """
    current = measurement if measurement is not None else _current.get()
    if current is None or name not in DURATION_MARKS:
        return
    if (
        type(duration_ms) not in (int, float)
        or not math.isfinite(duration_ms)
        or not 0 <= duration_ms <= 300000
    ):
        return
    current.record_phase(name, float(duration_ms))
    attributes(**{f"ms_{name}": float(duration_ms)})


def snapshot(measurement: Measurement | None = None) -> dict | None:
    """Content-free phases + bindings for telemetry consumption."""
    current = measurement if measurement is not None else _current.get()
    return current.as_dict() if current is not None else None


@contextmanager
def stage(name):
    """Time known server stages only, without content or exception recording."""
    current = _current.get()
    if current is None or name not in STAGE_NAMES:
        yield
        return
    started = time.perf_counter()
    with turn_span(f"aimms.voice.{name}") as span:
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            current.record_phase(name, elapsed)
            values = {f"ms_{name}": elapsed}
            if name == "tool":
                current.tool_ms += elapsed
                attributes(ms_tool=current.tool_ms)
            elif name == "model":
                current.model_ms += elapsed
                attributes(ms_model=current.model_ms)
            else:
                attributes(**values)
            set_span_attrs(span, **values)
