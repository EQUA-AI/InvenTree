"""Prepare typed evidence in the existing assistant; a tool cannot publish it."""

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from ai.core.maf_compat import ai_function
from ai.core.tools.read_only import guard_write_tool
from asgiref.sync import sync_to_async

_source: ContextVar[tuple[str, str] | None] = ContextVar("investigation_source", default=None)


@contextmanager
def bind_investigation_source(thread_id: str, turn_id: str):
    """Only the normalized turn executor supplies the source, never the model."""
    token = _source.set((thread_id, turn_id))
    try:
        yield
    finally:
        _source.reset(token)


@ai_function
@guard_write_tool
async def propose_investigation_artifact(
    kind: Literal["observation", "reading_set", "photo", "report", "laboratory", "check_result"],
    payload: dict[str, Any],
    request_key: str,
    file_id: str | None = None,
    supersedes_id: int | None = None,
) -> dict[str, Any]:
    """Propose this turn's evidence for the exact acknowledged investigation request.

    Use only technician-supplied facts. Preserve reading name/value/unit, observed
    time/conditions and laboratory sample/method/time/source/status/results; ask
    for missing facts. Unknown values stay null. Never infer healthy or complete.
    A photo/report needs an exact file_id uploaded on this turn. PNG/JPEG/PDF
    publication is supported; other files produce a non-executable review saying
    publication is unsupported. A correction names the saved artifact it replaces.
    Use one stable request_key on retries. This only prepares a review in Recent
    action proposals on the existing chat Approvals tab: tell the technician to review the exact
    payload and file disclosure there, then approve or dismiss. Nothing is shared
    until that authenticated visual approval. Do not claim the request resolved.
    """
    from ai.core.auth import get_current_principal

    principal = get_current_principal()
    bound = _source.get()
    if principal is None or bound is None or not request_key.strip() or len(request_key) > 90:
        return {
            "success": False,
            "executed": False,
            "detail": "Request-focused source unavailable. Nothing was saved.",
        }

    def prepare():
        from aichat.models import ChatTurn
        from aichat.services import proposals
        from aichat.services.scope_strings import scope_strings
        from aichat.services.work_focus import WORK_FOCUS_SNAPSHOT_KEY
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.filter(pk=principal.user_pk, is_active=True).first()
        turn = ChatTurn.objects.filter(pk=bound[1], thread_id=bound[0], thread__owner=owner).first()
        snapshot = (turn.trusted_context or {}).get(WORK_FOCUS_SNAPSHOT_KEY) if turn else None
        if owner is None or not isinstance(snapshot, dict) or snapshot.get("schema_version") != 2:
            return {
                "success": False,
                "executed": False,
                "detail": "Request focus unavailable. Nothing was saved.",
            }
        intent = {
            "request_id": snapshot["request_id"],
            "request_version": snapshot["request_version"],
            "kind": kind,
            "payload": payload,
        }
        if file_id is not None:
            intent["file_id"] = file_id
        if supersedes_id is not None:
            intent["supersedes_id"] = supersedes_id
        try:
            scope_key, scope_hash = scope_strings(owner)
            proposal = proposals.create_proposal(
                owner=owner,
                scope_key=scope_key,
                scope_hash=scope_hash,
                action_type="investigation.artifact",
                work_order_id=snapshot["work_order_id"],
                reason="Review technician evidence for this investigation request.",
                idempotency_key="investigation:"
                + hashlib.sha256(f"{bound[1]}:{request_key}".encode()).hexdigest(),
                policy_version="investigation-publication-v1",
                intent=intent,
                thread_id=bound[0],
                source_turn_id=bound[1],
                correlation_id=turn.correlation_id,
            )
            return {
                "success": True,
                "executed": False,
                "status": proposal.state,
                "proposal_id": str(proposal.pk),
                "request_id": snapshot["request_id"],
                "publication": proposal.preview["publication"],
                "review": "Review Recent action proposals on the chat Approvals tab. Nothing is shared until you approve.",
            }
        except proposals.ProposalError as exc:
            return {"success": False, "executed": False, "error": exc.code, "detail": str(exc)}

    return await sync_to_async(prepare, thread_sensitive=True)()


INVESTIGATION_PROPOSAL_TOOLS = [propose_investigation_artifact]
