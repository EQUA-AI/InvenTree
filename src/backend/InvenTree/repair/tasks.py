"""Background task discovery shim for the repair app.

``InvenTree.apps.InvenTreeConfig.collect_tasks()`` imports each app-level
``tasks.py`` module; importing the risk task module here registers the
Risk Radar scan dispatchers with the scheduled-task registry.
"""

from repair.risk_tasks import (  # noqa: F401
    dispatch_risk_scans_daily,
    dispatch_risk_scans_hourly,
    dispatch_risk_scans_minutes_15,
    sweep_risk_notifications,
)
