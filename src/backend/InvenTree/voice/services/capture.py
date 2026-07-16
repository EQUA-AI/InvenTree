"""Transcript-only governed capture (WS8/WS10 per the 2026-07-15 re-cut).

No audio is ever stored: revisions arrive from the realtime transcription
path (or a human correction) and every correction appends an immutable row.
Acceptance binds one exact revision by hash. Destination handoffs fail
closed until their canonical substrate exists — the Repair intake service
(WS11) for ``fault_intake`` and the live Feature #15 ``CloseoutCapture``
contract for ``closeout``.
"""

from __future__ import annotations

import hashlib

from django.db import IntegrityError, transaction
from django.utils import timezone

from voice.models import (
    CapturePurpose,
    CaptureState,
    VoiceCaptureSession,
    VoiceTranscriptAcceptance,
    VoiceTranscriptRevision,
)


class CaptureError(Exception):
    """Base class carrying a stable error code."""

    code = 'CAPTURE_STATE_CONFLICT'


class CaptureNotFound(CaptureError):  # noqa: N818
    """Unknown capture, or one the actor does not own."""

    code = 'CAPTURE_NOT_FOUND'


class PurposeUnavailable(CaptureError):  # noqa: N818
    """The purpose is not enabled by deployment policy."""

    code = 'CAPTURE_PURPOSE_UNSUPPORTED'


class RevisionStale(CaptureError):  # noqa: N818
    """The referenced revision is no longer the latest."""

    code = 'TRANSCRIPT_REVISION_STALE'


class DestinationUnavailable(CaptureError):  # noqa: N818
    """The canonical destination substrate does not exist yet."""

    code = 'DESTINATION_UNAVAILABLE'


class CaptureTargetInvalid(CaptureError):  # noqa: N818
    """The declared target does not exist in the trusted application store."""

    code = 'CAPTURE_TARGET_INVALID'


class DestinationStale(CaptureError):  # noqa: N818
    """The target version changed before capture creation."""

    code = 'DESTINATION_STALE'


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


_FORBIDDEN_METADATA_KEY_FRAGMENTS = (
    'audio',
    'blob',
    'credential',
    'file',
    'ice',
    'media',
    'recording',
    'sdp',
    'secret',
    'token',
)
_MAX_TRANSCRIPT_CHARS = 8_000
_MAX_SEGMENTS = 1_000


def _validate_transcript_metadata(value) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(
                fragment in normalized for fragment in _FORBIDDEN_METADATA_KEY_FRAGMENTS
            ):
                raise CaptureError('transcript metadata contains forbidden data')
            _validate_transcript_metadata(nested)
    elif isinstance(value, list):
        if len(value) > _MAX_SEGMENTS:
            raise CaptureError('transcript metadata exceeds bounds')
        for nested in value:
            _validate_transcript_metadata(nested)
    elif isinstance(value, str):
        if len(value) > _MAX_TRANSCRIPT_CHARS:
            raise CaptureError('transcript metadata exceeds bounds')
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise CaptureError('transcript metadata has an invalid type')


def create_capture(
    *,
    owner,
    scope_key: str,
    purpose: str,
    work_order_id: int,
    work_order_version: int,
    consent_version: str,
    idempotency_key: str,
    policy_version: str,
    enabled_purposes: tuple[str, ...] = (),
) -> VoiceCaptureSession:
    """Create (or exactly replay) one consented, purpose-bound capture."""
    if purpose not in CapturePurpose.values:
        raise PurposeUnavailable(f'unknown purpose {purpose!r}')
    if purpose not in enabled_purposes:
        raise PurposeUnavailable(f'{purpose} is not enabled for this deployment')
    if not consent_version:
        raise CaptureError('consent version is required')
    if not scope_key:
        raise CaptureError('scope is unresolved')
    try:
        with transaction.atomic():
            return VoiceCaptureSession.objects.create(
                owner=owner,
                scope_key=scope_key,
                scope_hash=_sha256(scope_key),
                purpose=purpose,
                target_work_order_id=work_order_id,
                target_version=work_order_version,
                consent_version=consent_version,
                consented_at=timezone.now(),
                policy_version=policy_version,
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        existing = VoiceCaptureSession.objects.get(
            owner=owner, idempotency_key=idempotency_key
        )
        if (
            existing.purpose != purpose
            or existing.target_work_order_id != work_order_id
            or existing.target_version != work_order_version
            or existing.scope_key != scope_key
            or existing.consent_version != consent_version
            or existing.policy_version != policy_version
        ):
            raise CaptureError(
                'a different capture intent already uses this key'
            ) from None
        return existing


def get_owned_capture(*, owner, scope_key: str, capture_id) -> VoiceCaptureSession:
    """Owner/scope-safe lookup; existence never leaks across boundaries."""
    try:
        return VoiceCaptureSession.objects.get(
            id=capture_id, owner=owner, scope_key=scope_key
        )
    except Exception as exc:
        raise CaptureNotFound('no such capture') from exc


def append_revision(
    *,
    capture: VoiceCaptureSession,
    full_text: str,
    created_by,
    segments: list | None = None,
    language: str = 'en-US',
    provider: str = 'voice_live_realtime',
    edit_reason: str = '',
) -> VoiceTranscriptRevision:
    """Append one immutable revision; corrections supersede, never mutate."""
    text = (full_text or '').strip()
    if not text:
        raise CaptureError('transcript text is required')
    if len(text) > _MAX_TRANSCRIPT_CHARS:
        raise CaptureError('transcript text exceeds bounds')
    normalized_segments = [] if segments is None else segments
    if not isinstance(normalized_segments, list):
        raise CaptureError('transcript segments must be a list')
    _validate_transcript_metadata(normalized_segments)
    with transaction.atomic():
        locked = VoiceCaptureSession.objects.select_for_update().get(pk=capture.pk)
        if locked.is_terminal or locked.state == CaptureState.COMMITTED:
            raise CaptureError(f'capture is {locked.state}')
        latest = locked.revisions.order_by('-revision').first()
        revision = VoiceTranscriptRevision.objects.create(
            capture=locked,
            revision=(latest.revision + 1) if latest else 1,
            full_text=text,
            content_hash=_sha256(text),
            segments=normalized_segments,
            language=language,
            provider=provider,
            supersedes=latest,
            created_by=created_by,
            edit_reason=edit_reason[:128],
        )
        if locked.state in (CaptureState.ACTIVE, CaptureState.ACCEPTED):
            locked.state = CaptureState.REVIEW
            locked.accepted_revision = None
            locked.save(update_fields=['state', 'accepted_revision', 'updated_at'])
        capture.state = locked.state
        capture.accepted_revision = locked.accepted_revision
        return revision


def accept_revision(
    *, capture: VoiceCaptureSession, revision_id, content_hash: str, accepted_by
) -> VoiceTranscriptAcceptance:
    """Bind acceptance to one exact revision, verified by hash.

    Accepting an older revision than the latest is rejected as stale; a
    hash mismatch means the client reviewed different text than stored.
    """
    with transaction.atomic():
        locked = VoiceCaptureSession.objects.select_for_update().get(pk=capture.pk)
        if locked.is_terminal:
            raise CaptureError(f'capture is {locked.state}')
        try:
            revision = locked.revisions.get(id=revision_id)
        except VoiceTranscriptRevision.DoesNotExist as exc:
            raise CaptureNotFound('no such revision') from exc
        latest = locked.revisions.order_by('-revision').first()
        if latest is None or revision.pk != latest.pk:
            raise RevisionStale('a newer revision exists; review it instead')
        if revision.content_hash != content_hash:
            raise CaptureError('reviewed text does not match stored revision')
        acceptance, created = VoiceTranscriptAcceptance.objects.get_or_create(
            revision=revision,
            defaults={
                'accepted_by': accepted_by,
                'content_hash': revision.content_hash,
            },
        )
        if not created and acceptance.content_hash != content_hash:
            raise CaptureError('revision was accepted with different content')
        locked.accepted_revision = revision
        if locked.state != CaptureState.COMMITTED:
            locked.state = CaptureState.ACCEPTED
        locked.save(update_fields=['accepted_revision', 'state', 'updated_at'])
        capture.state = locked.state
        capture.accepted_revision = revision
        return acceptance


def cancel_capture(
    *, capture: VoiceCaptureSession, reason: str = 'user_canceled'
) -> VoiceCaptureSession:
    """Cancel future processing; existing revisions remain auditable."""
    with transaction.atomic():
        locked = VoiceCaptureSession.objects.select_for_update().get(pk=capture.pk)
        if locked.state == CaptureState.COMMITTED:
            raise CaptureError('a committed capture cannot be canceled')
        if not locked.is_terminal:
            locked.state = CaptureState.CANCELED
            locked.terminal_reason = reason[:64]
            locked.save(update_fields=['state', 'terminal_reason', 'updated_at'])
        capture.state = locked.state
        capture.terminal_reason = locked.terminal_reason
        return capture


def handoff_capture(*, capture: VoiceCaptureSession):
    """Hand the exact accepted revision to its canonical destination.

    Fails closed for both purposes until their substrate is live:
    ``fault_intake`` requires the canonical Repair intake service (WS11) and
    ``closeout`` requires Feature #15's ``CloseoutCapture`` contract. Voice
    never writes packets or work orders itself (NG-07).
    """
    if capture.state != CaptureState.ACCEPTED:
        raise CaptureError('only an accepted capture may be handed off')
    if capture.purpose == CapturePurpose.CLOSEOUT:
        raise DestinationUnavailable(
            'Feature #15 CloseoutCapture substrate is not live'
        )
    raise DestinationUnavailable(
        'canonical Repair packet intake service (WS11) is not live'
    )
