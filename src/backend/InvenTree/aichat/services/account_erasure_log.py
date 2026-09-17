"""Persist local account-erasure intent without retaining identity text."""

from django.db import transaction

from aichat.models import AccountErasureTombstone


class ErasureIdentityConflictError(ValueError):
    """An erasure identity no longer matches; never include account details."""


@transaction.atomic
def record_erasure(*, user_id, user_joined_at, requested_at):
    """Keep the first intent clock and refuse primary-key reuse.

    Normal callers hold the user row lock and commit this with deactivation.
    Restore callers may retain an intent even if its user did not yet exist at
    the restored point; the deployment must remain isolated from all writers.
    """
    stone, _ = AccountErasureTombstone.objects.select_for_update().get_or_create(
        user_id=user_id,
        defaults={'user_joined_at': user_joined_at, 'requested_at': requested_at},
    )
    if stone.user_joined_at != user_joined_at:
        raise ErasureIdentityConflictError('Account erasure identity mismatch')
    if stone.requested_at > requested_at:
        stone.requested_at = requested_at
        stone.save(update_fields=['requested_at'])
    return stone
