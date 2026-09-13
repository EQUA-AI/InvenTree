"""AIMMS vocabulary v1: live, actor-scoped names and locations, never aliases."""

import json
from pathlib import Path

from ai.core.voice.experience import VOCABULARY_OWNER, VOCABULARY_VERSION


def hints_for_scoped_rows(rows) -> list[str]:
    """Intersect the approved version with freshly authorized, matching rows.

    IDs alone are insufficient (fixtures can differ between deployments). Never
    emit an old location/name after a record changes, or an unreviewed alias.
    """
    source = json.loads(Path(__file__).with_name("site_vocabulary_v1.json").read_text())
    if source["version"] != VOCABULARY_VERSION or source["owner"] != VOCABULARY_OWNER:
        return []
    approved = {row["id"]: row for row in source["equipment"]}
    terms = set()
    for row in rows:
        entry = approved.get(row.pk)
        if entry is None or row.name != entry["name"]:
            continue
        terms.add(entry["name"])
        if str(getattr(row, "location", "") or "").strip() == entry["location"]:
            terms.add(entry["location"])
    return sorted(terms)[:64]


def actor_phrase_hints(user_id) -> list[str]:
    """Refresh scope for each session; never publish the site-wide snapshot."""
    from assets import ai_read
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return []
    terms = set(hints_for_scoped_rows(ai_read.machines_in_scope(user, limit=64)))
    # Preserve the existing open-job hints, but refresh authorization instead
    # of reusing the old per-actor cache after a permission change.
    try:
        from tasks.models import WorkOrder
        from tasks.scope import work_order_scope_filter

        references = (
            WorkOrder.objects
            .filter(work_order_scope_filter(user))
            .exclude(status=WorkOrder.STATUS_DONE)
            .exclude(reference="")
            .order_by("-updated_at")
            .values_list("reference", flat=True)[:16]
        )
        terms.update(str(reference) for reference in references)
    except Exception:  # Optional hints fail closed on unavailable scope/data.
        pass
    return sorted(terms)[:64]


__all__ = ["VOCABULARY_OWNER", "VOCABULARY_VERSION", "actor_phrase_hints"]
