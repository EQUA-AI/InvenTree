"""The model-exposed source-inventory tool (S8a, WP-B2).

One `@ai_function`, `list_document_sources`, delegating to the registry
gateway (`ai.core.analysis.source_gateway`). The gateway module itself
stays MAF-free; this wrapper owns the async boundary and the acting-user
resolution, exactly like the corpus tool modules.
"""

from __future__ import annotations

from typing import Any

from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async


def _current_user():
    """Resolve the acting Django user from the authenticated ASGI boundary."""
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return None
    return get_user_model().objects.filter(pk=principal.user_pk).first()


@ai_function
async def list_document_sources(
    machine: str | None = None,
    source_class: str | None = None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """
    List what document sources exist and their status -- the registry, not
    the content.

    Use this when the user asks WHICH manuals, documents, attachments or
    media exist, are available, indexed, current, or failed ("what manuals
    do you have for the HX-200?", "which revision is current?", "did the
    datasheet finish indexing?"). It reports titles, revisions, dates,
    control class, current/superseded status, indexing failures and
    associated assets straight from the registry -- it does NOT read
    document contents. To answer what a document SAYS, use search_manuals
    or search_attachment_docs instead.

    Associations shown here come from ingest metadata; document
    applicability is not yet verified, so describe them as "associated by
    ingest metadata", never as "applies to".

    Args:
      machine: Optional machine name to narrow the listing to that asset's
               sources. Ambiguous names return candidates to pick from.
      source_class: Optional filter: controlled_document, asset_attachment
                    or evidence_media. Anything else is refused.
      include_superseded: Include superseded revision details per document
                          (default False; the current revision always shows).

    Returns:
      Dictionary with per-source-class 'sections' (each carrying rows,
      honest population counts and a retrieval envelope), 'asset_scope',
      and 'warnings' (always including applicability_unresolved).
    """

    @sync_to_async
    def _run():
        from ai.core.analysis.source_gateway import SOURCE_CLASSES, inventory

        user = _current_user()
        if user is None:
            return {
                "success": False,
                "error": "No authenticated user is available for this listing.",
            }
        machine_ids = None
        if machine:
            # A model-supplied NAME is a lookup key into the actor's own
            # authorized machines -- narrowing only, never scope.
            from assets.ai_read import machines_in_scope

            rows = machines_in_scope(user, query=machine) or []
            exact = [row for row in rows if str(row.get("name", "")).lower() == machine.lower()]
            candidates = exact or rows
            if not candidates:
                return {
                    "machine_filter": "not_resolved",
                    "message": (
                        "No authorized machine matched that name; the listing "
                        "was not run. Ask the user to confirm the machine."
                    ),
                }
            if len(candidates) > 1:
                return {
                    "machine_filter": "ambiguous",
                    "machine_candidates": [str(row.get("name", "")) for row in candidates[:5]],
                }
            machine_ids = [int(candidates[0]["machine_id"])]
        classes = None
        if source_class:
            if source_class not in SOURCE_CLASSES:
                return {
                    "success": False,
                    "error": "Unknown source_class.",
                    "allowed": list(SOURCE_CLASSES),
                }
            classes = [source_class]
        return inventory(
            user,
            machine_ids=machine_ids,
            source_classes=classes,
            include_superseded=bool(include_superseded),
        )

    return await _run()


SOURCE_INVENTORY_TOOLS = [list_document_sources]

__all__ = ["SOURCE_INVENTORY_TOOLS", "list_document_sources"]
