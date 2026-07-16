"""Scheduled background sweeps for the tasks app (Feature #15)."""

from InvenTree.tasks import ScheduledTask, scheduled_task


@scheduled_task(ScheduledTask.MINUTES, 15)
def sweep_closeout_effects():
    """Recover expired effect leases and execute everything due.

    The durable ``CloseoutEffect`` ledger, not the enqueue, is the source of
    truth: rows written in a completion transaction survive process death and
    are picked up here (FR-CO-011). A disabled executor flag makes this a
    no-op without losing rows.
    """
    from tasks.services.closeout_effects import sweep_closeout_effects as sweep

    return sweep()
