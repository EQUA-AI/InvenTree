"""Shared, fail-closed consent and client resolution for durable memory."""

import re
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tasks.scope import ScopeError, client_codes_for_actor, machine_scope_filter

from ai.core.analysis.scope import MODE_ALL_AUTHORIZED, MODE_EXPLICIT, scope_from_stored
from ai.core.config import get_settings
from aichat.models import (
    ChatThreadGrant,
    ClientAISettings,
    MemoryNoticeAcknowledgement,
    UserMemorySettings,
)
from assets.models import AssetMachine
from InvenTree.restore_hold import restore_hold_enabled


@dataclass(frozen=True)
class MemoryEligibility:
    """No source text or provider error information crosses this decision seam."""

    reason: str
    clients: frozenset[str] = frozenset()
    notice_version: str = ''

    @property
    def allowed(self):
        """Admission requires at least one enrolled, acknowledged client."""
        return self.reason == 'eligible' and bool(self.clients)


def notice_number(version):
    """Compare notice versions numerically, never lexicographically."""
    match = re.fullmatch(r'memory-v([1-9][0-9]*)', str(version))
    if not match:
        raise ValueError('Invalid memory notice version')
    return int(match.group(1))


def resolve_thread_client_context(actor, thread):
    """Intersect current authorized clients with current machine relationships."""
    if not actor or not actor.is_active or actor.pk != thread.owner_id:
        return frozenset()
    scope = scope_from_stored(thread.analysis_scope)
    try:
        authorized = client_codes_for_actor(actor)
        if scope.mode == MODE_ALL_AUTHORIZED:
            return authorized
        if scope.mode != MODE_EXPLICIT:
            return frozenset()
        codes = AssetMachine.objects.filter(
            machine_scope_filter(actor), pk__in=scope.machine_ids, client__active=True
        ).values_list('client__code', flat=True)
        return authorized.intersection(codes)
    except ScopeError:
        return frozenset()


def active_grants(thread, *, at=None):
    """Evaluate both current and historical sharing windows from durable grants."""
    at = at or timezone.now()
    return (
        ChatThreadGrant.objects
        .filter(thread=thread, created_at__lte=at)
        .filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=at))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=at))
    )


def evaluate_memory_eligibility(actor, thread, *, purpose='extract', source_time=None):
    """Re-evaluate at enqueue, execution and recall; flags never bypass consent."""
    if purpose not in {'extract', 'recall'}:
        raise ValueError('Invalid memory purpose')
    config = get_settings()
    enabled = (
        config.feature_semantic_memory_extract_shadow
        if purpose == 'extract'
        else config.feature_semantic_memory_recall
    )
    if not enabled:
        return MemoryEligibility('feature_disabled')
    if restore_hold_enabled():
        return MemoryEligibility('restore_hold')
    if not actor or actor.pk != thread.owner_id:
        return MemoryEligibility('not_owner')
    # Resolve fresh actor state: long-lived queued tasks must not use stale flags.
    actor = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    if actor is None:
        return MemoryEligibility('inactive_owner')
    if UserMemorySettings.objects.filter(user=actor, opted_out=True).exists():
        return MemoryEligibility('opted_out')
    if active_grants(thread).exists():
        return MemoryEligibility('shared_thread')
    if purpose == 'extract':
        mode = (
            config.aimms_memory_default_mode
            if thread.memory_mode == 'inherit'
            else thread.memory_mode
        )
        if mode != 'extract':
            return MemoryEligibility('memory_off')
        if source_time is not None and active_grants(thread, at=source_time).exists():
            return MemoryEligibility('shared_source_window')
    clients = resolve_thread_client_context(actor, thread)
    if not clients:
        return MemoryEligibility('no_authorized_clients')
    return _enrolled_clients(
        actor, clients, source_time=source_time if purpose == 'extract' else None
    )


def _enrolled_clients(actor, clients, *, source_time=None):
    """One notice/enrollment rule shared by thread and owner-level providers."""
    config = get_settings()
    current = notice_number(config.aimms_memory_notice_version)
    acknowledged = []
    notices = MemoryNoticeAcknowledgement.objects.filter(user=actor)
    if source_time is not None:
        notices = notices.filter(acknowledged_at__lte=source_time)
    for version in notices.values_list('notice_version', flat=True):
        try:
            number = notice_number(version)
        except ValueError:
            continue
        if number <= current:
            acknowledged.append((number, version))
    if not acknowledged:
        return MemoryEligibility('notice_required')
    number, version = max(acknowledged)
    eligible = set()
    enrollments = ClientAISettings.objects.filter(
        client__code__in=clients, client__active=True, memory_enabled=True
    )
    if source_time is not None:
        enrollments = enrollments.filter(enabled_at__lte=source_time)
    for code, required in enrollments.values_list(
        'client__code', 'required_notice_version'
    ):
        try:
            minimum = notice_number(required)
        except ValueError:
            continue
        if minimum <= number and minimum <= current:
            eligible.add(code)
    if not eligible:
        return MemoryEligibility('no_enrolled_clients')
    return MemoryEligibility('eligible', frozenset(eligible), version)


@transaction.atomic
def acknowledge_notice(actor, version):
    """Explicit current-version acknowledgement, never inferred or precreated."""
    if restore_hold_enabled() or version != get_settings().aimms_memory_notice_version:
        raise ValueError('Memory notice acknowledgement unavailable')
    notice_number(version)
    user = (
        get_user_model()
        .objects.select_for_update()
        .filter(pk=actor.pk, is_active=True)
        .first()
    )
    if user is None:
        raise ValueError('Memory notice acknowledgement unavailable')
    row, _created = MemoryNoticeAcknowledgement.objects.get_or_create(
        user=user, notice_version=version
    )
    return row


def voice_source_has_memory_consent(source):
    """Old voice consent did not disclose learned memory; never infer an upgrade."""
    if source.modality != 'voice':
        return True
    import uuid

    from voice.models import VoiceSession

    metadata = source.metadata if isinstance(source.metadata, dict) else {}
    try:
        identity = uuid.UUID(str(metadata.get('voice_session_id', '')))
    except (ValueError, TypeError):
        return False
    return VoiceSession.objects.filter(
        pk=identity,
        owner_id=source.thread.owner_id,
        thread_id=source.thread_id,
        consent_version='consent-v3-memory',
        created_at__lte=source.created_at,
    ).exists()


def evaluate_owner_memory(actor):
    """Current owner/client consent for already-confirmed fact preparation."""
    config = get_settings()
    if not (
        config.feature_semantic_memory_extract_shadow
        or config.feature_semantic_memory_recall
    ):
        return MemoryEligibility('feature_disabled')
    if restore_hold_enabled():
        return MemoryEligibility('restore_hold')
    actor = (
        get_user_model()
        .objects.filter(pk=getattr(actor, 'pk', None), is_active=True)
        .first()
    )
    if actor is None:
        return MemoryEligibility('inactive_owner')
    if UserMemorySettings.objects.filter(user=actor, opted_out=True).exists():
        return MemoryEligibility('opted_out')
    try:
        clients = client_codes_for_actor(actor)
    except ScopeError:
        return MemoryEligibility('no_authorized_clients')
    if not clients:
        return MemoryEligibility('no_authorized_clients')
    return _enrolled_clients(actor, clients)
