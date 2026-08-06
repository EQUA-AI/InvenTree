"""Per-tool severity for voice-confirmed writes (V2).

The confirmation bar must be set by the action that will actually run, not by
the words that happened to request it. In production a technician said "delete
the kanban card" and heard *"Archive kanban card with card id 127. This cannot
be undone. To confirm, say confirm delete."* -- archiving is reversible
(``restore_kanban_card`` exists), so the warning was false and the phrase named
an operation that was not going to happen. The same defect ran the other way and
mattered more: because the utterance carried no destructive verb, ``send_email``,
``cancel_purchase_order``, ``merge_stock`` and ``deactivate_part`` all landed on
the lenient bar and would have executed on a bare "yes".

So severity is looked up per tool here, and the utterance may only *raise* it.

Signed-off confirmation contract (2026-07-26):

* ``REVERSIBLE``       -- a bare "yes" confirms (the action can be undone in-app).
* ``IRREVERSIBLE``     -- requires the exact server-authored phrase.
* ``EXTERNAL_EFFECT``  -- requires the exact phrase; the effect leaves the system
  entirely (an email cannot be recalled), so it is never lenient regardless of
  how routine it looks.

Unmapped tools are treated as ``IRREVERSIBLE``: a new write tool must be
classified deliberately, and until it is, it gets the strict bar rather than the
lenient one. ``test_severity_map_covers_every_action_tool`` fails the build when
a tool is added without a decision here.
"""

from __future__ import annotations

from enum import StrEnum

from ai.core.voice.confirmation import WriteActionClass


class WriteSeverity(StrEnum):
    """How hard a confirmed voice write is to walk back."""

    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"
    EXTERNAL_EFFECT = "external_effect"


#: Reversible in-app: the record survives and the change can be undone from the
#: normal surface (restore an archived card, adjust a quantity back, edit a
#: field again). These keep the lenient bare-"yes" confirmation.
_REVERSIBLE = frozenset({
    "add_bom_item",
    "add_po_line_item",
    "add_so_line_item",
    "add_stock",
    "add_stock_test_result",
    "assign_stock",
    "change_stock_status",
    "count_stock",
    "create_company",
    "create_manufacturer_part",
    "create_part",
    "create_part_category",
    "create_purchase_order",
    "create_sales_order",
    "create_stock_location",
    "create_supplier_part",
    "install_stock",
    "set_part_parameter",
    "transfer_stock",
    "uninstall_stock",
    "update_part",
    "update_purchase_order",
    "update_stock_location",
})

#: Destroys, consumes, or transitions a record in a way the app cannot simply
#: undo -- including stock that is removed or merged away, and order lifecycle
#: transitions that re-open only through a separate governed process.
_IRREVERSIBLE = frozenset({
    "cancel_purchase_order",
    "complete_purchase_order",
    "convert_stock",
    "deactivate_part",
    "delete_po_line_item",
    "delete_purchase_order",
    "issue_purchase_order",
    "merge_stock",
    "receive_po_items",
    "remove_stock",
    "return_stock",
    "serialize_stock",
    "split_stock",
})

#: Leaves the system: a third party sees it and it cannot be recalled.
_EXTERNAL_EFFECT = frozenset({
    "generate_and_send_document",
    "mark_email_processed",
    "send_email",
})

#: Spoken confirmation phrase per severity/action family. Server-authored and
#: fixed: never derived from the transcript, and never a bare decline token
#: ("cancel" alone would collide with the decline grammar).
_CONFIRM_PHRASES: dict[str, str] = {
    "cancel_purchase_order": "confirm cancel order",
    "complete_purchase_order": "confirm complete order",
    "convert_stock": "confirm convert",
    "deactivate_part": "confirm deactivate",
    "delete_po_line_item": "confirm delete",
    "delete_purchase_order": "confirm delete",
    "issue_purchase_order": "confirm issue order",
    "merge_stock": "confirm merge",
    "receive_po_items": "confirm receive",
    "remove_stock": "confirm remove",
    "return_stock": "confirm return",
    "serialize_stock": "confirm serialize",
    "split_stock": "confirm split",
    "generate_and_send_document": "confirm send",
    "mark_email_processed": "confirm mark processed",
    "send_email": "confirm send",
}

#: Fallback when a tool is unmapped (and therefore strict by policy).
DEFAULT_CONFIRM_PHRASE = "confirm action"


def severity_for_tool_name(name: str) -> WriteSeverity:
    """Severity of one action tool. Unknown tools fail closed to strict."""
    key = (name or "").strip().lower()
    if key in _REVERSIBLE:
        return WriteSeverity.REVERSIBLE
    if key in _EXTERNAL_EFFECT:
        return WriteSeverity.EXTERNAL_EFFECT
    return WriteSeverity.IRREVERSIBLE


def confirm_phrase_for_tool_name(name: str) -> str:
    """The exact phrase a strict confirmation requires for this tool."""
    return _CONFIRM_PHRASES.get((name or "").strip().lower(), DEFAULT_CONFIRM_PHRASE)


def action_class_for_severity(severity: WriteSeverity) -> WriteActionClass:
    """Map severity onto the confirmation gate's action class."""
    if severity is WriteSeverity.REVERSIBLE:
        return WriteActionClass.CONFIRMABLE
    return WriteActionClass.IRREVERSIBLE


def classified_tool_names() -> frozenset[str]:
    """Every deliberately classified tool name (for the exhaustiveness test)."""
    return _REVERSIBLE | _IRREVERSIBLE | _EXTERNAL_EFFECT


__all__ = [
    "DEFAULT_CONFIRM_PHRASE",
    "WriteSeverity",
    "action_class_for_severity",
    "classified_tool_names",
    "confirm_phrase_for_tool_name",
    "severity_for_tool_name",
]
