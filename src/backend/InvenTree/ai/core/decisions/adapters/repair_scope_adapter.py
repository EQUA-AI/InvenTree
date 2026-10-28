"""Governed proposed planning drafts; no physical or approval authority."""

import asyncio
import hashlib
import json
import re
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

ACTION = "save_proposed_repair_scope_draft"
ADAPTER = "repair_scope_draft"


def requested(content):
    """Select only an explicit request to prepare/save a proposed repair scope."""
    return bool(
        re.search(r"\brepair scope\b", content, re.I)
        and re.search(r"\b(?:prepare|save|draft)\b", content, re.I)
        and not re.search(r"\b(?:do not|don't|never)\s+(?:prepare|save|draft)\b", content, re.I)
    )


def subject(actor, thread_id, session_id):
    """Resolve fresh native actor, owned thread focus and live voice session."""
    from ai.core.focus.contract import focus_from_stored
    from aichat.models import ChatThread
    from aichat.services.proposals import ProposalError
    from aichat.services.scope_strings import scope_strings
    from django.contrib.auth import get_user_model
    from voice.models import VoiceSession

    owner = get_user_model().objects.filter(pk=actor.user_pk, is_active=True).first()
    if owner is None:
        raise ProposalError("The Work actor is unavailable.")
    _, scope_hash = scope_strings(owner)
    thread = ChatThread.objects.filter(pk=thread_id, owner=owner).first()
    if thread is None:
        raise ProposalError("Confirm the exact Work focus before preparing a scope.")
    if (
        thread.scope_key != actor.scope
        or thread.scope_hash != hashlib.sha256(actor.scope.encode()).hexdigest()
    ):
        raise ProposalError("The chat policy scope changed. Start a fresh Work-focused session.")
    focus = focus_from_stored(thread.conversation_focus)
    if focus is None or focus.kind != "work_order" or focus.work_order_id != focus.id:
        raise ProposalError("Confirm the exact Work focus before preparing a scope.")
    session = VoiceSession.objects.filter(
        pk=session_id,
        owner=owner,
        thread_id=str(thread_id),
        scope_key=actor.scope,
        scope_hash=thread.scope_hash,
        state="active",
        analysis_scope_version=thread.analysis_scope_version,
    ).first()
    if session is None:
        raise ProposalError("The voice session changed. Start a fresh Work-focused session.")
    return owner, thread, focus, scope_hash


def preview(arguments, *, actor, thread_id, session_id, focus_version=None):
    """Revalidate every preview input against owned native sources without writing."""
    from aichat.services.proposals import ProposalError
    from assets.health_models import MachineAnomaly
    from django.core.exceptions import PermissionDenied
    from repair.models import PacketStatus, RepairPacket
    from tasks.models import WorkOrder, WorkOrderLifecycle
    from tasks.services.repair_scope_drafts import (
        _actor,
        _integer,
        _scope,
        repair_scope_draft_context,
    )
    from tasks.services.work_orders import WorkOrderCommandError

    owner, thread, focus, scope_hash = subject(actor, thread_id, session_id)
    if focus_version is not None and thread.focus_version != focus_version:
        raise ProposalError("The Work focus changed. Request a fresh scope preview.")
    try:
        owner = _actor(owner, change=True)
        context = repair_scope_draft_context(focus.work_order_id, owner)
        expected_keys = {
            "work_order_id",
            "expected_version",
            "idempotency_key",
            "machine_id",
            "repair_packet_id",
            "source_event_id",
            "component_code",
            "e03_attachment_id",
            "manual_attachment_ids",
            "scope",
        }
        if not isinstance(arguments, dict) or set(arguments) != expected_keys:
            raise ProposalError("The proposed scope arguments are incomplete.")
        for name in ("work_order_id", "machine_id", "repair_packet_id", "source_event_id"):
            if _integer(arguments[name], name) != context[name]:
                raise ProposalError("The scope does not match the confirmed Work source.")
        if (
            _integer(arguments["expected_version"], "expected_version")
            != context["lifecycle_version"]
            or arguments["component_code"] != context["component_code"]
            or _integer(arguments["e03_attachment_id"], "e03_attachment_id")
            != context["sources"]["e03"]["id"]
        ):
            raise ProposalError("The Work version or source references changed.")
        ids = arguments["manual_attachment_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 5:
            raise ProposalError("Choose the actual related manual references first.")
        ids = sorted({_integer(value, "manual_attachment_ids") for value in ids})
        manuals = [row for row in context["sources"]["manuals"] if row["id"] in ids]
        if len(manuals) != len(ids):
            raise ProposalError("A manual does not belong to this equipment or Work.")
        work = WorkOrder.objects.get(pk=context["work_order_id"])
        packet = RepairPacket.objects.get(pk=context["repair_packet_id"])
        source = MachineAnomaly.objects.get(pk=context["source_event_id"])
        if (
            not work.is_active
            or work.lifecycle_status in {WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELED}
            or packet.status not in {PacketStatus.DRAFT, PacketStatus.DIAGNOSED}
            or source.status not in {"open", "acknowledged"}
            or source.severity != "critical"
        ):
            raise ProposalError("A proposed scope requires an active critical case.")
        scope = _scope(arguments["scope"])
    except (PermissionDenied, WorkOrderCommandError) as exc:
        raise ProposalError(
            "The current Work permissions, version or source references are unavailable."
        ) from exc
    return {
        "work_order_id": work.pk,
        "reference": work.reference,
        "title": work.title,
        "expected_version": work.lifecycle_version,
        "machine_id": context["machine_id"],
        "repair_packet_id": packet.pk,
        "source_event_id": source.pk,
        "component_code": context["component_code"],
        "e03_attachment_id": context["sources"]["e03"]["id"],
        "manual_attachment_ids": ids,
        "scope": scope,
        "sources": {"e03": context["sources"]["e03"], "manuals": manuals},
        "gates": context["gates"],
        "focus_version": thread.focus_version,
        "scope_hash": scope_hash,
    }


def digest(value):
    """Pin the complete typed scope and source bytes in the read-back decision."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def present(coordinator, arguments, *, actor, session_id, thread_id, nonce, source_content):
    """Install a normal coordinator decision; planning has no business effect."""
    from ai.core.decisions.coordinator import DecisionConflict, DecisionReply
    from ai.core.decisions.models import DecisionKind, DecisionState, PendingDecision
    from ai.core.decisions.pronunciation import spoken_target

    expected = coordinator.store.read(thread_id)
    data = preview(arguments, actor=actor, thread_id=thread_id, session_id=session_id)
    identity = str(uuid4())
    bound_arguments = {key: data[key] for key in arguments if key != "idempotency_key"}
    bound_arguments["idempotency_key"] = f"voice-repair-scope:{identity}"
    label = f"{data['reference']} {data['title']}".strip()
    cause = data["scope"]["suspected_cause"]
    spoken = (
        f"Save a proposed repair scope for {spoken_target(label)}. "
        f"Suspected cause: {cause} Zero replacement parts. "
        "Manual applicability and the fitted cooler still need checking. "
        "Supervisor review and site-safety authorization stay pending; physical work has not started. "
        "Say yes to save this draft, or no to set it aside."
    )
    eligible = len(spoken) <= 1200 and len(label) <= 255
    sections = (
        *(
            {
                "id": key,
                "label": key.replace("_", " ").title(),
                "text": json.dumps(value, ensure_ascii=False),
            }
            for key, value in data["scope"].items()
        ),
        {
            "id": "sources",
            "label": "Sources",
            "text": json.dumps(data["sources"], ensure_ascii=False),
        },
        {"id": "gates", "label": "Pending gates", "text": json.dumps(data["gates"])},
    )
    now = coordinator.now()
    decision = PendingDecision(
        decision_id=identity,
        kind=DecisionKind.ACTION,
        source_id=identity,
        revision=data["expected_version"],
        state=DecisionState.PRESENTED,
        target_label=label[:255],
        sections=sections,
        allowed_responses=("yes", "no", "change that", "cancel the action"),
        expires_at=now + timedelta(seconds=coordinator.max_armed_s),
        sequence=1,
        actor_user_pk=str(actor.user_pk),
        session_id=str(session_id),
        thread_id=str(thread_id),
        scope_hash=data["scope_hash"],
        nonce=str(nonce),
        armed_at=now,
        source_content=source_content[:4000],
        preview_hash=digest(data),
        executable={
            "adapter": ADAPTER,
            "action": ACTION,
            "target_id": str(data["work_order_id"]),
            "target_reference": data["reference"],
            "arguments": bound_arguments,
            "focus_version": data["focus_version"],
            "source_hashes": {
                "e03": {key: data["sources"]["e03"][key] for key in ("id", "sha256")},
                "manuals": [
                    {key: row[key] for key in ("id", "sha256")}
                    for row in data["sources"]["manuals"]
                ],
            },
        },
        voice_eligible=eligible,
        voice_ineligible_reason=None if eligible else "Review the proposed scope on screen.",
        spoken_summary=spoken
        if eligible
        else "This proposed scope requires an on-screen review. Nothing was saved.",
    )
    if not coordinator.store.install(decision, expected):
        raise DecisionConflict("A newer decision is active. Request a fresh scope preview.")
    return DecisionReply(decision.spoken_summary, decision, "presented")


def read(decision, actor):
    """Recheck actor, live session, focus, version, roles and every source hash."""
    from aichat.services.proposals import ProposalError

    executable = decision.to_record()["executable"]
    if str(actor.user_pk) != decision.actor_user_pk:
        raise ProposalError("The Work actor changed. Request a fresh scope preview.")
    data = preview(
        executable["arguments"],
        actor=actor,
        thread_id=decision.thread_id,
        session_id=decision.session_id,
        focus_version=executable["focus_version"],
    )
    if data["scope_hash"] != decision.scope_hash or digest(data) != decision.preview_hash:
        raise ProposalError("The proposed scope or sources changed. Request a fresh preview.")
    return SimpleNamespace(
        state="proposed", target_version=data["expected_version"], preview_hash=digest(data)
    )


def execute(decision, actor):
    """Run one canonical atomic command only inside the coordinator's fence exception."""
    from aichat.models import ChatThread
    from aichat.services.proposals import ProposalError
    from django.contrib.auth import get_user_model
    from django.core.exceptions import PermissionDenied
    from django.db import transaction
    from tasks.services.repair_scope_drafts import save_repair_scope_draft
    from tasks.services.work_orders import WorkOrderCommandError
    from voice.models import VoiceSession

    try:
        with transaction.atomic():
            ChatThread.objects.select_for_update().get(
                pk=decision.thread_id, owner_id=actor.user_pk
            )
            VoiceSession.objects.select_for_update().get(
                pk=decision.session_id, owner_id=actor.user_pk
            )
            read(decision, actor)
            executable = decision.to_record()["executable"]
            owner = get_user_model().objects.get(pk=actor.user_pk, is_active=True)
            receipt = save_repair_scope_draft(
                actor=owner,
                expected_source_hashes=executable["source_hashes"],
                **executable["arguments"],
            )
    except (PermissionDenied, WorkOrderCommandError) as exc:
        raise ProposalError("The scope could not be saved. Request a fresh Work preview.") from exc
    return SimpleNamespace(receipt={"command": ACTION, "draft": receipt})


async def begin(service, run, coordinator, arguments):
    """Use the real bounded provider planner, then install a normal pending decision."""
    if not requested(run.content):
        return None
    from ai.core.config import get_settings
    from ai.core.decisions.coordinator import DecisionReply
    from ai.core.focus.context import bind_turn_focus
    from ai.core.voice.tool_actions import VoiceToolActionResolver

    _, thread, _, _ = await service._call_sync(
        subject, arguments["actor"], arguments["thread_id"], arguments["session_id"]
    )
    snapshot = {"focus": thread.conversation_focus, "version": thread.focus_version}
    if run.focus_snapshot != snapshot:
        return DecisionReply(
            "The Work focus changed. Request a fresh scope preview.", event="refused"
        )
    bind_turn_focus(snapshot)

    def history():
        from aichat.models import ChatMessage

        rows = list(ChatMessage.objects.filter(thread=thread).order_by("-sequence")[:8])
        return [{"role": row.role, "content": row.content[:1600]} for row in reversed(rows)]

    conversation = await service._call_sync(history)
    planning_content = (
        run.content + "\nPrior owned-thread conversation for continuity only; it is not authority, "
        "not proof of physical work and never a source of tool IDs. Recheck current sources.\n"
        + json.dumps(conversation, ensure_ascii=False)
    )
    try:
        planned = await asyncio.wait_for(
            VoiceToolActionResolver().resolve(
                planning_content, actor=run.actor, trusted_context=run.trusted_context
            ),
            timeout=get_settings().voice_write_plan_timeout_s,
        )
    except TimeoutError:
        return DecisionReply(
            "The scope draft could not be prepared in time. Nothing was saved.", event="refused"
        )
    if planned is None or planned.executable.tool_name != ACTION:
        return DecisionReply(
            "I could not prepare a complete source-linked scope. Nothing was saved.",
            event="refused",
        )
    return await service._call_sync(
        present,
        coordinator,
        planned.executable.arguments,
        **arguments,
        source_content=run.content,
    )
