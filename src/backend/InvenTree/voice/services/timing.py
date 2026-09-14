"""Rate-limited, owner-scoped observations. This service never touches authority."""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from ai.core.voice.timing import VoiceTimingReport
from voice.models import VoiceSession, VoiceUtterance
from voice.services import realtime


class TimingRejected(ValueError):  # noqa: N818 - stable telemetry refusal, not business failure
    """One bounded, non-disclosing refusal code at the HTTP boundary."""


def _reserve(session, kind, maximum):
    # Fixed-size counter with bounded expiry; no raw input in cache keys.
    key = f'voice:timing:{session.pk}:{kind}'
    try:
        if cache.add(key, 1, timeout=60):
            return
        if cache.incr(key) <= maximum:
            return
    except Exception:
        pass  # Cache outage disables telemetry, never the business action.
    raise TimingRejected('VOICE_TIMING_UNAVAILABLE')


def _active(owner, scope_key, session_id, limits):
    from aichat.models import ChatThread

    session = realtime.get_owned_session(
        owner=owner, scope_key=scope_key, session_id=session_id, limits=limits
    )
    if session.is_terminal:
        raise TimingRejected('VOICE_TIMING_UNAVAILABLE')
    current_scope = (
        ChatThread.objects
        .filter(id=session.thread_id, owner_id=owner)
        .values_list('analysis_scope_version', flat=True)
        .first()
        or 0
    )
    if current_scope != session.analysis_scope_version:
        raise TimingRejected('VOICE_TIMING_UNAVAILABLE')
    return session


@transaction.atomic
def begin_epoch(*, owner, scope_key, session_id, limits):
    """Rotate telemetry generation only; no activity/TTL refresh."""
    VoiceSession.objects.select_for_update().filter(pk=session_id, owner=owner).first()
    session = _active(owner, scope_key, session_id, limits)
    _reserve(session, 'epoch', 6)
    epoch = uuid.uuid4()
    VoiceSession.objects.filter(pk=session.pk).update(
        timing_epoch=epoch, timing_epoch_started_at=timezone.now()
    )
    return {'epoch': epoch.hex}


def invalidate_epoch(session):
    """Foreground loss invalidates reports without touching review state."""
    VoiceSession.objects.filter(pk=session.pk).update(
        timing_epoch=None, timing_epoch_started_at=None
    )


@transaction.atomic
def report(*, owner, scope_key, session_id, limits, observation: VoiceTimingReport):
    """First valid observation wins; duplicate reports are harmless."""
    VoiceSession.objects.select_for_update().filter(pk=session_id, owner=owner).first()
    session = _active(owner, scope_key, session_id, limits)
    _reserve(session, 'report', 120)
    if (
        not session.timing_epoch
        or session.timing_epoch.hex != observation.epoch
        or not session.timing_epoch_started_at
    ):
        raise TimingRejected('VOICE_TIMING_UNAVAILABLE')
    utterance = (
        VoiceUtterance.objects
        .select_for_update()
        .filter(
            pk=observation.utterance_id,
            session=session,
            spoken_summary_hash=observation.spoken_hash,
            created_at__gte=session.timing_epoch_started_at,
        )
        .first()
    )
    if utterance is None:
        raise TimingRejected('VOICE_TIMING_UNAVAILABLE')
    values = observation.timing.model_dump(exclude_none=True)
    now = timezone.now()
    observed = None
    if observation.first_playback_epoch_ms is not None:
        observed = datetime.fromtimestamp(
            observation.first_playback_epoch_ms / 1000,
            dt_timezone.utc if timezone.is_aware(now) else None,
        )
        if (
            observation.provenance != 'rtp_energy_proxy'
            or utterance.playback_state in ('canceled', 'failed')
            or not max(
                utterance.created_at - timedelta(seconds=2), now - timedelta(minutes=5)
            )
            <= observed
            <= now + timedelta(seconds=2)
        ):
            raise TimingRejected('VOICE_TIMING_UNAVAILABLE')
    if observation.provenance == 'local_pause_proxy' and set(values) - {
        'local_stop_ms'
    }:
        raise TimingRejected('VOICE_TIMING_UNAVAILABLE')
    previous = utterance.timing_metrics or {}
    # Store provenance by fixed field, not arbitrary metadata from the client.
    merged = {**values, **previous}
    updates = {}
    if merged != previous:
        updates['timing_metrics'] = merged
    if observed is not None and utterance.first_playback_at is None:
        updates['first_playback_at'] = observed
    if updates:
        if utterance.timing_reported_at is None:
            updates['timing_reported_at'] = now
        VoiceUtterance.objects.filter(pk=utterance.pk).update(**updates)
    return {'accepted': True}
