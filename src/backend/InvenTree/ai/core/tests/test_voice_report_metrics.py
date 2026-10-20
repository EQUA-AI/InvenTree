"""Package E: report metrics stay telemetry-only and onset-honest.

Real ORM rows via the shared island schema helper (the island migration
graph defect is pre-existing and held -- see test_voice_playback_reconcile).
Schema failures are not swallowed here either.

Contract under test: the new optional client fields (``ms_queue``,
``feedback_onset_ms``, ``answer_onset_ms``) and report fields (``onset_kind``,
``coverage_reason``, ``generation``) are finite/bounded telemetry; a filler
row can never populate useful-answer onset; missing observations stay
missing with a coverage reason instead of a fabricated timestamp.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("django")

import django

django.setup()

from ai.core.tests.focus_test_db import ensure_island_tables  # noqa: E402
from ai.core.voice.timing import VoiceTimingReport  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402
from voice.models import VoiceUtterance  # noqa: E402
from voice.services import realtime, timing  # noqa: E402

ANSWER_TEXT = "Two likely causes."
STATUS_TEXT = "Let me check that."


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()
    yield


class _Env:
    def __init__(self, utterance_type="completed_answer", spoken=ANSWER_TEXT):
        self.user = get_user_model().objects.create_user(
            # Unique per env: a rejected report/insert must never make the
            # next fixture collide on a count-derived username.
            username=f"metrics-{uuid.uuid4().hex[:12]}",
            password="unused",
        )
        self.session = realtime.create_session(
            owner=self.user,
            thread_id="metrics",
            scope_key="test",
            policy_version="v1",
            limits=realtime.SessionLimits(),
        )
        self.kwargs = {
            "owner": self.user,
            "scope_key": "test",
            "session_id": self.session.pk,
            "limits": realtime.SessionLimits(),
        }
        self.epoch = timing.begin_epoch(**self.kwargs)["epoch"]
        self.utterance = realtime.persist_utterance(
            session=self.session,
            utterance_type=utterance_type,
            spoken_summary=spoken,
            # Real DB invariant (voice_utterance_answer_binds_response): only
            # a completed answer may bind a response id; status rows carry
            # none. The fixture honors the constraint instead of weakening it.
            response_id="turn-1" if utterance_type == "completed_answer" else "",
            turn_id="turn-1",
        )

    def report(self, **overrides):
        values = {
            "epoch": self.epoch,
            "utterance_id": str(self.utterance.pk),
            "spoken_hash": self.utterance.spoken_summary_hash,
            "provenance": "rtp_energy_proxy",
            "timing": {"submit_to_observed_playback_ms": 17.5},
        }
        values.update(overrides)
        return VoiceTimingReport(**values)


def test_answer_row_records_onset_kind_and_generation_as_fixed_fields():
    env = _Env()
    observation = env.report(
        first_playback_epoch_ms=timezone.now().timestamp() * 1000,
        onset_kind="answer",
        generation=5,
        timing={"submit_to_observed_playback_ms": 17.5, "answer_onset_ms": 3000.0},
    )
    assert timing.report(**env.kwargs, observation=observation) == {"accepted": True}
    row = VoiceUtterance.objects.get(pk=env.utterance.pk)
    assert row.timing_metrics["onset_kind"] == "answer"
    assert row.timing_metrics["generation"] == 5
    assert row.timing_metrics["answer_onset_ms"] == 3000.0  # noqa: RUF069 - exact wire fixture.
    assert row.first_playback_at is not None


def test_filler_row_never_populates_useful_answer_onset():
    env = _Env(utterance_type="interim_status", spoken=STATUS_TEXT)
    now_ms = timezone.now().timestamp() * 1000
    with pytest.raises(timing.TimingRejected):
        timing.report(
            **env.kwargs,
            observation=env.report(
                first_playback_epoch_ms=now_ms,
                onset_kind="answer",
            ),
        )
    with pytest.raises(timing.TimingRejected):
        timing.report(
            **env.kwargs,
            observation=env.report(
                timing={"answer_onset_ms": 3000.0},
            ),
        )
    row = VoiceUtterance.objects.get(pk=env.utterance.pk)
    assert row.first_playback_at is None
    assert not row.timing_metrics or "answer_onset_ms" not in row.timing_metrics


def test_missing_observation_stays_missing_with_coverage_reason():
    env = _Env()
    observation = env.report(
        first_playback_epoch_ms=None,
        coverage_reason="hidden_page",
        timing={"ms_queue": 120.0, "feedback_onset_ms": 900.0},
    )
    assert timing.report(**env.kwargs, observation=observation) == {"accepted": True}
    row = VoiceUtterance.objects.get(pk=env.utterance.pk)
    assert row.first_playback_at is None
    assert row.timing_metrics["coverage_reason"] == "hidden_page"
    assert row.timing_metrics["ms_queue"] == 120.0  # noqa: RUF069 - exact wire fixture.


def test_pause_proxy_may_report_queue_wait_but_never_invents_onset():
    env = _Env()
    observation = env.report(
        provenance="local_pause_proxy",
        first_playback_epoch_ms=None,
        timing={"local_stop_ms": 2, "ms_queue": 80.0},
    )
    assert timing.report(**env.kwargs, observation=observation) == {"accepted": True}
    row = VoiceUtterance.objects.get(pk=env.utterance.pk)
    assert row.first_playback_at is None
    assert row.timing_metrics["local_stop_ms"] == 2
    assert row.timing_metrics["ms_queue"] == 80.0  # noqa: RUF069 - exact wire fixture.
    with pytest.raises(timing.TimingRejected):
        timing.report(
            **env.kwargs,
            observation=env.report(
                provenance="local_pause_proxy",
                first_playback_epoch_ms=None,
                timing={"feedback_onset_ms": 900.0},
            ),
        )
