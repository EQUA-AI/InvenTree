"""Scheduled maintenance for the governed proposal rail (WS7-T9)."""

from InvenTree.tasks import ScheduledTask, scheduled_task


@scheduled_task(ScheduledTask.MINUTES, 15)
def expire_stale_chat_action_proposals():
    """Expire pending proposals past their confirmation window.

    Expiry never deletes anything: rows move to the terminal ``expired``
    state and remain auditable. Adopted default cadence is 15 minutes.
    """
    from aichat.services.proposals import (
        expire_stale_proposals,
        sweep_proposal_notifications,
    )

    # Warn before expiring so a proposal in its final window still gets its
    # reminder on the sweep that would otherwise be its last.
    counts = sweep_proposal_notifications()
    expired = expire_stale_proposals()
    if expired or counts['warned'] or counts['outcomes']:
        import logging

        logging.getLogger('inventree').info(
            'Proposal sweep: expired=%d warned=%d outcomes=%d',
            expired,
            counts['warned'],
            counts['outcomes'],
        )
