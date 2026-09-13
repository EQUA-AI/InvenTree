"""Deterministic, complete read-back fields for each governed work-order action."""

FIELDS = {
    "closeout.consent": ("disclosure",),
    "closeout.accept": ("revision", "full_text", "content_hash"),
    "closeout.handoff": ("revision", "full_text", "content_hash"),
    "procedure.complete": (
        "application_id",
        "step_key",
        "step_snapshot",
        "value",
        "passed",
        "resulting_status",
    ),
    "work_order.resume": ("current_status", "resulting_status"),
    "work_order.assign": ("current_assigned_to_name", "proposed_assigned_to_name"),
    "work_order.schedule": ("current_start", "current_end", "proposed_start", "proposed_end"),
    "work_order.resize": (
        "current_estimated_minutes",
        "proposed_estimated_minutes",
        "current_end",
        "proposed_end",
    ),
    "work_order.update": ("changes",),
    "work_order.transition": ("current_status", "resulting_status"),
    "work_order.delete": ("current_status", "resulting_status", "reason", "irreversible"),
    "work_order.create_child": ("proposed_title", "card_kind"),
    "work_order.generate_procurement": ("parts_snapshot", "note"),
    "dependency.create": (
        "predecessor_reference",
        "successor_reference",
        "dependency_type",
        "lag_minutes",
    ),
    "dependency.delete": (
        "dependency_id",
        "predecessor_reference",
        "successor_reference",
        "dependency_type",
        "lag_minutes",
    ),
}

VERBS = {
    "closeout.consent": "Consent to dictating a closeout note for",
    "closeout.accept": "Accept the exact closeout note for",
    "closeout.handoff": "Hand off the accepted closeout note for",
    "procedure.complete": "Complete a procedure step for",
    "work_order.resume": "Resume",
    "work_order.assign": "Reassign",
    "work_order.schedule": "Schedule",
    "work_order.resize": "Resize",
    "work_order.update": "Update",
    "work_order.transition": "Change the state of",
    "work_order.delete": "Permanently delete",
    "work_order.create_child": "Create a child for",
    "work_order.generate_procurement": "Generate procurement for",
    "dependency.create": "Add a dependency to",
    "dependency.delete": "Remove a dependency from",
}


def readable(value):
    """Keep all structured fields and literal units; never truncate a review."""
    if value is None:
        return "not set"
    if isinstance(value, dict):
        return "; ".join(
            f"{key.replace('_', ' ')}: {readable(item)}" for key, item in sorted(value.items())
        )
    if isinstance(value, (list, tuple)):
        return "; ".join(readable(item) for item in value) or "none"
    return str(value)


def review(action, preview, spoken_label):
    """Return speech, displayed sections and the actual strict phrase if required."""
    fields = (*FIELDS[action], "warning")
    sections = tuple(
        {"id": key, "label": key.replace("_", " ").title(), "text": readable(preview.get(key))}
        for key in fields
    )
    phrase = preview.get("confirm_phrase") or None
    instruction = f"Say {phrase} to proceed" if phrase else "Say yes to proceed"
    spoken = (
        f"{VERBS[action]} {spoken_label}. "
        + " ".join(f"{section['label']}: {section['text']}." for section in sections)
        + f" {instruction}, change that to correct it, or no to set it aside."
    )
    return spoken, sections, phrase
