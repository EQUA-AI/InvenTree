"""Owner consent controls; no provider calls or implicit enrollment."""

from django.contrib.auth import get_user_model
from django.db import transaction

from ai.core.config import get_settings
from aichat.models import (
    AIRetentionOutbox,
    MemoryExtractionClaim,
    MemoryFactEvent,
    MemoryMode,
    MemoryNoticeAcknowledgement,
    UserMemorySettings,
)
from aichat.services.memory_eligibility import (
    active_grants,
    evaluate_memory_eligibility,
)
from aichat.services.memory_lifecycle import withdraw_proposals_for_thread
from aichat.services.threads import InvalidBoundary
from InvenTree.restore_hold import restore_hold_enabled

# A version identifies immutable copy. Adding another version requires adding
# its reviewed text here; changing an environment value alone cannot invent it.
NOTICE_COPY = {
    'memory-v1': (
        'When memory is enabled for your client and you choose to use it, AIMMS '
        'can derive memories from eligible chat and voice conversations for use '
        'in later conversations. You can review, correct and forget memories '
        'on What AIMMS remembers. Learning is excluded from shared conversations. '
        'Turning learning off for a conversation preserves confirmed memories; '
        'opting out stops learning and removes your learned memories. '
        'Deleting a conversation may retain confirmed memories with their source '
        'link removed. Forgetting a memory also clears your conversation summaries; '
        'it does not delete original messages. Microsoft processes memory inputs '
        'and may retain them for abuse monitoring and human review. '
        'Do not provide secrets or sensitive personal information for memory.'
    )
}


def memory_status(owner):
    """Expose exact available notice copy and owner settings without client IDs."""
    owner = get_user_model().objects.filter(pk=owner.pk, is_active=True).first()
    if owner is None:
        raise ValueError('Memory controls unavailable')
    config = get_settings()
    version = config.aimms_memory_notice_version
    copy = NOTICE_COPY.get(version)
    return {
        'can_write': owner.has_perm('aichat.write_memory'),
        'cleanup_pending': AIRetentionOutbox.objects
        .filter(kind='memory_owner_purge', reference__startswith=f'{owner.pk}:')
        .exclude(state='done')
        .exists(),
        'notice_version': version,
        'notice_text': copy,
        'notice_available': copy is not None,
        'acknowledged': MemoryNoticeAcknowledgement.objects.filter(
            user=owner, notice_version=version
        ).exists(),
        'opted_out': UserMemorySettings.objects.filter(
            user=owner, opted_out=True
        ).exists(),
        'default_mode': config.aimms_memory_default_mode,
        'extraction_enabled': config.feature_semantic_memory_extract_shadow,
        'recall_enabled': config.feature_semantic_memory_recall,
        'restore_hold': restore_hold_enabled(),
    }


def thread_memory_status(owner, thread):
    """Explain extraction and recall independently for the owner's conversation."""
    config = get_settings()
    return {
        'thread_id': thread.pk,
        'memory_mode': thread.memory_mode,
        'effective_mode': (
            config.aimms_memory_default_mode
            if thread.memory_mode == MemoryMode.INHERIT
            else thread.memory_mode
        ),
        'extraction_status': evaluate_memory_eligibility(owner, thread).reason,
        'recall_status': evaluate_memory_eligibility(
            owner, thread, purpose='recall'
        ).reason,
    }


@transaction.atomic
def set_thread_mode(repository, thread_id, mode):
    """Serialize mode changes with writes and exclude old input on every change."""
    if mode not in MemoryMode.values or restore_hold_enabled():
        raise InvalidBoundary('Memory mode unavailable')
    thread = repository._lock_thread(thread_id)
    effective = get_settings().aimms_memory_default_mode if mode == 'inherit' else mode
    if effective == 'extract' and active_grants(thread).exists():
        raise InvalidBoundary('Shared conversations cannot learn memories')
    if thread.memory_mode == mode:
        return thread
    thread.memory_mode = mode
    thread.memory_through_sequence = max(
        thread.memory_through_sequence, thread.next_sequence - 1
    )
    thread.save(update_fields=['memory_mode', 'memory_through_sequence', 'updated_at'])
    MemoryExtractionClaim.objects.filter(
        thread=thread, state__in=['pending', 'claimed', 'deferred', 'failed']
    ).update(state='skipped', lease_token=None, claimed_at=None)
    if effective == 'off':
        withdraw_proposals_for_thread(thread)
    MemoryFactEvent.objects.create(
        owner_id=repository.actor_id, actor_id=repository.actor_id, action='mode_change'
    )
    return thread
