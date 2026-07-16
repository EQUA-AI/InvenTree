"""Scheduled maintenance for the governed proposal rail (WS7-T9)."""

from InvenTree.tasks import ScheduledTask, scheduled_task


@scheduled_task(ScheduledTask.MINUTES, 15)
def expire_stale_chat_action_proposals():
    """Expire pending proposals past their confirmation window.

    Expiry never deletes anything: rows move to the terminal ``expired``
    state and remain auditable. Adopted default cadence is 15 minutes.
    """
    from aichat.services.proposals import expire_stale_proposals

    expired = expire_stale_proposals()
    if expired:
        import logging

        logging.getLogger('inventree').info(
            'Expired %d stale chat action proposals', expired
        )
