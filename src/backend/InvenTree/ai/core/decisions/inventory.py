"""Exact inventory intent parsing and authoritative identity resolution."""

import re

from ai.core.decisions.resolver import WorkOrderIntent
from ai.core.decisions.work_order_review import readable


class InventoryAmbiguity(ValueError):
    """A server-owned list of identities; selecting one only creates a review."""

    def __init__(self, question, options):
        super().__init__(question)
        self.options = options


def parse_stock_intent(content):
    """Recognize only the four governed movements; never infer quantities."""
    text = content.strip().rstrip(".!?")
    patterns = (
        (
            "stock.read",
            r"(?:show stock|stock|how much stock) of part (?P<part_name>.+?)(?: page (?P<page>\d+))?",
        ),
        ("stock.add", r"add (?P<quantity>\d+(?:\.\d+)?) to stock item (?P<stock_item_id>\d+)"),
        (
            "stock.remove",
            r"remove (?P<quantity>\d+(?:\.\d+)?) from stock item (?P<stock_item_id>\d+)",
        ),
        ("stock.count", r"count stock item (?P<stock_item_id>\d+) as (?P<quantity>\d+(?:\.\d+)?)"),
        (
            "stock.transfer",
            r"transfer (?P<quantity>\d+(?:\.\d+)?) from stock item (?P<stock_item_id>\d+) to location (?P<location_name>.+?)",
        ),
        (
            "stock.add",
            r"add (?P<quantity>\d+(?:\.\d+)?) of part (?P<part_name>.+?) to location (?P<location_name>.+?)",
        ),
    )
    for action, pattern in patterns:
        match = re.fullmatch(pattern + r"(?: because (?P<reason>.+))?", text, re.I)
        if match:
            params = {key: value for key, value in match.groupdict().items() if value is not None}
            reason = params.pop("reason", "")
            if "stock_item_id" in params:
                params["stock_item_id"] = int(params["stock_item_id"])
            return WorkOrderIntent(
                str(params.get("stock_item_id") or params.get("part_name")), reason, action, params
            )
    return None


def _ambiguous(question, rows, field, label):
    if len(rows) > 3:
        from aichat.services.proposals import ProposalError

        raise ProposalError(
            "More than three identities match. Use an exact IPN or full location path."
        )
    raise InventoryAmbiguity(
        question,
        [
            {
                "id": f"{field}:{row.pk}",
                "label": label(row),
                "kind": "inventory",
                "ref": {"field": field, "id": row.pk, "label": label(row)},
            }
            for row in rows
        ],
    )


def resolve_parameters(actor, parameters):
    """Resolve names/IPNs and full paths to exact IDs under current ownership."""
    from aichat.services.proposals import ProposalError
    from aichat.services.stock_commands import require_role
    from django.db.models import Q
    from part.models import Part
    from stock.models import StockLocation

    require_role(actor, "view")
    params = dict(parameters)
    name = params.pop("part_name", None)
    if name is not None:
        parts = list(
            Part.objects.filter(Q(IPN__iexact=name) | Q(name__iexact=name), active=True).order_by(
                "pk"
            )[:4]
        )
        if len(parts) > 1:
            _ambiguous(
                "Which part do you mean?",
                parts,
                "part_id",
                lambda row: f"{row.name}, IPN {row.IPN or 'not set'}, part {row.pk}",
            )
        if not parts:
            raise ProposalError("The part is unavailable. Say its exact name or IPN.")
        params["part_id"] = parts[0].pk
    name = params.pop("location_name", None)
    if name is not None:
        # Full paths are derived, not a user-supplied numeric scope override.
        locations = []
        for row in (
            StockLocation.objects
            .filter(Q(pathstring__iexact=name) | Q(name__iexact=name))
            .order_by("pk")
            .iterator()
        ):
            if row.check_ownership(actor):
                locations.append(row)
                if len(locations) == 4:
                    break
        if len(locations) > 1:
            _ambiguous(
                "Which location do you mean?", locations, "location_id", lambda row: row.pathstring
            )
        if not locations:
            raise ProposalError("The location is unavailable. Say its full location path.")
        params["location_id"] = locations[0].pk
    return params


def read_stock(actor, parameters):
    """Deterministic, bounded pages with literal canonical quantities and units."""
    from aichat.services.proposals import ProposalError
    from aichat.services.stock_commands import require_role
    from part.models import Part
    from stock.models import StockItem

    require_role(actor, "view")
    params = resolve_parameters(actor, parameters)
    part = Part.objects.filter(pk=params.get("part_id"), active=True).first()
    if part is None:
        raise ProposalError("The selected part is no longer available.")
    page = int(params.get("page") or 1)
    if not 1 <= page <= 100:
        raise ProposalError("Choose an inventory page between one and one hundred.")
    rows = []
    offset = (page - 1) * 5
    visible = 0
    for item in (
        StockItem.objects.filter(part=part).select_related("location").order_by("pk").iterator()
    ):
        if not item.check_ownership(actor):
            continue
        if visible >= offset:
            rows.append(item)
        visible += 1
        if len(rows) == 6:
            break
    label = f"{part.name}, IPN {part.IPN or 'not set'}"
    if not rows:
        return f"{label}. No visible stock records on page {page}."
    lines = [f"{label}. Inventory page {page}."]
    for index, item in enumerate(rows[:5], start=1):
        lines.append(
            f"{index}. Stock item {item.pk}: {item.quantity} {part.units or 'each'}; "
            f"location {item.location.pathstring if item.location else 'not set'}; "
            f"serial {item.serial or 'not set'}."
        )
    if len(rows) > 5:
        lines.append(
            f"More records are available. Say stock of part {part.IPN or part.name} page {page + 1}."
        )
    # Preserve record boundaries through canonical speech validation. Otherwise
    # the prose chunker treats numeric identifiers as cross-page qualifiers.
    return "\n".join(lines)


async def pending_selection(service, run, coordinator, arguments):
    """Consume inventory choices once, with actor/session/scope and identity checks."""
    from dataclasses import replace
    from datetime import UTC, datetime

    from ai.core.decisions.coordinator import DecisionReply
    from ai.core.questions.answers import interpret_question_answer
    from aichat.services.proposals import ProposalError

    record = service.question_store.take(run.thread.pk)
    if record is None:
        return None
    if record.get("source") != "voice_inventory":
        service.question_store.save(run.thread.pk, record)
        return None
    owner, (_, scope_hash) = await service._call_sync(coordinator.adapter.owner_scope, run.actor)
    options = record.get("options") or []
    interpretation = interpret_question_answer(run.content, options, modality="voice")
    if interpretation.outcome != "selected":
        return DecisionReply(
            "The inventory selection was set aside. Request a fresh inventory preview.",
            event="declined",
        )
    option = options[interpretation.option_index]
    ref = option.get("ref") or {}
    if (
        ref.get("actor_id") != owner.pk
        or ref.get("scope_hash") != scope_hash
        or ref.get("session_id") != arguments["session_id"]
        or datetime.fromisoformat(record["expires_at"]) <= datetime.now(UTC)
    ):
        raise ProposalError("The inventory choices expired or your session changed.")
    intent = WorkOrderIntent(**ref["intent"]) if ref.get("intent") else None
    if intent is None:
        raise ProposalError("Request a fresh inventory preview.")

    def recheck():
        from part.models import Part
        from stock.models import StockLocation

        if ref["field"] == "part_id":
            row = Part.objects.filter(pk=ref["id"], active=True).first()
            label = f"{row.name}, IPN {row.IPN or 'not set'}, part {row.pk}" if row else None
        else:
            row = StockLocation.objects.filter(pk=ref["id"]).first()
            label = row.pathstring if row and row.check_ownership(owner) else None
        if label != ref["label"]:
            raise ProposalError("The selected inventory identity changed. Request new choices.")

    await service._call_sync(recheck)
    parameters = dict(intent.parameters)
    parameters.pop("part_name" if ref["field"] == "part_id" else "location_name", None)
    parameters[ref["field"]] = ref["id"]
    intent = replace(intent, parameters=parameters)
    try:
        if intent.action == "stock.read":
            return DecisionReply(await service._call_sync(read_stock, owner, parameters))
        return await service._call_sync(
            coordinator.present,
            intent,
            **arguments,
            source_content=run.content,
            expected=await service._call_sync(coordinator.store.read, run.thread.pk),
        )
    except InventoryAmbiguity as exc:
        exc.resume_intent = intent
        raise


def review(action, preview, spoken_label):
    """Complete before/after quantities, literal units and both location paths."""
    keys = (
        "quantity",
        "units",
        "quantity_before",
        "quantity_after",
        "serial",
        "location",
        "destination",
        "reason",
        "warning",
    )
    sections = tuple(
        {"id": key, "label": key.replace("_", " ").title(), "text": readable(preview.get(key))}
        for key in keys
    )
    phrase = preview.get("confirm_phrase") or None
    speech = (
        f"{action.removeprefix('stock.').title()} {spoken_label}. "
        + " ".join(f"{section['label']}: {section['text']}." for section in sections)
        + f" Say {phrase or 'yes'} to proceed, or no to set it aside."
    )
    return speech, sections, phrase
