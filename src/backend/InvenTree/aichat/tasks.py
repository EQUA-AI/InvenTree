"""Scheduled maintenance for the governed proposal rail (WS7-T9)."""

import logging

from InvenTree.tasks import ScheduledTask, scheduled_task

logger = logging.getLogger('inventree')

PROPOSAL_SWEEP_INTERVAL_MINUTES = 1


@scheduled_task(ScheduledTask.MINUTES, PROPOSAL_SWEEP_INTERVAL_MINUTES)
def expire_stale_chat_action_proposals():
    """Expire pending proposals past their confirmation window.

    Expiry never deletes anything: rows move to the terminal ``expired``
    state and remain auditable. A one-minute cadence covers the shortest
    (three-minute voice) proposal TTL despite scheduler alignment.
    """
    from aichat.services.proposals import (
        expire_stale_proposals,
        sweep_proposal_notifications,
    )

    counts = {'warned': 0, 'outcomes': 0}
    try:
        # Notification delivery is helpful but subordinate to mandatory expiry.
        counts = sweep_proposal_notifications()
    except Exception:
        logger.exception('Proposal notification sweep failed; continuing expiry')
    expired = expire_stale_proposals()
    if expired or counts['warned'] or counts['outcomes']:
        logger.info(
            'Proposal sweep: expired=%d warned=%d outcomes=%d',
            expired,
            counts['warned'],
            counts['outcomes'],
        )
