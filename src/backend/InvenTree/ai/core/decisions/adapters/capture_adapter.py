"""Live closeout capture: consent, immutable dictation, review, separate handoff.

Only explicit note commands enter the transcript ledger. Chat and decision
commands are never silently recorded as closeout evidence.
"""

import os
import re

from ai.core.decisions.resolver import WorkOrderIntent, _reference
from ai.core.voice.experience import CONSENT_VERSION
from aichat.services.proposals import ProposalError, _authorized_work_order
from django.conf import settings
from django.db import transaction
from voice.models import CaptureState, VoiceCaptureSession, VoiceSession
from voice.services import capture as captures

ACTIONS = {"closeout.consent", "closeout.accept", "closeout.handoff"}
DISCLOSURE = (
    "Your dictated text and corrections will be saved as a closeout note. "
    "AIMMS does not store audio. You must review and accept the exact note, "
    "then separately confirm its handoff. This does not complete the work order."
)


def require_enabled():
    """Check both AI and Django policy planes at every capture operation."""
    from ai.core.config import get_settings

    if not getattr(get_settings(), "feature_voice_closeout", False):
        raise ProposalError("Closeout by voice is disabled.")
    key = os.environ.get("AIMMS_SINGLE_SITE_POLICY_KEY", "").strip()
    if not (
        key
        and getattr(settings, "AIMMS_WORK_ORDERS_ENABLED", False)
        and getattr(settings, "AIMMS_CLOSEOUT_WIZARD_ENABLED", False)
        and os.environ.get("AIMMS_VOICE_CAPTURE_ENABLED") == "1"
        and "closeout" in os.environ.get("AIMMS_VOICE_PURPOSES", "").split(",")
        and os.environ.get("AIMMS_VOICE_CONSENT_VERSION") == CONSENT_VERSION
    ):
        raise ProposalError(
            "Closeout capture policy is incomplete. Ask an administrator to check both app planes."
        )
    return key


def session_for(actor, session_id):
    """Bind the current actor, active live session and explicit consent version."""
    session = VoiceSession.objects.filter(
        pk=session_id, owner=actor, consent_version=CONSENT_VERSION
    ).first()
    if session is None or session.is_terminal:
        raise ProposalError("Start a new voice session with the current consent disclosure.")
    return session


def authorize(actor, work_order, intent):
    """Scope and canonical capture permission precede disclosure of any note."""
    from tasks.permissions import require_permission
    from tasks.services.closeout_capture import CAPTURE_CLOSEOUT, _require_capture_eligible
    from tasks.services.work_orders import _require_no_packet

    require_enabled()
    session_for(actor, intent.get("live_session_id"))
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_no_packet(work_order)
    _require_capture_eligible(work_order)


def bound_capture(intent, actor=None, *, lock=False):
    """Capture identities cannot move between sessions, targets or site policies."""
    rows = VoiceCaptureSession.objects.select_for_update() if lock else VoiceCaptureSession.objects
    filters = {
        "pk": intent.get("capture_id"),
        "live_session_id": intent.get("live_session_id"),
        "purpose": "closeout",
        "scope_key": require_enabled(),
    }
    if actor is not None:
        filters["owner"] = actor
    capture = rows.filter(**filters).first()
    if capture is None or capture.is_terminal or capture.state == CaptureState.COMMITTED:
        raise ProposalError("This closeout capture is unavailable. Start a fresh review.")
    return capture


def preview(work_order, intent, action):
    """Only authoritative full text and hashes enter the review."""
    base = {
        "site_policy": require_enabled(),
        "consent_version": CONSENT_VERSION,
        "warning": DISCLOSURE,
    }
    if action == "closeout.consent":
        return {**base, "disclosure": DISCLOSURE, "confirm_phrase": "yes"}
    capture = bound_capture(intent)
    if (
        capture.target_work_order_id != work_order.pk
        or capture.target_version != work_order.lifecycle_version
    ):
        raise ProposalError("The capture target changed. Start a fresh capture.")
    latest = capture.revisions.order_by("-revision").first()
    if (
        latest is None
        or str(latest.pk) != intent.get("revision_id")
        or latest.content_hash != intent.get("content_hash")
    ):
        raise ProposalError("The note changed. Read and accept its latest revision.")
    if action == "closeout.handoff" and capture.accepted_revision_id != latest.pk:
        raise ProposalError("Accept the exact note before requesting handoff.")
    if intent.get("auditory_review_id"):
        from ai.core.decisions.adapters.capture_review import require_complete

        reviewed = require_complete(intent["auditory_review_id"], capture, latest)
        base["auditory_review_id"] = str(reviewed.pk)
    return {
        **base,
        "capture_id": str(capture.pk),
        "revision_id": str(latest.pk),
        "revision": latest.revision,
        "content_hash": latest.content_hash,
        "full_text": latest.full_text,
        "capture_state": capture.state,
        "confirm_phrase": "confirm handoff" if action == "closeout.handoff" else "accept this note",
    }


def execute(proposal, actor):
    """Called inside the locked proposal transaction; receipts commit with effects."""
    from tasks.models import WorkOrderCommand
    from tasks.services.readiness import evaluate_work_order_readiness

    intent, action = proposal.intent, proposal.action_type
    work_order = _authorized_work_order(actor, proposal.target_work_order_id)
    authorize(actor, work_order, intent)
    try:
        if action == "closeout.consent":
            session = session_for(actor, intent["live_session_id"])
            VoiceSession.objects.select_for_update().get(pk=session.pk)
            if VoiceCaptureSession.objects.filter(
                live_session=session, state__in=["active", "review", "accepted"]
            ).exists():
                raise ProposalError(
                    "A closeout note is already active. Continue it or say cancel closeout note first."
                )
            capture = captures.create_capture(
                owner=actor,
                scope_key=require_enabled(),
                purpose="closeout",
                work_order_id=work_order.pk,
                work_order_version=work_order.lifecycle_version,
                consent_version=CONSENT_VERSION,
                idempotency_key=f"proposal:{proposal.pk}",
                policy_version="voice-closeout-v1",
                enabled_purposes=("closeout",),
            )
            capture.live_session = session
            capture.save(update_fields=["live_session"])
            return {
                "command": action,
                "capture_id": str(capture.pk),
                "consent_version": CONSENT_VERSION,
            }
        capture = bound_capture(intent, actor, lock=True)
        receipt = {
            "command": action,
            "capture_id": str(capture.pk),
            "revision_id": intent["revision_id"],
            "content_hash": intent["content_hash"],
        }
        if action == "closeout.accept":
            acceptance = captures.accept_revision(
                capture=capture,
                revision_id=intent["revision_id"],
                content_hash=intent["content_hash"],
                accepted_by=actor,
            )
            return {**receipt, "acceptance_id": acceptance.pk}
        row = captures.handoff_capture(capture=capture)
        command = WorkOrderCommand.objects.get(
            work_order=work_order, idempotency_key=f"closeout-voice:{intent['revision_id']}"
        )
        readiness = evaluate_work_order_readiness(work_order, action="complete", actor=actor)
        return {
            **receipt,
            "closeout_capture_id": row.pk,
            "event_id": int(command.result_ref),
            "remaining_gates": [item.message for item in readiness.blockers],
        }
    except captures.CaptureError as exc:
        raise ProposalError(str(exc)) from exc


def verified(proposal):
    """Immutable revision, acceptance and canonical handoff evidence, never a boolean."""
    from tasks.closeout_models import CloseoutCapture
    from tasks.models import WorkOrderCommand, WorkOrderEvent
    from voice.models import VoiceTranscriptAcceptance

    receipt = proposal.receipt or {}
    if receipt.get("command") != proposal.action_type:
        return False
    capture = VoiceCaptureSession.objects.filter(
        pk=receipt.get("capture_id"),
        owner=proposal.owner,
        target_work_order_id=proposal.target_work_order_id,
        live_session_id=proposal.intent.get("live_session_id"),
    ).first()
    if capture is None:
        return False
    if proposal.action_type == "closeout.consent":
        return (
            capture.idempotency_key == f"proposal:{proposal.pk}"
            and capture.consent_version == receipt.get("consent_version") == CONSENT_VERSION
        )
    if receipt.get("revision_id") != proposal.intent.get("revision_id") or receipt.get(
        "content_hash"
    ) != proposal.preview.get("content_hash"):
        return False
    acceptance = VoiceTranscriptAcceptance.objects.filter(
        revision_id=receipt["revision_id"],
        revision__capture=capture,
        content_hash=receipt["content_hash"],
        accepted_by=proposal.owner,
    ).first()
    if acceptance is None:
        return False
    if proposal.action_type == "closeout.accept":
        return acceptance.pk == receipt.get("acceptance_id")
    row = CloseoutCapture.objects.filter(
        pk=receipt.get("closeout_capture_id"),
        work_order_id=proposal.target_work_order_id,
        transcript_reference=receipt["revision_id"],
        created_by=proposal.owner,
        current_revision__source_content_hash=receipt["content_hash"],
    ).first()
    key = f"closeout-voice:{receipt['revision_id']}"
    return bool(
        row
        and WorkOrderCommand.objects.filter(
            work_order_id=proposal.target_work_order_id,
            idempotency_key=key,
            command="closeout_capture",
            status="succeeded",
            result_ref=str(receipt.get("event_id")),
        ).exists()
        and WorkOrderEvent.objects.filter(
            pk=receipt.get("event_id"),
            actor=proposal.owner,
            work_order_id=proposal.target_work_order_id,
            idempotency_key=key,
            event_type="CLOSEOUT_CAPTURE_CREATED",
            metadata__result_metadata__capture_id=row.pk,
        ).exists()
    )


def begin(coordinator, content, *, actor, session_id, thread_id, nonce):
    """Explicit deterministic capture commands, with no model-generated edits."""
    from ai.core.decisions.coordinator import DecisionReply

    # ASR adds sentence punctuation to commands. Preserve literal dictation and
    # replacement-note payloads, including their punctuation, in their entirety.
    text = content.strip()
    command_text = (
        text
        if re.match(r"^(?:note |replace note with )", text, re.I)
        else text.rstrip(".!?").strip()
    )
    start = re.fullmatch(
        r"start close\s*out(?: note)? for (?:work order )?(.{1,120})", command_text, re.I
    )
    command = re.fullmatch(
        r"(?:note (.+)|replace note with (.+)|change (.+) to (.+)|read the whole note(?: page (\d+))?|accept this note|hand\s*off this note|cancel close\s*out note)",
        command_text,
        re.I | re.S,
    )
    if not start and not command:
        return None
    if (
        command
        and command[3]
        and not VoiceCaptureSession.objects.filter(
            live_session_id=session_id,
            owner_id=actor.user_pk,
            state__in=["active", "review", "accepted"],
        ).exists()
    ):
        return None
    owner, _ = coordinator.adapter.owner_scope(actor)
    session = session_for(owner, session_id)
    if session.thread_id != str(thread_id):
        raise ProposalError("The voice thread changed. Start a new capture.")
    require_enabled()
    if start:
        intent = WorkOrderIntent(
            _reference(start[1]) or start[1],
            "",
            "closeout.consent",
            {"live_session_id": str(session.pk)},
        )
    else:
        with transaction.atomic():
            capture = (
                VoiceCaptureSession.objects
                .select_for_update()
                .filter(
                    owner=owner,
                    live_session=session,
                    purpose="closeout",
                    scope_key=require_enabled(),
                    state__in=["active", "review", "accepted"],
                )
                .first()
            )
            if capture is None:
                raise ProposalError(
                    "Start a closeout note and explicitly consent before dictating."
                )
            if re.fullmatch(r"cancel close\s*out note", command_text, re.I):
                captures.cancel_capture(capture=capture)
                return DecisionReply(
                    "Closeout capture canceled. Its existing transcript revisions are retained for audit. No handoff was submitted."
                )
            work_order = _authorized_work_order(owner, capture.target_work_order_id)
            authorize(owner, work_order, {"live_session_id": str(session.pk)})
            if capture.target_version != work_order.lifecycle_version:
                raise ProposalError("The work order changed. Start a fresh capture.")
            latest = capture.revisions.order_by("-revision").first()
            if any(command[index] for index in (1, 2, 3)):
                existing = capture.revisions.filter(source_turn_id=str(nonce)).first()
                if existing:
                    if existing.segments != [{"source_text": content}]:
                        raise ProposalError(
                            "This dictation turn was already used for different text."
                        )
                    return DecisionReply(
                        f"Note revision {existing.revision} is already recorded. No text was added again."
                    )
                if command[3]:
                    if latest is None or latest.full_text.count(command[3]) != 1:
                        raise ProposalError(
                            "The correction is missing or ambiguous. Say replace note with the complete corrected note."
                        )
                    old, replacement = command[3], command[4]
                    # Repeating the SAME literal unit in a correction must not
                    # produce "fifty PSI PSI". No unit conversion is performed.
                    unit = replacement.split()[-1]
                    if unit.casefold() == "psi":
                        # ASR commonly emits lowercase psi. Match only this
                        # unambiguous unit case-insensitively; SI prefixes such
                        # as mPa versus MPa must never be folded together.
                        matches = list(
                            re.finditer(re.escape(old) + r"\s+(?i:psi)\b", latest.full_text)
                        )
                        if len(matches) == 1:
                            old = matches[0].group()
                    elif unit in {
                        "PSI",
                        "bar",
                        "kPa",
                        "MPa",
                        "V",
                        "A",
                        "mm",
                        "cm",
                        "m",
                        "kg",
                        "g",
                        "L",
                        "ml",
                        "°C",
                        "°F",
                    }:
                        with_unit = f"{old} {unit}"
                        if latest.full_text.count(with_unit) == 1:
                            old = with_unit
                    text = latest.full_text.replace(old, replacement, 1)
                else:
                    text = command[2] or "\n".join(
                        value for value in (latest.full_text if latest else "", command[1]) if value
                    )
                latest = captures.append_revision(
                    capture=capture,
                    full_text=text,
                    created_by=owner,
                    segments=[{"source_text": content}],
                    edit_reason="voice_correction"
                    if command[2] or command[3]
                    else "voice_dictation",
                )
                latest.source_turn_id = str(nonce)
                latest.save(update_fields=["source_turn_id"])
                return DecisionReply(
                    f"Note revision {latest.revision} saved. Say read the whole note, then accept this note to review acceptance. No handoff was submitted."
                )
            if latest is None:
                raise ProposalError("Dictate a note before requesting review.")
            if command_text.lower().startswith("read the whole note"):
                from ai.core.decisions.adapters.capture_review import present

                return present(
                    coordinator,
                    capture=capture,
                    revision=latest,
                    page=int(command[5] or 1),
                    actor=actor,
                    nonce=nonce,
                    expected=coordinator.store.read(thread_id),
                )
            from ai.core.decisions.adapters.capture_review import available

            reviewed = available(capture, latest)
            intent = WorkOrderIntent(
                str(work_order.pk),
                "",
                "closeout.accept"
                if command_text.lower() == "accept this note"
                else "closeout.handoff",
                {
                    "live_session_id": str(session.pk),
                    "capture_id": str(capture.pk),
                    "revision_id": str(latest.pk),
                    "content_hash": latest.content_hash,
                    **({"auditory_review_id": str(reviewed.pk)} if reviewed else {}),
                },
            )
    return coordinator.present(
        intent,
        actor=actor,
        session_id=session_id,
        thread_id=thread_id,
        nonce=nonce,
        source_content=content,
        expected=coordinator.store.read(thread_id),
    )
