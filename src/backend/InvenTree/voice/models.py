"""Durable realtime Voice ledgers (WS4-T2).

These rows are audit/reconnect ledgers, never conversation history and never
an authorization store (contract §4.2). By design no model in this app has a
field for SDP, ICE, provider tokens, or audio bytes — the 2026-07-15 owner
decision forbids durable audio anywhere, and signaling material is ephemeral
session data. ``voice.tests.test_realtime_models`` enforces the absence of
such fields by name.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class VoiceSessionState(models.TextChoices):
    """Lifecycle of one user-visible realtime session."""

    CREATED = 'created', 'Created'
    CONNECTING = 'connecting', 'Connecting'
    ACTIVE = 'active', 'Active'
    ENDED = 'ended', 'Ended'
    FAILED = 'failed', 'Failed'
    EXPIRED = 'expired', 'Expired'


TERMINAL_SESSION_STATES = (
    VoiceSessionState.ENDED,
    VoiceSessionState.FAILED,
    VoiceSessionState.EXPIRED,
)


class VoiceTransport(models.TextChoices):
    """Audio transports a session may negotiate."""

    WEBRTC = 'webrtc', 'WebRTC (public preview)'
    RELAY = 'relay', 'Server WebSocket relay'


class TransportAttemptOutcome(models.TextChoices):
    """Terminal outcome of one transport negotiation attempt."""

    STARTED = 'started', 'Started'
    CONNECTED = 'connected', 'Connected'
    FAILED = 'failed', 'Failed'
    CLOSED = 'closed', 'Closed'


class VoiceUtteranceType(models.TextChoices):
    """What a persisted spoken text is allowed to be."""

    INTERIM_STATUS = 'interim_status', 'Interim status'
    COMPLETED_ANSWER = 'completed_answer', 'Completed answer'
    FAILURE_STATUS = 'failure_status', 'Failure status'


class PlaybackState(models.TextChoices):
    """Playback lifecycle of one utterance."""

    PENDING = 'pending', 'Pending'
    REQUESTED = 'requested', 'Requested'
    PLAYING = 'playing', 'Playing'
    DONE = 'done', 'Done'
    CANCELED = 'canceled', 'Canceled'
    FAILED = 'failed', 'Failed'


class VoiceSession(models.Model):
    """One explicitly user-started, bounded, visible realtime session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='voice_sessions',
    )
    thread_id = models.CharField(max_length=255)
    scope_key = models.CharField(max_length=255)
    scope_hash = models.CharField(max_length=64)
    state = models.CharField(
        max_length=16,
        choices=VoiceSessionState.choices,
        default=VoiceSessionState.CREATED,
    )
    transport = models.CharField(
        max_length=16, choices=VoiceTransport.choices, blank=True
    )
    azure_session_id = models.CharField(max_length=128, blank=True)
    policy_version = models.CharField(max_length=64)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    terminal_reason = models.CharField(max_length=64, blank=True)
    turn_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Owner-first indexes and terminal-state consistency."""

        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', 'state'], name='voice_session_owner_state'),
            models.Index(
                fields=['state', 'last_activity_at'], name='voice_session_sweep_idx'
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(state__in=[s.value for s in TERMINAL_SESSION_STATES])
                    & Q(ended_at__isnull=False)
                )
                | ~Q(state__in=[s.value for s in TERMINAL_SESSION_STATES]),
                name='voice_session_terminal_has_end',
            ),
            models.CheckConstraint(
                condition=~Q(scope_hash=''), name='voice_session_scope_required'
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        """Whether this session can never become active again."""
        return self.state in TERMINAL_SESSION_STATES


class VoiceTransportAttempt(models.Model):
    """One transport negotiation attempt; metadata only, never payloads."""

    session = models.ForeignKey(
        VoiceSession, on_delete=models.CASCADE, related_name='transport_attempts'
    )
    transport = models.CharField(max_length=16, choices=VoiceTransport.choices)
    outcome = models.CharField(
        max_length=16,
        choices=TransportAttemptOutcome.choices,
        default=TransportAttemptOutcome.STARTED,
    )
    reason = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Chronological per-session attempts."""

        ordering = ['created_at']
        indexes = [
            models.Index(
                fields=['session', 'created_at'], name='voice_attempt_session_idx'
            )
        ]


class VoiceUtterance(models.Model):
    """Exact text persisted before it may ever be spoken (contract §6.5).

    ``spoken_summary_hash`` is the SHA-256 of ``spoken_summary``; playback is
    only requested for text whose hash matches a persisted row, which is what
    makes exact TTS enforceable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        VoiceSession, on_delete=models.CASCADE, related_name='utterances'
    )
    turn_id = models.CharField(max_length=64, blank=True)
    utterance_type = models.CharField(max_length=20, choices=VoiceUtteranceType.choices)
    spoken_summary = models.TextField()
    spoken_summary_hash = models.CharField(max_length=64)
    response_id = models.CharField(max_length=64, blank=True)
    policy_version = models.CharField(max_length=64)
    playback_state = models.CharField(
        max_length=16, choices=PlaybackState.choices, default=PlaybackState.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Idempotent completed answers and non-empty spoken text."""

        ordering = ['created_at']
        indexes = [
            models.Index(
                fields=['session', 'utterance_type'], name='voice_utterance_session_idx'
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'response_id'],
                condition=Q(utterance_type=VoiceUtteranceType.COMPLETED_ANSWER.value)
                & ~Q(response_id=''),
                name='voice_utterance_one_answer',
            ),
            models.CheckConstraint(
                condition=~Q(spoken_summary='') & ~Q(spoken_summary_hash=''),
                name='voice_utterance_text_required',
            ),
            models.CheckConstraint(
                condition=Q(utterance_type=VoiceUtteranceType.COMPLETED_ANSWER.value)
                | Q(response_id=''),
                name='voice_utterance_answer_binds_response',
            ),
        ]


class CapturePurpose(models.TextChoices):
    """Governed capture destinations; nothing else may be recorded."""

    FAULT_INTAKE = 'fault_intake', 'Fault intake'
    CLOSEOUT = 'closeout', 'Closeout'


class CaptureState(models.TextChoices):
    """Lifecycle of one transcript-only capture (2026-07-15 re-cut)."""

    ACTIVE = 'active', 'Active'
    REVIEW = 'review', 'Review required'
    ACCEPTED = 'accepted', 'Accepted'
    COMMITTED = 'committed', 'Committed'
    CANCELED = 'canceled', 'Canceled'
    FAILED = 'failed', 'Failed'


TERMINAL_CAPTURE_STATES = (CaptureState.CANCELED, CaptureState.FAILED)


class VoiceCaptureSession(models.Model):
    """One purpose-bound push-to-talk capture. No audio is ever stored.

    The consent act is starting the capture (owner decision 2026-07-15);
    the version/time of the disclosure shown is recorded here. Audio is
    ephemeral in the realtime transport — only transcript revisions and
    their acceptance persist, under chat/record retention policy.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='voice_captures',
    )
    scope_key = models.CharField(max_length=255)
    scope_hash = models.CharField(max_length=64)
    purpose = models.CharField(max_length=16, choices=CapturePurpose.choices)
    target_work_order_id = models.PositiveIntegerField()
    target_version = models.PositiveIntegerField()
    state = models.CharField(
        max_length=16, choices=CaptureState.choices, default=CaptureState.ACTIVE
    )
    consent_version = models.CharField(max_length=32)
    consented_at = models.DateTimeField()
    accepted_revision = models.ForeignKey(
        'voice.VoiceTranscriptRevision',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='+',
    )
    policy_version = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    terminal_reason = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """One intent per key; consent always precedes capture."""

        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['owner', 'purpose', 'state'], name='voice_capture_owner_idx'
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'idempotency_key'], name='voice_capture_intent_key'
            ),
            models.CheckConstraint(
                condition=~Q(consent_version=''), name='voice_capture_consent_required'
            ),
            models.CheckConstraint(
                condition=~Q(scope_hash=''), name='voice_capture_scope_required'
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        """Whether this capture can never progress again."""
        return self.state in TERMINAL_CAPTURE_STATES


class VoiceTranscriptRevision(models.Model):
    """One immutable transcript revision; corrections append, never edit."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    capture = models.ForeignKey(
        VoiceCaptureSession, on_delete=models.CASCADE, related_name='revisions'
    )
    revision = models.PositiveIntegerField()
    full_text = models.TextField()
    content_hash = models.CharField(max_length=64)
    segments = models.JSONField(default=list, blank=True)
    language = models.CharField(max_length=16, default='en-US')
    provider = models.CharField(max_length=32, default='voice_live_realtime')
    supersedes = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, related_name='+'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    edit_reason = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Monotonic, unique, non-empty revisions."""

        ordering = ['revision']
        constraints = [
            models.UniqueConstraint(
                fields=['capture', 'revision'], name='voice_revision_monotonic'
            ),
            models.CheckConstraint(
                condition=~Q(full_text='') & ~Q(content_hash=''),
                name='voice_revision_text_required',
            ),
        ]


class VoiceTranscriptAcceptance(models.Model):
    """Append-only evidence that a human reviewed one exact revision."""

    revision = models.OneToOneField(
        VoiceTranscriptRevision, on_delete=models.PROTECT, related_name='acceptance'
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    content_hash = models.CharField(max_length=64)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Acceptance is immutable evidence, one row per revision."""

        ordering = ['accepted_at']
