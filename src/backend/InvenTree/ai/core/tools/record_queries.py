"""Exact native record reads, shared by typed and reviewed voice turns."""

from __future__ import annotations

import hashlib
import json
import logging
import re

from ai.core.read_numbers import normalize_read_numbers
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)
_WO = re.compile(
    r"^\s*(?:please\s+)?(?:what(?:'s| is) (?:the )?status of|show (?:the )?(?:status|overview) (?:of|for))\s+(?:work\s*order|wo)\s*(?:id\s*)?[:#]?\s*(\d{1,9})\s*[?.!]?\s*$",
    re.I,
)
_BOM = re.compile(
    r"^\s*(?:please\s+)?(?:show|list|what is)\s+(?:the\s+)?(?:bom|bill of materials)\s+(?:for|of)\s+part\s*(?:id\s*)?[:#]?\s*(\d{1,9})\s*[?.!]?\s*$",
    re.I,
)


def _display(value: str) -> str:
    """Remove only the reader's outer prompt wrapper for deterministic display.

    The evidence record stays fenced. This string never enters a model input.
    """
    prefix, suffix = "[UNTRUSTED-CONTENT-BEGIN]\n", "\n[UNTRUSTED-CONTENT-END]"
    if value.startswith(prefix) and value.endswith(suffix):
        return value[len(prefix) : -len(suffix)]
    return value


async def resolve_record_query(query: str, context: dict | None, offered: set[str]) -> dict | None:
    """Never let an exact status/BOM read substitute a model-selected target."""
    from ai.core.tools.inventree.read.maintenance import get_work_order_overview
    from ai.core.tools.inventree.read.stock import get_bom

    parsed = normalize_read_numbers(query)
    work = _WO.fullmatch(parsed.text)
    bom = _BOM.fullmatch(parsed.text)
    if not (work or bom):
        return None
    target = int((work or bom)[1])
    tool = "get_work_order_overview" if work else "get_bom"
    evidence = {
        "requested_id": target,
        "tool": tool,
        "tool_arguments": {"work_order_id" if work else "part_id": target},
        "returned_id": None,
        "resolved": False,
    }
    if bom:
        evidence["tool_arguments"].update(
            include_inherited=True, recursive=False, include_stock=False
        )
    unavailable = {
        "text": "That exact record could not be verified with your current access. Please check its ID.",
        "evidence": evidence,
    }
    if parsed.ambiguous or target <= 0 or tool not in offered:
        return unavailable
    if bom:

        def authorized():
            from ai.core.tools.inventree.read.database import _current_user
            from users.permissions import check_user_role

            user = _current_user()
            return user is not None and check_user_role(user, "part", "view")

        if not await sync_to_async(authorized)():
            return unavailable
    try:
        data = (
            await get_work_order_overview(target)
            if work
            else await get_bom(target, include_stock=False)
        )
    except (ValueError, PermissionError):
        return unavailable
    returned = data.get("work_order_id" if work else "part_id")
    evidence["returned_id"] = returned
    if data.get("error") or type(returned) is not int or returned != target:
        logger.warning(
            "exact_read_identity_rejected tool=%s requested=%s returned=%s", tool, target, returned
        )
        return unavailable
    evidence.update(
        resolved=True,
        record=data,
        source_revision=data.get("updated_at") if work else data.get("source_revision"),
    )
    evidence["source_snapshot_sha256"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()
    logger.info(
        "exact_read_verified tool=%s requested=%s returned=%s revision=%s snapshot=%s",
        tool,
        target,
        returned,
        evidence["source_revision"],
        evidence["source_snapshot_sha256"],
    )
    voice = (context or {}).get("modality") == "voice"
    if work:
        machine = data.get("machine")
        text = f"Work order {target} ({data.get('reference') or 'reference not recorded'}): {_display(data['title'])}. Lifecycle status: {data['lifecycle_status']}; board status: {data.get('board_status') or 'not recorded'}."
        text += (
            f" Machine {machine['machine_id']}: {_display(machine['name'])}."
            if machine
            else " No machine recorded."
        )
        text += f" Assigned to: {data.get('assigned_to') or 'not assigned'}."
    else:
        text = f"BOM for part {target} ({data.get('part_ipn') or data.get('part_name')}; revision {data.get('part_revision') or 'not recorded'}): {data['total_items']} lines."
        rows = data["items"][:3] if voice else data["items"]
        shown = 0
        for row in rows:

            def flag(key, row=row):
                value = row.get(key)
                return "yes" if value is True else "no" if value is False else "not recorded"

            entry = f" Component part {row.get('sub_part')}: {row.get('sub_part_ipn') or row.get('sub_part_name') or 'name not recorded'}, quantity {row.get('quantity', 'not recorded')}; optional {flag('optional')}, variants allowed {flag('allow_variants')}, applies to assembly variants {flag('inherited')}."
            # Reserve room for the omitted-line count inside the speech bound.
            if voice and len(text) + len(entry) > 600:
                break
            text += entry
            shown += 1
        if shown < data["total_items"]:
            text += f" {data['total_items'] - shown} more lines are not listed here. Ask for the complete BOM in text."
    return {"text": text, "evidence": evidence}
