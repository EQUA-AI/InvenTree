"""Best-effort touch invalidation backed by mandatory domain revalidation."""

import logging


def invalidate_for_source(source_id, thread_id):
    """Touch mutations invalidate the overlay; domain revalidation is the backstop."""
    from ai.core.decisions.coordinator import get_coordinator
    from ai.core.decisions.pipeline import enabled

    if not enabled() or not thread_id:
        return
    try:
        coordinator = get_coordinator()
        decision = coordinator.store.read(thread_id)
        if decision and decision.source_id == str(source_id):
            coordinator.disarm(thread_id, "source_invalidated")
    except Exception:
        logging.getLogger(__name__).warning(
            "Decision invalidation deferred to authoritative revalidation"
        )
