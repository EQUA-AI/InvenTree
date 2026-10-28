"""Bind optional diagnostic sources to an owned, server-confirmed Work focus."""

import re
from dataclasses import replace


def manual_passage_query(content):
    """Retain only the exact requested passage topic from the admitted request."""
    from ai.core.agents.voice_routing import VoiceComplexityRouter

    if not isinstance(content, str) or not 1 <= len(content) <= 1000:
        return ""
    match = re.match(
        r"^\s*(?:please\s+)?open\s+(?:(?:the|that|this)\s+)?"
        r"(?:(?:weg|motor|manufacturer|attached|related)\s+){0,2}manual\s+passage\s+"
        r"(?:on|about|regarding)\s+(?P<topic>.+?)(?=[.!?](?:\s|$)|[;\n]|$)",
        content,
        re.I,
    )
    if (
        match is None
        or re.search(r"\b(?:another|other|compare|versus|across)\b", content, re.I)
        or not VoiceComplexityRouter._read_source_navigation(content, confirmed_work_focus=True)
    ):
        return ""
    return match.group("topic").strip()


def inspection_requested(content):
    """Offer E03 for a current source request or a question about its findings."""
    if not isinstance(content, str):
        return False
    text = " ".join(content.casefold().split())
    label = r"(?:p08[- ]temp[- ]e03|e[- ]?(?:zero|0)[- ]?(?:three|3))"
    request = r"\b(?:open|read|retrieve|review|show|get|consult)\b"
    if re.search(
        r"\b(?:do not|don't|never)\s+(?:open|read|retrieve|review|show|get|consult|use)\b", text
    ):
        return False
    if re.search(request + r"[^.!?]{0,140}\b" + label + r"\b", text):
        return True
    # A user-supplied description of a record is not evidence. When the user
    # asks what its stated finding means, offer the same native reader so the
    # answer can check the actual source instead of trusting the description.
    return bool(
        re.search(r"\b" + label + r"\s+(?:reports?|shows?|records?|says?)\b", text)
        and re.search(r"\b(?:what|why|which|are|is|does|do|could|should)\b[^?]{0,180}\?", text)
        and re.search(r"\b(?:causes?|replac\w*|parts?|checks?|justif\w*)\b", text)
    )


def bind_repair_sources(context, *, actor, thread_id, focus_snapshot, content, modality="text"):
    """Narrow source availability without changing the core diagnostic authority."""
    from ai.core.focus.contract import focus_from_stored
    from ai.core.tools.diagnostics import DiagnosticContext
    from aichat.models import ChatThread
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
    from tasks.services.repair_scope_drafts import _repair_source_work
    from tasks.services.work_orders import WorkOrderCommandError

    if not isinstance(context, DiagnosticContext):
        return context
    context = replace(
        context,
        focused_repair_packet_id=None,
        prepared_inspection_requested=False,
        original_manual_passage_request="",
    )
    if not isinstance(focus_snapshot, dict):
        return context
    target = focus_from_stored(focus_snapshot.get("focus"))
    if target is None or target.kind != "work_order" or target.work_order_id != target.id:
        return context
    if str(context.principal.user_pk) != str(actor.user_pk):
        return context
    owner = get_user_model().objects.filter(pk=actor.user_pk, is_active=True).first()
    if owner is None:
        return context
    thread = ChatThread.objects.filter(
        pk=thread_id,
        owner=owner,
        scope_key=actor.scope,
        conversation_focus=focus_snapshot["focus"],
        focus_version=focus_snapshot.get("version"),
    ).first()
    if thread is None:
        return context
    try:
        work, _, _, packet = _repair_source_work(target.id, owner)
    except (ObjectDoesNotExist, PermissionDenied, WorkOrderCommandError):
        return context
    root = context.root_for("repair_packet", packet.pk)
    if (
        root is None
        or root.linked_machine_id != work.machine_id
        or target.machine_id != work.machine_id
        or root.expected_revision != packet.updated_at.isoformat()
    ):
        return context
    return replace(
        context,
        focused_repair_packet_id=packet.pk,
        prepared_inspection_requested=inspection_requested(content),
        original_manual_passage_request=(
            content if modality == "voice" and manual_passage_query(content) else ""
        ),
    )
