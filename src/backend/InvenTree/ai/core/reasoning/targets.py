"""Select a diagnostic target from server-authorized roots and thread focus."""

from __future__ import annotations

import re
from typing import Any

from ai.core.focus.contract import focus_from_stored


def voice_work_diagnostic_records(
    records: Any,
    focus_snapshot: dict[str, Any] | None,
    *,
    focused_repair_packet_id: int | None,
    content: str,
    modality: str,
) -> tuple[Any, ...]:
    """Project a single-case voice diagnosis after native Work binding.

    This only reduces the provider's copying surface. The original diagnostic
    context and every registry authorization/revision check remain unchanged.
    Cross-machine, comparison and station-wide requests keep their full roots.
    """
    original = tuple(records)
    if modality != "voice" or not isinstance(focus_snapshot, dict):
        return original
    target = focus_from_stored(focus_snapshot.get("focus"))
    if (
        target is None
        or target.kind != "work_order"
        or target.work_order_id != target.id
        or type(focused_repair_packet_id) is not int
        or focused_repair_packet_id <= 0
    ):
        return original

    from ai.core.turn.targets import machine_candidates

    mentioned_machines = machine_candidates(content)
    if mentioned_machines and mentioned_machines != {target.machine_id}:
        return original
    if not re.search(
        r"\b(?:diagnos\w*|troubleshoot\w*|checks?|hypothes\w*|causes?)\b",
        content,
        re.I,
    ):
        return original
    if re.search(
        r"\b(?:compare\w*|comparison|versus|vs|other|another|across|fleet|"
        r"all|both|between|multiple|several|every|station[ -]wide)\b"
        r"|\b(?:station\s+(?:status|health|summary)|(?:status|health|summary)\s+"
        r"(?:of|for)\s+(?:the\s+)?station|each\s+(?:machine|asset|pump))\b",
        content,
        re.I,
    ):
        return original
    selected = tuple(
        record
        for record in original
        if (record.entity_type, record.entity_id)
        in {("machine", target.machine_id), ("repair_packet", focused_repair_packet_id)}
    )
    if len(selected) != 2:
        return original
    machine = next((record for record in selected if record.entity_type == "machine"), None)
    packet = next((record for record in selected if record.entity_type == "repair_packet"), None)
    if (
        machine is None
        or packet is None
        or packet.linked_machine_id != machine.entity_id
        or not machine.expected_revision
        or not packet.expected_revision
    ):
        return original

    def normalized(value):
        return " ".join(re.findall(r"[a-z]+|\d+", value.casefold()))

    text = normalized(content)
    current_name = normalized(machine.display_name)
    equipment = re.findall(r"\b(?:pump|blower|compressor|turbine)\s*0*(\d+)\b", content, re.I)
    current_equipment = {
        int(value)
        for value in re.findall(
            r"\b(?:pump|blower|compressor|turbine)\s*0*(\d+)\b", machine.display_name, re.I
        )
    }
    if any(int(value) not in current_equipment for value in equipment):
        return original
    for record in original:
        if record.entity_type != "machine" or record.entity_id == machine.entity_id:
            continue
        # Preserve explicitly named other sites/equipment even without typed IDs.
        for label in record.display_name.split("/"):
            name = normalized(label)
            if name and name not in current_name and f" {name} " in f" {text} ":
                return original
    return selected


def reasoning_target_ids(
    records: Any, focus_snapshot: dict[str, Any] | None
) -> tuple[int | None, int | None]:
    """Never select an unrelated record merely because it was listed last."""
    machines = {
        record.entity_id
        for record in records
        if record.entity_type == "machine"
        and type(record.entity_id) is int
        and record.entity_id > 0
    }
    if focus_snapshot is not None:
        if not isinstance(focus_snapshot, dict):
            return None, None
        target = focus_from_stored(focus_snapshot.get("focus"))
        if target is None:
            return None, None
        candidate = target.machine_id
        if target.kind == "machine" and candidate != target.id:
            return None, None
        machine_id = candidate if candidate in machines else None
        return machine_id, None
    else:
        machine_id = next(iter(machines)) if len(machines) == 1 else None
    packets = {
        record.entity_id
        for record in records
        if record.entity_type == "repair_packet"
        and machine_id is not None
        and record.linked_machine_id == machine_id
        and type(record.entity_id) is int
        and record.entity_id > 0
    }
    return machine_id, next(iter(packets)) if len(packets) == 1 else None
