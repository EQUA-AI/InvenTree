"""Full-note auditory review, separate from acceptance and handoff authority.

Each page is an output-only, session-bound decision with its own bounded
delivery window. A new action can be armed only after every exact page has
completed playback. Reconnect/pause, revision drift and scope drift invalidate
the evidence. No timeout, read request, or arbitrary page acknowledgment counts.
"""

import hashlib
import re
from datetime import timedelta
from uuid import uuid4

from ai.core.decisions.coordinator import DecisionConflict, DecisionReply
from ai.core.decisions.grammar import _normalize_command
from ai.core.decisions.models import DecisionKind, PendingDecision
from aichat.services.proposals import ProposalError, _authorized_work_order
from aichat.services.scope_strings import scope_strings
from django.db import transaction
from voice.models import VoiceCaptureReview, VoiceUtterance

POLICY = "closeout-full-review-v1"


def note_pages(revision):
    """Keep all original characters, including whitespace and literal units."""
    pieces = re.findall(r".{1,900}(?:\s+|$)|\S{1,900}", revision.full_text, re.S)
    if not pieces or "".join(pieces) != revision.full_text:
        raise ProposalError("Read this note on screen; its full text could not be paged.")
    return [
        f"Note revision {revision.revision}, page {index} of {len(pieces)}. {piece}"
        + (
            f" Say read the whole note page {index + 1} for the next page."
            if index < len(pieces)
            else " End of note."
        )
        for index, piece in enumerate(pieces, start=1)
    ]


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate(review, owner):
    """Current actor, session, purpose, site, target and exact revision first."""
    from ai.core.decisions.adapters import capture_adapter as capture

    revision = review.revision
    row = revision.capture
    session = capture.session_for(owner, review.session_id)
    if (
        review.invalidated
        or review.policy_version != POLICY
        or row.owner_id != owner.pk
        or row.live_session_id != session.pk
        or row.scope_key != capture.require_enabled()
        or row.purpose != "closeout"
        or row.state not in ("active", "review", "accepted")
        or row.revisions.order_by("-revision").first().pk != revision.pk
        or digest(revision.full_text) != revision.content_hash
        or review.scope_hash != scope_strings(owner)[1]
        or review.page_hashes != [digest(page) for page in note_pages(revision)]
    ):
        raise ProposalError("The note review changed. Read the whole note again.")
    work_order = _authorized_work_order(owner, row.target_work_order_id)
    capture.authorize(owner, work_order, {"live_session_id": str(session.pk)})
    if (
        work_order.lifecycle_version != review.target_version
        or row.target_version != review.target_version
    ):
        raise ProposalError("The work order changed. Start a fresh capture.")
    return row


def completed(review):
    """Require every exact persisted page; a replay or another session cannot fill a gap."""
    ids = review.utterance_ids
    if len(ids) != len(review.page_hashes) or not all(ids) or len(set(ids)) != len(ids):
        return False
    rows = {
        str(row.pk): row
        for row in VoiceUtterance.objects.filter(session=review.session, pk__in=ids)
    }
    return all(
        (row := rows.get(utterance_id)) is not None
        and row.playback_state == "done"
        and row.spoken_summary_hash == expected == digest(row.spoken_summary)
        for utterance_id, expected in zip(ids, review.page_hashes, strict=True)
    )


def require_complete(review_id, capture, revision):
    review = VoiceCaptureReview.objects.filter(
        pk=review_id, revision=revision, session_id=capture.live_session_id
    ).first()
    if review is None:
        raise ProposalError("Read the whole note before voice acceptance.")
    validate(review, capture.owner)
    if not completed(review):
        raise ProposalError("Every note page must finish playing before voice acceptance.")
    return review


def available(capture, revision):
    review = (
        VoiceCaptureReview.objects
        .filter(revision=revision, session_id=capture.live_session_id, invalidated=False)
        .order_by("-created_at")
        .first()
    )
    if review is None:
        return None
    validate(review, capture.owner)
    return review if completed(review) else None


def invalidate(session_id):
    VoiceCaptureReview.objects.filter(session_id=session_id, invalidated=False).update(
        invalidated=True
    )


def present(coordinator, *, capture, revision, page, actor, nonce, expected):
    pages = note_pages(revision)
    if not 1 <= page <= len(pages):
        raise ProposalError("That note page is unavailable.")
    owner, (_, scope_hash) = coordinator.adapter.owner_scope(actor)
    with transaction.atomic():
        if page == 1:
            invalidate(capture.live_session_id)
            review = VoiceCaptureReview.objects.create(
                revision=revision,
                session_id=capture.live_session_id,
                scope_hash=scope_hash,
                target_version=capture.target_version,
                policy_version=POLICY,
                page_hashes=[digest(text) for text in pages],
                utterance_ids=[None] * len(pages),
            )
        else:
            review = (
                VoiceCaptureReview.objects
                .filter(revision=revision, session_id=capture.live_session_id, invalidated=False)
                .order_by("-created_at")
                .first()
            )
            if review is None:
                raise ProposalError("Start with read the whole note before choosing a page.")
        validate(review, owner)
        now = coordinator.now()
        decision = PendingDecision(
            decision_id=str(uuid4()),
            kind=DecisionKind.CAPTURE_REVIEW,
            source_id=str(review.pk),
            revision=revision.revision,
            state="presented",
            target_label=f"Closeout note revision {revision.revision}, page {page} of {len(pages)}",
            expires_at=now + timedelta(seconds=coordinator.max_armed_s),
            sequence=1,
            actor_user_pk=str(owner.pk),
            session_id=str(capture.live_session_id),
            thread_id=capture.live_session.thread_id,
            scope_hash=scope_hash,
            nonce=str(nonce),
            armed_at=now,
            source_content="read the whole note",
            sections=({"id": "note_page", "label": "Exact note page", "text": pages[page - 1]},),
            allowed_responses=("repeat", "read more", "no"),
            voice_eligible=False,
            voice_ineligible_reason="This only reads the note. Acceptance and handoff require separate reviews.",
            preview_hash=revision.content_hash,
            spoken_summary=pages[page - 1],
            executable={"adapter": "capture_review", "page": page, "review_id": str(review.pk)},
        )
        if not coordinator.store.install(decision, expected):
            raise DecisionConflict("The note review changed. Read the whole note again.")
    return DecisionReply(decision.spoken_summary, decision, "presented")


def read(coordinator, decision, actor, session_id):
    if decision.actor_user_pk != str(actor.user_pk) or decision.session_id != str(session_id):
        raise DecisionConflict("The session changed. Read the whole note again.")
    owner, _ = coordinator.adapter.owner_scope(actor)
    review = VoiceCaptureReview.objects.get(pk=decision.source_id)
    validate(review, owner)
    page = decision.executable["page"]
    if (
        decision.preview_hash != review.revision.content_hash
        or decision.spoken_summary != note_pages(review.revision)[page - 1]
    ):
        raise DecisionConflict("The note page changed. Read the whole note again.")
    return review


def bind(decision, utterance_id, spoken_text):
    """Persist exact page identity before provider dispatch; replacing a read resets its evidence."""
    with transaction.atomic():
        review = VoiceCaptureReview.objects.select_for_update().get(pk=decision.source_id)
        validate(review, review.revision.capture.owner)
        page = decision.executable["page"] - 1
        if (
            spoken_text != note_pages(review.revision)[page]
            or not VoiceUtterance.objects.filter(
                pk=utterance_id,
                session_id=decision.session_id,
                spoken_summary=spoken_text,
                spoken_summary_hash=review.page_hashes[page],
            ).exists()
        ):
            raise DecisionConflict("The exact persisted note page is unavailable.")
        review.utterance_ids[page] = str(utterance_id)
        review.save(update_fields=["utterance_ids"])


def resolve(
    coordinator, decision, content, *, actor, session_id, thread_id, nonce, context, **kwargs
):
    command = _normalize_command(content)
    if decision.state != "presented":
        return None
    coordinator.check_context(decision, context)
    if coordinator.now() >= decision.expires_at:
        return coordinator.disarm(thread_id, "expired")
    try:
        review = read(coordinator, decision, actor, session_id)
    except Exception:
        coordinator.disarm(thread_id, "source_invalidated")
        raise
    if command in ("repeat", "repeat that", "read it back"):
        return DecisionReply(decision.spoken_summary, decision, "review")
    if command in ("read more", "next page", "next", "continue"):
        page = decision.executable["page"] + 1
        if page > len(review.page_hashes):
            return DecisionReply(
                "End of note. Say accept this note to review acceptance; no handoff is submitted.",
                decision,
            )
        return present(
            coordinator,
            capture=review.revision.capture,
            revision=review.revision,
            page=page,
            actor=actor,
            nonce=nonce,
            expected=decision,
        )
    if re.fullmatch(
        r"read the whole note(?: page \d+)?|accept this note|hand\s*off this note", command
    ):
        # Keep the reviewed evidence, but never treat this page as an action.
        coordinator.advance(decision, state="set_aside")
        return None
    reply = coordinator.disarm(thread_id, "unrelated")
    from dataclasses import replace

    return replace(reply, route_normally=True)
