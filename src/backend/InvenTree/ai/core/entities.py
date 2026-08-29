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


def build_analysis_entity_manifest(
    claims: Any, store: Any, scope: Any = None
) -> list[dict[str, Any]]:
    """Chips for the analysis rail (S10): claim entity refs ONLY.

    No fleet-root fallback exists here by design (§8.6): inputs are the
    validated claims' ``entity_refs`` (``"machine:12"`` — server-minted by
    the evidence adapters) plus the explicit-scope focus machines. Labels
    resolve from the store's fact values; an unmapped ref is dropped and the
    validator's C10 check flags it. Each chip carries ``ref`` so C10 can
    join chips back to claims.
    """
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()

    labels: dict[str, str] = {}
    for fact in getattr(store, "facts", {}).values():
        rendered = fact.rendered_values()
        for ref in fact.entity_refs:
            if ref.startswith("machine:") and rendered.get("machine"):
                labels.setdefault(ref, rendered["machine"])
            elif ref.startswith("machine:") and rendered.get("label"):
                # S7 group rows carry the machine name as the group label.
                labels.setdefault(ref, rendered["label"])
            elif ref.startswith("workorder:") and rendered.get("reference"):
                labels.setdefault(ref, rendered["reference"])

    def _add(ref: str) -> None:
        if ref in seen or len(entities) >= MAX_ENTITIES:
            return
        kind, _, raw_pk = str(ref).partition(":")
        model = SERVER_MODEL_MAP.get(kind.strip().lower())
        if model is None or not raw_pk.isdigit():
            return
        seen.add(ref)
        label = labels.get(ref) or f"{model} #{raw_pk}"
        entities.append({
            "model": model,
            "pk": int(raw_pk),
            "label": str(label)[:_MAX_LABEL_CHARS],
            "source": "claim_evidence",
            "ref": ref,
        })

    for claim in claims:
        for ref in getattr(claim, "entity_refs", ()):
            _add(ref)
    if scope is not None and getattr(scope, "explicit", False):
        for machine_id in getattr(scope, "machine_ids", ()) or ():
            _add(f"machine:{machine_id}")

    return entities


__all__ = [
    "MAX_ENTITIES",
    "SERVER_MODEL_MAP",
    "build_analysis_entity_manifest",
    "build_entity_manifest",
]
