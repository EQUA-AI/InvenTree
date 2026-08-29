"""Realtime session lifecycle authority (WS4-T3/T8/T9 persistence half).

Owner/scope authorization precedes every lookup; limits and expiry are
enforced server-side; utterances are persisted before any speech request
(the FastAPI layer builds provider payloads only from rows returned here).
All functions are synchronous Django ORM code — the ASGI layer wraps them
with ``sync_to_async``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from voice.models import (
    TERMINAL_SESSION_STATES,
    PlaybackState,
    VoiceSession,
    VoiceSessionState,
    VoiceTransport,
    VoiceTransportAttempt,
    VoiceUtterance,
    VoiceUtteranceType,
)


class VoiceSessionError(Exception):
    """Base class carrying a stable error code."""

    code = 'VOICE_SESSION_UNAVAILABLE'


class VoiceSessionForbidden(VoiceSessionError):  # noqa: N818
    """Unknown session or a session the actor does not own."""

    code = 'VOICE_SESSION_FORBIDDEN'


class VoiceSessionLimit(VoiceSessionError):  # noqa: N818
    """The actor already runs the maximum number of active sessions."""

    code = 'VOICE_SESSION_LIMIT'


class VoiceSessionExpired(VoiceSessionError):  # noqa: N818
    """The session aged or idled out and cannot be used again."""

    code = 'VOICE_SESSION_EXPIRED'


class ExactSpeechConflict(VoiceSessionError):  # noqa: N818
    """A different completed answer already exists for this response id."""

    code = 'IDEMPOTENCY_CONFLICT'


@dataclass(frozen=True)
class SessionLimits:
    """Server-side caps supplied from validated settings."""

    max_active_per_user: int = 1
    idle_timeout_s: int = 300
    max_age_s: int = 3600
    max_turns: int = 100


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def create_session(
    *,
    owner,
    thread_id: str,
    scope_key: str,
    policy_version: str,
    limits: SessionLimits,
    analysis_scope_version: int = 0,
) -> VoiceSession:
    """Create one owned session after enforcing the concurrency limit."""
    if not scope_key:
        raise VoiceSessionError('scope is unresolved')
    with transaction.atomic():
        expire_stale_sessions(owner=owner, limits=limits)
        active = (
            VoiceSession.objects
            .select_for_update()
            .filter(owner=owner, scope_key=scope_key)
            .exclude(state__in=[s.value for s in TERMINAL_SESSION_STATES])
            .count()
        )
        if active >= limits.max_active_per_user:
            raise VoiceSessionLimit('active session limit reached')
        return VoiceSession.objects.create(
            owner=owner,
            thread_id=thread_id,
            scope_key=scope_key,
            scope_hash=_sha256(scope_key),
            policy_version=policy_version,
            analysis_scope_version=analysis_scope_version,
        )


def get_owned_session(
    *, owner, scope_key: str, session_id, limits: SessionLimits
) -> VoiceSession:
    """Return the owner's in-scope session, expiring it when out of bounds.

    Unknown ids and other owners' ids raise the same error so existence is
    never disclosed across owners.
    """
    try:
        session = VoiceSession.objects.get(
            id=session_id, owner=owner, scope_key=scope_key
        )
    except (
        VoiceSession.DoesNotExist,
        DjangoValidationError,
        ValueError,
        TypeError,
    ) as exc:
        raise VoiceSessionForbidden('no such session') from exc
    if session.is_terminal:
        return session
    now = timezone.now()
    idle_deadline = session.last_activity_at + timedelta(seconds=limits.idle_timeout_s)
    age_deadline = session.created_at + timedelta(seconds=limits.max_age_s)
    if now > idle_deadline or now > age_deadline:
        _terminate(session, VoiceSessionState.EXPIRED, 'session_bounds')
        raise VoiceSessionExpired('session expired')
    return session


def touch_session(
    *, session: VoiceSession, limits: SessionLimits, count_turn: bool = False
) -> VoiceSession:
    """Record activity; enforce the per-session turn budget."""
    if session.is_terminal:
        raise VoiceSessionExpired('session already ended')
    session.last_activity_at = timezone.now()
    update_fields = ['last_activity_at', 'updated_at']
    if count_turn:
        session.turn_count += 1
        update_fields.append('turn_count')
        if session.turn_count > limits.max_turns:
            _terminate(session, VoiceSessionState.EXPIRED, 'turn_budget')
            raise VoiceSessionExpired('turn budget exhausted')
    if session.state == VoiceSessionState.CREATED:
        session.state = VoiceSessionState.ACTIVE
        update_fields.append('state')
    session.save(update_fields=update_fields)
    return session


def end_session(*, session: VoiceSession, reason: str = 'user_ended') -> VoiceSession:
    """Idempotently end a session and cancel queued playback."""
    if not session.is_terminal:
        _terminate(session, VoiceSessionState.ENDED, reason)
    return session


def fail_session(*, session: VoiceSession, reason: str) -> VoiceSession:
    """Mark a session failed after a provider/policy violation."""
    if not session.is_terminal:
        _terminate(session, VoiceSessionState.FAILED, reason)
    return session


def _terminate(session: VoiceSession, state: VoiceSessionState, reason: str) -> None:
    session.state = state
    session.terminal_reason = reason[:64]
    session.ended_at = timezone.now()
    session.save(update_fields=['state', 'terminal_reason', 'ended_at', 'updated_at'])
    session.utterances.filter(
        playback_state__in=[PlaybackState.PENDING, PlaybackState.REQUESTED]
    ).update(playback_state=PlaybackState.CANCELED)


def record_transport_attempt(
    *, session: VoiceSession, transport: str, outcome: str, reason: str = ''
) -> VoiceTransportAttempt:
    """Append one transport negotiation outcome (metadata only)."""
    if transport not in VoiceTransport.values:
        raise VoiceSessionError('unknown transport')
    attempt = VoiceTransportAttempt.objects.create(
        session=session, transport=transport, outcome=outcome, reason=reason[:64]
    )
    if outcome == 'connected':
        session.transport = transport
        session.save(update_fields=['transport', 'updated_at'])
    return attempt


def persist_utterance(
    *,
    session: VoiceSession,
    utterance_type: str,
    spoken_summary: str,
    response_id: str = '',
    turn_id: str = '',
    policy_version: str = '',
) -> VoiceUtterance:
    """Persist exact spoken text before any speech request (§6.5).

    Replaying the same completed answer returns the stored row; a different
    text under the same response id is a conflict, never an update.
    """
    if session.is_terminal:
        raise VoiceSessionExpired('session already ended')
    text = spoken_summary.strip()
    if not text:
        raise VoiceSessionError('spoken summary is empty')
    digest = _sha256(text)
    try:
        with transaction.atomic():
            return VoiceUtterance.objects.create(
                session=session,
                utterance_type=utterance_type,
                spoken_summary=text,
                spoken_summary_hash=digest,
                response_id=response_id,
                turn_id=turn_id,
                policy_version=policy_version or session.policy_version,
            )
    except IntegrityError:
        existing = VoiceUtterance.objects.get(
            session=session,
            response_id=response_id,
            utterance_type=VoiceUtteranceType.COMPLETED_ANSWER,
        )
        if existing.spoken_summary_hash != digest:
            raise ExactSpeechConflict(
                'a different answer is already bound to this response id'
            ) from None
        return existing


def mark_playback(*, utterance: VoiceUtterance, state: str) -> VoiceUtterance:
    """Advance playback state without ever mutating the spoken text."""
    if state not in PlaybackState.values:
        raise VoiceSessionError('unknown playback state')
    utterance.playback_state = state
    utterance.save(update_fields=['playback_state', 'updated_at'])
    return utterance


def expire_stale_sessions(*, limits: SessionLimits, owner=None) -> int:
    """Sweep sessions past their idle/age bounds (WS4-T9 orphan recovery)."""
    now = timezone.now()
    queryset = VoiceSession.objects.exclude(
        state__in=[s.value for s in TERMINAL_SESSION_STATES]
    )
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    expired = 0
    for session in queryset.filter(
        last_activity_at__lt=now - timedelta(seconds=limits.idle_timeout_s)
    ) | queryset.filter(created_at__lt=now - timedelta(seconds=limits.max_age_s)):
        _terminate(session, VoiceSessionState.EXPIRED, 'orphan_sweep')
        expired += 1
    return expired
