"""Package E/F: truthful bounded varied progress catalogue and schedule.

Pure tests (no database, no provider): the allow-listed localized stage
variants, the deterministic per-session rotation, and the approved bounded
waiting schedule (plan §5.1/§5.2). The tracker is a pure clock-driven state
machine here, so every threshold is exercised with a fake clock.

Contract delta (2026-10-05, VoiceScopeLatencyFixPlan §5.2, user-approved):
the single historical 4 s "Let me check that" timer is replaced by an
explicit bounded schedule -- first optional cue at 2 s after admission, a
second cue no earlier than 8 s after admission and never closer than the
minimum gap to the previous cue, at most two optional cues per turn. These
are policy bounds taken from the approved plan, NOT measured latency wins.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from ai.core.voice import progress, status_phrases


def _selector(session_key="session-1"):
    return status_phrases.StatusPhraseSelector(session_key)


# --------------------------------------------------------------------------- #
# Catalogue: allow-listed, localized, content-free                             #
# --------------------------------------------------------------------------- #
def test_every_stage_variant_is_allow_listed_and_localized():
    assert set(progress.STAGE_ORDER) == set(status_phrases.PROGRESS_STAGES)
    for stage, phrases in status_phrases.STAGE_PHRASES.items():
        assert phrases, stage
        for phrase in phrases:
            assert phrase in status_phrases.ALLOWED_STATUS_PHRASES, phrase
            localized = status_phrases.localized_status_phrase(phrase, "es")
            assert localized in status_phrases.ALLOWED_STATUS_PHRASES, localized
            assert localized != phrase, phrase
            # Deterministic English fallback outside the locale allow-list.
            assert status_phrases.localized_status_phrase(phrase, "xx") == phrase
            # Static catalogue: no template slots that content could fill.
            assert "{" not in phrase and "}" not in phrase


def test_catalogue_holds_only_the_approved_stages():
    assert status_phrases.PROGRESS_STAGES == (
        "admitted",
        "queued",
        "read_active",
        "still_working",
        "answer_in_chat",
        "failed",
        "incomplete",
    )
    assert (
        frozenset({
            "admitted",
            "queued",
            "read_active",
            "still_working",
        })
        == status_phrases.OPTIONAL_WAITING_STAGES
    )
    assert (
        frozenset({
            "answer_in_chat",
            "failed",
            "incomplete",
        })
        == status_phrases.REQUIRED_OUTCOME_STAGES
    )
    # Terminal truthfulness: a failed turn never rotates to the incomplete
    # phrase and vice versa -- the variants are semantically distinct.
    assert status_phrases.STAGE_PHRASES["failed"] == (status_phrases.TURN_FAILED,)
    assert status_phrases.STAGE_PHRASES["incomplete"] == (status_phrases.ANSWER_INCOMPLETE,)


def test_phrase_policy_version_is_recorded_for_audit():
    assert status_phrases.STATUS_PHRASE_POLICY_VERSION.startswith("status-v")


# --------------------------------------------------------------------------- #
# Rotation: deterministic per session/stage, no immediate repetition           #
# --------------------------------------------------------------------------- #
def test_rotation_is_deterministic_per_session_and_stage():
    first = [_selector("session-a").select("admitted") for _ in range(1)]
    again = [_selector("session-a").select("admitted") for _ in range(1)]
    assert first == again
    a = _selector("session-a")
    b = _selector("session-a")
    assert [a.select("still_working") for _ in range(5)] == [
        b.select("still_working") for _ in range(5)
    ]


def test_rotation_never_repeats_immediately_and_stays_in_the_stage():
    selector = _selector("session-rot")
    seen = [selector.select("admitted") for _ in range(6)]
    variants = status_phrases.STAGE_PHRASES["admitted"]
    assert all(phrase in variants for phrase in seen)
    assert all(left != right for left, right in pairwise(seen))
    assert set(seen) == set(variants)


def test_rotation_is_localized_per_stage():
    selector = _selector("session-es")
    phrase = selector.select("still_working", locale="es")
    assert phrase in status_phrases.ALLOWED_STATUS_PHRASES
    assert phrase not in status_phrases.STAGE_PHRASES["still_working"]


# --------------------------------------------------------------------------- #
# Schedule: bounded, fake-clock behaviour                                     #
# --------------------------------------------------------------------------- #
def _tracker(preference="brief", **kwargs):
    clock = [0.0]
    tracker = progress.ProgressTracker(
        session_key="session-1", preference=preference, clock=lambda: clock[0], **kwargs
    )
    return tracker, clock


def test_no_optional_prompt_before_the_first_delay():
    tracker, clock = _tracker()
    tracker.admit()
    clock[0] = progress.FIRST_OPTIONAL_DELAY_S - 0.01
    assert tracker.due() is None
    clock[0] = progress.FIRST_OPTIONAL_DELAY_S
    prompt = tracker.due()
    assert prompt is not None
    assert prompt.number == 1
    assert prompt.stage == "admitted"
    assert prompt.phrase in status_phrases.STAGE_PHRASES["admitted"]


def test_second_prompt_is_bounded_by_time_gap_and_count():
    tracker, clock = _tracker()
    tracker.admit()
    clock[0] = 2.0
    assert tracker.due() is not None
    # Inside the gap: no second prompt.
    clock[0] = 2.0 + progress.MIN_OPTIONAL_GAP_S - 0.01
    assert tracker.due() is None
    # Past the gap but before the second schedule point: still none.
    clock[0] = 7.9
    assert tracker.due() is None
    clock[0] = 8.0
    second = tracker.due()
    assert second is not None
    assert second.number == 2
    assert second.stage == "still_working"
    # Hard cap: never a third optional prompt.
    clock[0] = 1000.0
    assert tracker.due() is None


def test_gap_after_prior_prompt_is_enforced_even_late():
    tracker, clock = _tracker()
    tracker.admit()
    clock[0] = 6.0  # first cue lands late (e.g. real dispatch delay)
    assert tracker.due() is not None
    clock[0] = 8.0  # second schedule point reached, but gap too small
    assert tracker.due() is None
    clock[0] = 6.0 + progress.MIN_OPTIONAL_GAP_S
    assert tracker.due() is not None


def test_queued_stage_never_invents_read_active():
    tracker, clock = _tracker()
    tracker.admit()
    tracker.signal_queued()
    clock[0] = 2.0
    prompt = tracker.due()
    assert prompt.stage == "queued"
    assert prompt.phrase in status_phrases.STAGE_PHRASES["queued"]
    assert tracker.emitted == 1


def test_read_active_is_spoken_only_when_actually_signaled():
    tracker, clock = _tracker()
    tracker.admit()
    clock[0] = 2.0
    first = tracker.due()
    assert first.stage == "admitted"  # no read signal yet: nothing to claim
    tracker, clock = _tracker(preference="guided")
    tracker.admit()
    tracker.signal_read_started()
    clock[0] = 2.0
    prompt = tracker.due()
    assert prompt.stage == "read_active"
    assert prompt.phrase in status_phrases.STAGE_PHRASES["read_active"]


def test_guided_announces_only_real_stage_changes_within_hard_bounds():
    tracker, clock = _tracker(preference="guided")
    tracker.admit()
    clock[0] = 2.0
    assert tracker.due() is not None  # announces admission
    # No stage change: silence, despite time passing.
    clock[0] = 20.0
    assert tracker.due() is None
    tracker.signal_read_started()
    clock[0] = 20.0  # a real change is announced within the hard bounds
    second = tracker.due()
    assert second is not None
    assert second.number == 2
    assert second.stage == "read_active"
    # Hard count holds for guided too.
    tracker.signal_still_working()
    clock[0] = 100.0
    assert tracker.due() is None


def test_guided_stage_change_still_respects_the_minimum_gap():
    tracker, clock = _tracker(preference="guided")
    tracker.admit()
    clock[0] = 2.0
    assert tracker.due() is not None
    tracker.signal_read_started()
    clock[0] = 2.0 + progress.MIN_OPTIONAL_GAP_S - 0.01
    assert tracker.due() is None
    clock[0] = 2.0 + progress.MIN_OPTIONAL_GAP_S
    assert tracker.due() is not None


def test_off_suppresses_only_optional_waiting_prompts():
    tracker, clock = _tracker(preference="off")
    tracker.admit()
    tracker.signal_read_started()
    clock[0] = 100.0
    assert tracker.due() is None
    # Required outcomes (errors, clarifications, readbacks) are NOT optional
    # waiting speech and stay reachable regardless of the preference.
    selector = _selector("session-off")
    for stage in status_phrases.REQUIRED_OUTCOME_STAGES:
        assert selector.select(stage) in status_phrases.STAGE_PHRASES[stage]


def test_invalidation_stops_future_prompts_including_unsent_fillers():
    for reason in ("result_ready", "end", "hidden", "barge_in", "thread_change"):
        tracker, clock = _tracker()
        tracker.admit()
        tracker.invalidate(reason)
        clock[0] = 1000.0
        assert tracker.due() is None, reason
        assert tracker.emitted == 0


def test_invalidation_between_prompts_stops_the_second():
    tracker, clock = _tracker()
    tracker.admit()
    clock[0] = 2.0
    assert tracker.due() is not None
    tracker.invalidate("barge_in")
    clock[0] = 100.0
    assert tracker.due() is None


def test_tracker_never_carries_content():
    tracker, clock = _tracker()
    tracker.admit()
    clock[0] = 2.0
    prompt = tracker.due()
    state = tracker.snapshot()
    assert "PRIVATE_SENTINEL" not in str(state)
    assert set(state) <= {"stage", "emitted", "invalidated", "preference"}
    assert prompt.phrase in status_phrases.ALLOWED_STATUS_PHRASES


def test_schedule_constants_are_the_approved_bounded_values():
    assert pytest.approx(2.0) == progress.FIRST_OPTIONAL_DELAY_S
    assert pytest.approx(8.0) == progress.SECOND_OPTIONAL_DELAY_S
    assert progress.SECOND_OPTIONAL_DELAY_S > progress.FIRST_OPTIONAL_DELAY_S
    assert progress.MIN_OPTIONAL_GAP_S > 0
    assert progress.MAX_OPTIONAL_PROMPTS == 2
    # The legacy single-timer constant is the bridge for the un-integrated
    # route path: it equals the first optional cue, no longer the old 4 s.
    assert pytest.approx(progress.FIRST_OPTIONAL_DELAY_S) == status_phrases.INTERIM_STATUS_DELAY_S
