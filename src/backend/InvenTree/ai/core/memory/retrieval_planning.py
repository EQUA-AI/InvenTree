"""Pure, bounded retrieval decisions owned by the context builder.

Plans choose source order, never grant access. Executors resolve current native
records and reauthorize every emitted result. An absent serial is not permission
to broaden an explicitly selected machine set.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ManualRetrievalPlan:
    """Immutable asset coordinates and ordered conditional source steps."""

    machine_ids: tuple[int, ...]
    serials: tuple[str, ...]
    models: tuple[str, ...]
    steps: tuple[str, ...]
    serial_unresolved: bool


def manual_retrieval_plan(*, asset_set, document_selected=False, attachments_available=False):
    """Freeze the existing controlled-source fallback policy without I/O."""
    machine_ids = tuple(sorted(asset_set.machine_pks))
    serials = tuple(sorted(asset_set.serials))
    models = tuple(sorted(asset_set.models))
    if len(machine_ids) > 100 or len(serials) > 100 or len(models) > 100:
        raise ValueError("Retrieval scope exceeds bounds")
    if any(type(pk) is not int or pk <= 0 for pk in machine_ids):
        raise ValueError("Invalid native machine identity")
    if any(
        not isinstance(value, str) or not value or len(value) > 255 for value in (*serials, *models)
    ):
        raise ValueError("Invalid native asset coordinate")
    unresolved = bool(machine_ids and not serials)
    if unresolved:
        steps = ("verified_exact_controlled",)
    else:
        planned = ["pinned_revision"] if document_selected else []
        planned.append("exact_asset_controlled")
        if models:
            planned.append("verified_model_config")
        if serials:
            planned.append("fleet_wide_controlled")
        if attachments_available:
            planned.append("asset_attachments")
        steps = tuple(planned)
    return ManualRetrievalPlan(machine_ids, serials, models, steps, unresolved)
