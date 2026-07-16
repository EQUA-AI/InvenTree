"""Scheduled reconciliation for realtime voice sessions (WS4-T9)."""

import os

from InvenTree.tasks import ScheduledTask, scheduled_task


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@scheduled_task(ScheduledTask.MINUTES, 15)
def expire_stale_voice_sessions():
    """Expire orphaned realtime sessions past their idle/age bounds.

    Browser crashes and backend restarts can strand sessions in a
    non-terminal state; this sweep gives them an honest terminal state and
    cancels queued playback. It never deletes ledger rows.
    """
    from voice.services.realtime import SessionLimits, expire_stale_sessions

    limits = SessionLimits(
        max_active_per_user=_int_env('VOICE_LIVE_MAX_ACTIVE_SESSIONS_PER_USER', 1),
        idle_timeout_s=_int_env('VOICE_LIVE_IDLE_TIMEOUT_S', 300),
        max_age_s=_int_env('VOICE_LIVE_MAX_SESSION_AGE_S', 3600),
        max_turns=_int_env('VOICE_LIVE_MAX_TURNS_PER_SESSION', 100),
    )
    expired = expire_stale_sessions(limits=limits)
    if expired:
        import logging

        logging.getLogger('inventree').info(
            'Expired %d orphaned voice sessions', expired
        )
