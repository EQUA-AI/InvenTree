"""Scheduled Risk Radar scan dispatchers (Features #4 / #16).

One dispatcher task per cadence class fans out leased per-rule/scope scans
through the standard django-q2 registry, so operations see them exactly
like every other InvenTree scheduled task. Every dispatcher fails closed
while ``AIMMS_RISK_RADAR_ENABLED`` is off or the scanner principal is
unconfigured.
"""

from InvenTree.tasks import ScheduledTask, scheduled_task


@scheduled_task(ScheduledTask.MINUTES, 15)
def dispatch_risk_scans_minutes_15():
    """Dispatch the 15-minute cadence class (safety/blocker rules)."""
    from repair.risk_services import dispatch_scans

    return dispatch_scans('minutes_15')


@scheduled_task(ScheduledTask.HOURLY)
def dispatch_risk_scans_hourly():
    """Dispatch the hourly cadence class (SLA/aging/lateness rules)."""
    from repair.risk_services import dispatch_scans

    return dispatch_scans('hourly')


@scheduled_task(ScheduledTask.DAILY)
def dispatch_risk_scans_daily():
    """Dispatch the daily cadence class (stock/closeout/asset rules)."""
    from repair.risk_services import dispatch_scans

    return dispatch_scans('daily')


@scheduled_task(ScheduledTask.MINUTES, 5)
def sweep_risk_notifications():
    """Deliver due pending risk notification intents.

    The durable pending rows are the source of truth; this sweeper
    recovers any occurrence whose post-commit queue hint was lost.
    """
    from repair.risk_services import deliver_pending_notifications

    return deliver_pending_notifications()
