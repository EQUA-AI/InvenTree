"""Per-action voice eligibility and review rules (voice-UX plan A5, doc §5.2).

One row per user-facing write action on BOTH rails:

* the tool rail (InvenTree stock / purchasing / parts / email tools, classified
  in :mod:`ai.core.voice.action_severity`), and
* the proposal rail (work-order ``ProposalAction`` values dispatched by
  ``aichat.services.proposals``).

Each row records the doc §5.2 fields the voice layer can decide from code:
severity and confirmation grammar, the spoken change label, whether a full
read-back review is required, whether the required review can be conveyed by
voice at all, and whether the action may execute by voice TODAY -- with a
short spoken reason and the plan task that lifts the restriction when not.

Unmapped actions are unavailable. Enforcement in the resolver is behind
``FEATURE_VOICE_ACTION_POLICY_ENFORCE`` (default off) so the table lands as a
tested spec first; ``docs/docs/aimms/voice-action-eligibility.md`` is
generated from it (``render_markdown``) and a test keeps the two in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai.core.voice.action_severity import (
    WriteSeverity,
    action_class_for_severity,
    change_label_for_tool_name,
    classified_tool_names,
    confirm_phrase_for_tool_name,
    severity_for_tool_name,
)
from ai.core.voice.confirmation import WriteActionClass

ACTION_POLICY_VERSION = "voice-action-policy-v1"

Rail = Literal["tool", "proposal"]
Review = Literal["brief", "full"]


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """The voice policy for one user-facing write action."""

    name: str
    rail: Rail
    severity: WriteSeverity
    action_class: WriteActionClass
    #: Exact strict phrase for irreversible/external actions; "" when lenient.
    confirm_phrase: str
    #: Spoken change label for the success sentence.
    change_label: str
    #: "full" = every required read-back section before confirmation.
    review: Review
    #: Can the required review be conveyed faithfully by voice?
    voice_review_allowed: bool
    #: May this action execute from a spoken confirmation today?
    voice_execution_allowed: bool
    #: Short spoken reason when execution or review is not allowed.
    ineligible_reason: str = ""
    #: Plan task that lifts the restriction ("B6", "E5", "F-WO-2", ...).
    follow_up: str = ""


# --------------------------------------------------------------------------- #
# Tool rail                                                                    #
# --------------------------------------------------------------------------- #
#: Spoken reasons. Short, user-facing, never transcript-derived.
REASON_NO_RECEIPT = "that action has no voice receipt yet"
REASON_STOCK_NOT_YET = "that stock action is not available by voice yet"
REASON_NOT_COMMITTED = "that change is not available by voice yet"
REASON_ATTACHMENT_SCREEN = "the document must be checked on screen before sending"
REASON_BATCH_SCREEN = "batch scheduling must be reviewed on screen"
REASON_CREATE_SCREEN = "creating work orders by voice needs a screen preview"

#: Committed families with a governed adapter arriving in Phase B/C: until the
#: adapter and the VoiceOperation receipt exist (B4/B6/C7), execution is off.
_TOOL_RECEIPT_PENDING: dict[str, str] = {
    "add_stock": "E5",
    "remove_stock": "E5",
    "transfer_stock": "E5",
    "count_stock": "E5",
    "create_purchase_order": "C7",
    "add_po_line_item": "C7",
    "issue_purchase_order": "C7",
    "receive_po_items": "C7",
    "update_purchase_order": "C7",
    "cancel_purchase_order": "C7",
    "complete_purchase_order": "C7",
    "delete_purchase_order": "C7",
    "delete_po_line_item": "C7",
    "send_email": "C7",
    "mark_email_processed": "C7",
}
#: Stock actions with no voice read-back designed yet (plan E6 / F-INV-1).
_TOOL_STOCK_NOT_YET = frozenset({
    "assign_stock",
    "change_stock_status",
    "convert_stock",
    "install_stock",
    "merge_stock",
    "return_stock",
    "serialize_stock",
    "split_stock",
    "uninstall_stock",
    "add_stock_test_result",
    "update_stock_location",
})
#: Outside the six committed workflow families (parts, companies, sales).
_TOOL_NOT_COMMITTED = frozenset({
    "add_bom_item",
    "add_so_line_item",
    "create_company",
    "create_manufacturer_part",
    "create_part",
    "create_part_category",
    "create_sales_order",
    "create_stock_location",
    "create_supplier_part",
    "deactivate_part",
    "set_part_parameter",
    "update_part",
})
#: Genuine screen requirements: the review cannot be conveyed by voice.
_TOOL_SCREEN_REQUIRED: dict[str, str] = {
    "generate_and_send_document": REASON_ATTACHMENT_SCREEN,
}


def _tool_policy(name: str) -> ActionPolicy:
    severity = severity_for_tool_name(name)
    action_class = action_class_for_severity(severity)
    strict = action_class is WriteActionClass.IRREVERSIBLE
    review: Review = "full" if severity is not WriteSeverity.REVERSIBLE else "brief"
    voice_review_allowed = name not in _TOOL_SCREEN_REQUIRED
    if name in _TOOL_SCREEN_REQUIRED:
        reason, follow_up = _TOOL_SCREEN_REQUIRED[name], "OD-15"
    elif name in _TOOL_RECEIPT_PENDING:
        reason, follow_up = REASON_NO_RECEIPT, _TOOL_RECEIPT_PENDING[name]
    elif name in _TOOL_STOCK_NOT_YET:
        reason, follow_up = REASON_STOCK_NOT_YET, "E6"
    elif name in _TOOL_NOT_COMMITTED:
        reason, follow_up = REASON_NOT_COMMITTED, "F-OTHER"
    else:  # pragma: no cover - every classified tool is listed above
        reason, follow_up = REASON_NOT_COMMITTED, "F-OTHER"
    return ActionPolicy(
        name=name,
        rail="tool",
        severity=severity,
        action_class=action_class,
        confirm_phrase=confirm_phrase_for_tool_name(name) if strict else "",
        change_label=change_label_for_tool_name(name),
        review=review,
        voice_review_allowed=voice_review_allowed,
        voice_execution_allowed=False,
        ineligible_reason=reason,
        follow_up=follow_up,
    )


# --------------------------------------------------------------------------- #
# Proposal rail (aichat.models.ProposalAction values; kept as strings so this  #
# pure module never imports Django -- the exhaustiveness test compares them)   #
# --------------------------------------------------------------------------- #
_LIFECYCLE_LABELS = {
    "work_order.hold": "is now on hold",
    "work_order.resume": "is now in progress",
    "work_order.schedule": "has been scheduled",
    "work_order.resize": "has been resized",
    "work_order.update": "plan has been updated",
    "work_order.assign": "has been assigned",
    "work_order.delete": "has been deleted",
    "work_order.cancel": "is now cancelled",
    "work_order.transition": "has moved to the requested state",
    "work_order.create": "has been created",
    "work_order.create_child": "now has the new child work order",
    "repair_work_package.create": "repair work package has been created",
    "work_order.generate_procurement": "now has the procurement child",
    "dependency.create": "now has the new dependency",
    "dependency.delete": "no longer has that dependency",
    "schedule.optimize": "schedule has been optimized",
}


def _proposal(
    name: str,
    *,
    severity: WriteSeverity = WriteSeverity.REVERSIBLE,
    confirm_phrase: str = "",
    review: Review = "brief",
    allowed: bool = True,
    reason: str = "",
    follow_up: str = "",
) -> ActionPolicy:
    return ActionPolicy(
        name=name,
        rail="proposal",
        severity=severity,
        action_class=action_class_for_severity(severity),
        confirm_phrase=confirm_phrase,
        change_label=_LIFECYCLE_LABELS[name],
        review=review,
        voice_review_allowed=True,
        voice_execution_allowed=allowed,
        ineligible_reason=reason,
        follow_up=follow_up,
    )


PROPOSAL_POLICIES: dict[str, ActionPolicy] = {
    policy.name: policy
    for policy in (
        _proposal("work_order.hold"),
        _proposal("work_order.resume"),
        _proposal("work_order.schedule"),
        _proposal("work_order.resize"),
        _proposal("work_order.update"),
        _proposal("work_order.assign"),
        _proposal("work_order.transition"),
        _proposal("work_order.create_child"),
        _proposal("work_order.generate_procurement"),
        _proposal("dependency.create"),
        _proposal("dependency.delete"),
        _proposal("repair_work_package.create", review="full"),
        _proposal(
            "work_order.delete",
            severity=WriteSeverity.IRREVERSIBLE,
            confirm_phrase="confirm delete",
            review="full",
        ),
        # OD-3: no strict phrase exists on the proposal rail today; fail closed
        # to a strict phrase until the owner decides otherwise.
        _proposal(
            "work_order.cancel",
            severity=WriteSeverity.IRREVERSIBLE,
            confirm_phrase="confirm cancel",
            review="full",
        ),
        _proposal(
            "work_order.create",
            allowed=False,
            reason=REASON_CREATE_SCREEN,
            follow_up="F-WO-2",
        ),
        _proposal(
            "schedule.optimize",
            severity=WriteSeverity.IRREVERSIBLE,
            confirm_phrase="confirm optimize schedule",
            review="full",
            allowed=False,
            reason=REASON_BATCH_SCREEN,
            follow_up="F-WO-2",
        ),
    )
}


def tool_policies() -> dict[str, ActionPolicy]:
    """Every classified tool's policy, keyed by tool name."""
    return {name: _tool_policy(name) for name in sorted(classified_tool_names())}


def action_policy(name: str) -> ActionPolicy | None:
    """The policy for one action on either rail, or ``None`` when unmapped."""
    key = (name or "").strip().lower()
    if key in PROPOSAL_POLICIES:
        return PROPOSAL_POLICIES[key]
    if key in classified_tool_names():
        return _tool_policy(key)
    return None


def all_policies() -> tuple[ActionPolicy, ...]:
    """Every policy, proposal rail first, then tools alphabetically."""
    return (*PROPOSAL_POLICIES.values(), *tool_policies().values())


def render_markdown() -> str:
    """The tracked eligibility table (docs/docs/aimms/voice-action-eligibility.md)."""
    lines = [
        "# Voice action eligibility",
        "",
        f"Generated from `ai/core/voice/action_policy.py` ({ACTION_POLICY_VERSION}). "
        "Do not edit by hand: regenerate with",
        '`python manage.py shell -c "from ai.core.voice.action_policy import render_markdown; '
        "print(render_markdown(), end='')\" > docs/docs/aimms/voice-action-eligibility.md`. "
        "A test fails when this file drifts.",
        "",
        "Every business write requires a read-back and an explicit spoken confirmation "
        "(owner decision, 2026-09-09). *Confirmation* is the exact strict phrase for "
        "irreversible/external actions or a short assent for reversible ones. "
        "*Voice execution* is enforced by `FEATURE_VOICE_ACTION_POLICY_ENFORCE`; "
        "an unlisted action is always unavailable.",
        "",
        "| Rail | Action | Severity | Confirmation | Change label | Review | Voice review | Voice execution | Reason / follow-up |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for policy in all_policies():
        confirmation = f"`{policy.confirm_phrase}`" if policy.confirm_phrase else "short assent"
        execution = "yes" if policy.voice_execution_allowed else "no"
        reason = policy.ineligible_reason or "—"
        if policy.follow_up:
            reason = f"{reason} ({policy.follow_up})"
        lines.append(
            f"| {policy.rail} | `{policy.name}` | {policy.severity.value} | {confirmation} | "
            f"{policy.change_label} | {policy.review} | "
            f"{'yes' if policy.voice_review_allowed else 'no'} | {execution} | {reason} |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ACTION_POLICY_VERSION",
    "PROPOSAL_POLICIES",
    "ActionPolicy",
    "action_policy",
    "all_policies",
    "render_markdown",
    "tool_policies",
]
