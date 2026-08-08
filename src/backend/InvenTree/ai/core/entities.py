"""Server-observed entity manifest for chat answers (S28, EX-ADR-004).

Chips under an answer navigate to the records the turn was actually about —
and "about" is defined by the SERVER, never the model: the manifest is built
exclusively from server-resolved record roots and server-validated canonical
evidence. A model cannot place a chip by mentioning an id, so a chip can
never point a technician at a record the turn had no authorized relationship
with.

The manifest maps server source types onto client ``ModelType`` strings;
anything unmapped is dropped rather than guessed — an unmappable chip would
either dead-end or navigate somewhere wrong, and both are worse than no chip.
"""

from __future__ import annotations

from typing import Any

MAX_ENTITIES = 12
_MAX_LABEL_CHARS = 120

#: Server source types -> client ModelType strings. Only entries whose client
#: route semantics are verified belong here; ``work_order_closeout`` is
#: deliberately absent (its pk is not the work order's).
SERVER_MODEL_MAP = {
    "machine": "assetmachine",
    "asset_machine": "assetmachine",
    "assetmachine": "assetmachine",
    "repair_packet": "repairpacket",
    "repair_packet_redacted": "repairpacket",
    "work_order": "workorder",
    "workorder": "workorder",
}


#: Without any tool-observation signal, only this many record roots may
#: become chips: a reasoning turn's envelope carries the one or two records
#: it is about, while a text turn's root listing is the WHOLE authorized
#: fleet — and a dozen unrelated machine chips under every answer is noise
#: that teaches users to ignore the feature (found live, 2026-08-08).
_MAX_UNOBSERVED_ROOTS = 3


def build_entity_manifest(
    *, canonical: dict[str, Any], record_roots: Any = (), observed_ids: Any = None
) -> list[dict[str, Any]]:
    """Build the deduplicated, bounded manifest for one terminal turn.

    Sources, in trust order: server-resolved diagnostic record roots, then
    validated canonical evidence entries. Free text never contributes.
    ``observed_ids`` (identifier strings some tool actually returned) keeps
    root chips to records the turn actually touched; without it, roots only
    qualify when the root set itself is small.
    """
    entities: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    roots = list(record_roots or ())
    observed = {str(value) for value in (observed_ids or ())}
    if observed:
        roots = [root for root in roots if str(getattr(root, "entity_id", "")) in observed]
    elif len(roots) > _MAX_UNOBSERVED_ROOTS:
        roots = []

    def _add(source: str, model_key: Any, entity_id: Any, label: Any) -> None:
        model = SERVER_MODEL_MAP.get(str(model_key or "").strip().lower())
        if model is None or len(entities) >= MAX_ENTITIES:
            return
        try:
            pk = int(entity_id)
        except (TypeError, ValueError):
            return
        if pk <= 0 or (model, pk) in seen:
            return
        seen.add((model, pk))
        text = str(label or "").strip() or f"{model} #{pk}"
        entities.append({
            "model": model,
            "pk": pk,
            "label": text[:_MAX_LABEL_CHARS],
            "source": source,
        })

    for root in roots:
        _add(
            "record_root",
            getattr(root, "entity_type", ""),
            getattr(root, "entity_id", None),
            getattr(root, "display_name", ""),
        )

    response = canonical.get("canonical_response")
    if isinstance(response, dict):
        for item in response.get("evidence") or ():
            if isinstance(item, dict):
                _add(
                    "evidence",
                    item.get("source_type"),
                    item.get("source_id"),
                    item.get("summary"),
                )

    return entities


__all__ = ["MAX_ENTITIES", "SERVER_MODEL_MAP", "build_entity_manifest"]
