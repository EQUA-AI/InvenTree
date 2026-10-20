"""Truthful, bounded, varied waiting-progress policy (plan §5.1/§5.2).

Pure module (no Django, no provider): the schedule and budget for OPTIONAL
waiting speech, driven by verified application signals and an injectable
clock so every threshold is testable without sleeping.

Approved bounded schedule (VoiceScopeLatencyFixPlan §5.2, 2026-10-05):
first optional cue about 2 s after admission when the answer is still
unavailable; at most one further cue, no earlier than 8 s after admission
and never closer than ``MIN_OPTIONAL_GAP_S`` to the previous cue; hard cap
``MAX_OPTIONAL_PROMPTS`` optional cues per ordinary turn. These are policy
bounds taken from the approved plan -- not measured latency wins; tune them
after real measurements.

Truthfulness rules encoded here:

* stages come only from explicit application signals (``signal_*``); the
  authorized-read phrase is unreachable without ``signal_read_started``;
* preference ``off`` suppresses ONLY optional waiting speech -- required
  outcomes (errors, clarifications, readbacks) live in
  ``status_phrases.REQUIRED_OUTCOME_STAGES`` and bypass this budget;
* preference ``guided`` announces real stage changes within the same hard
  count and gap limits; ``brief`` follows the fixed schedule;
* ``invalidate`` (result ready / end / hidden / barge-in / thread change)
  stops every future prompt immediately, including never-spoken fillers;
* the tracker never accepts or stores content: only stage ids, counts and
  timestamps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ai.core.voice import status_phrases

if TYPE_CHECKING:
    from collections.abc import Callable

#: First optional cue after admission while the answer is still unavailable.
FIRST_OPTIONAL_DELAY_S = 2.0
#: Second optional cue: no earlier than this long after admission.
SECOND_OPTIONAL_DELAY_S = 8.0
#: Minimum silence between two optional cues (after the previous cue).
#: Explicit policy default, pending measurement -- not a measured win.
MIN_OPTIONAL_GAP_S = 3.0
#: Hard cap of optional waiting prompts per ordinary turn.
MAX_OPTIONAL_PROMPTS = 2

ProgressPreference = Literal["off", "brief", "guided"]

#: Invalidate reasons accepted from the application (content-free codes).
INVALIDATION_REASONS = frozenset({
    "result_ready",
    "end",
    "hidden",
    "barge_in",
    "thread_change",
    "error",
})

#: Stage order used for snapshots/telemetry; also the catalogue order.
STAGE_ORDER = status_phrases.PROGRESS_STAGES


@dataclass(frozen=True)
class ProgressPrompt:
    """One selected optional prompt. Text is always from the allow-list."""

    stage: str
    phrase: str
    number: int
    at: float


class ProgressTracker:
    """Per-turn optional waiting-speech budget and stage truth."""

    def __init__(
        self,
        *,
        session_key: str = "",
        locale: str | None = None,
        preference: ProgressPreference = "brief",
        clock: Callable[[], float] = time.monotonic,
        selector: status_phrases.StatusPhraseSelector | None = None,
    ) -> None:
        if preference not in ("off", "brief", "guided"):
            raise ValueError("unknown progress preference")
        self._locale = locale
        self._preference: ProgressPreference = preference
        self._clock = clock
        self._selector = selector or status_phrases.StatusPhraseSelector(session_key)
        self._admitted_at: float | None = None
        self._stage = "admitted"
        self._announced: set[str] = set()
        self._emitted = 0
        self._last_prompt_at: float | None = None
        self._invalidated = False

    # -- verified application signals (no content ever accepted) ---------- #
    def admit(self, at: float | None = None) -> None:
        """Record admission; optional prompts cannot precede it."""
        if self._admitted_at is None:
            self._admitted_at = self._clock() if at is None else at
            self._stage = "admitted"
            self._announced.add("admitted")

    def signal_queued(self) -> None:
        """The request is actually waiting behind another turn."""
        self._stage = "queued"

    def signal_read_started(self) -> None:
        """An authorized read ACTUALLY started. Never call speculatively."""
        self._stage = "read_active"

    def signal_still_working(self) -> None:
        """Work remains active after the initial update."""
        self._stage = "still_working"

    # -- budget ------------------------------------------------------------ #
    def due(self, at: float | None = None) -> ProgressPrompt | None:
        """Return and consume the next optional prompt, if one is due now."""
        if self._invalidated or self._preference == "off":
            return None
        if self._admitted_at is None or self._emitted >= MAX_OPTIONAL_PROMPTS:
            return None
        now = self._clock() if at is None else at
        if now - self._admitted_at < FIRST_OPTIONAL_DELAY_S:
            return None
        if self._emitted > 0:
            if self._last_prompt_at is not None and now - self._last_prompt_at < MIN_OPTIONAL_GAP_S:
                return None
            if self._preference == "brief" and now - self._admitted_at < SECOND_OPTIONAL_DELAY_S:
                return None
            if self._preference == "guided" and self._stage in self._announced:
                # Guided announces verified stage changes only.
                return None
        stage = self._next_stage()
        phrase = self._selector.select(stage, locale=self._locale)
        self._emitted += 1
        self._last_prompt_at = now
        self._announced.add(stage)
        return ProgressPrompt(stage=stage, phrase=phrase, number=self._emitted, at=now)

    def invalidate(self, reason: str) -> None:
        """Stop all future optional prompts (end/result/hidden/barge-in)."""
        if reason not in INVALIDATION_REASONS:
            raise ValueError("unknown invalidation reason")
        self._invalidated = True

    # -- state ------------------------------------------------------------- #
    @property
    def emitted(self) -> int:
        return self._emitted

    @property
    def invalidated(self) -> bool:
        return self._invalidated

    @property
    def current_stage(self) -> str:
        return self._stage

    def _next_stage(self) -> str:
        # The second brief cue is truthful as "still working" when nothing
        # newer than admission was signaled; a real signal always wins.
        if self._emitted >= 1 and self._stage == "admitted":
            return "still_working"
        return self._stage

    def snapshot(self) -> dict:
        """Content-free progress state for telemetry."""
        return {
            "stage": self._stage,
            "emitted": self._emitted,
            "invalidated": self._invalidated,
            "preference": self._preference,
        }
