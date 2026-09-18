"""Prepare owner memory actions; model tools never confirm or mutate facts."""

from typing import Any, Literal

from ai.core.maf_compat import ai_function
from ai.core.tools.read_only import guard_write_tool
from asgiref.sync import sync_to_async


@ai_function
@guard_write_tool
async def propose_memory_action(
    action: Literal["remember", "update", "forget", "forget_all"],
    request_key: str,
    memory_fact_id: str | None = None,
    expected_version: int | None = None,
    topics: list[str] | None = None,
    replacement_fact_id: str | None = None,
) -> dict[str, Any]:
    """Prepare one action for exact visual review on the owner's memory page.

    Use only an existing memory/suggestion ID and version supplied by the server
    or user; never invent them. New prose cannot create a fact here: eligible
    conversation extraction must first produce a screened suggestion. Keep one
    stable request_key per requested action. This tool does not execute memory
    changes or accept spoken/model confirmation. Tell the user to open the
    returned review path, read the exact action and confirm there. Forget-all
    accepts no fact/version/topics and requires the page's exact confirmation.
    """
    from ai.core.auth import get_current_principal

    principal = get_current_principal()

    def prepare():
        from aichat.services import memory_commands, proposals
        from django.contrib.auth import get_user_model
        from InvenTree.helpers import pui_url

        owner = (
            get_user_model()
            .objects.filter(pk=principal.user_pk if principal else None, is_active=True)
            .first()
        )
        if owner is None:
            return {"success": False, "error": "memory_action_unavailable"}
        intent = {
            name: value
            for name, value in {
                "memory_fact_id": memory_fact_id,
                "expected_version": expected_version,
                "topics": topics,
                "replacement_fact_id": replacement_fact_id,
            }.items()
            if value is not None
        }
        try:
            proposal = memory_commands.prepare_action(
                owner, action_type=f"memory.{action}", idempotency_key=request_key, intent=intent
            )
            if proposal.state != "proposed":
                return {"success": False, "error": "memory_action_already_processed"}
            # No remembered text/client coordinates enter the model transcript.
            return {
                "success": True,
                "status": "awaiting_review",
                "executed": False,
                "proposal_id": str(proposal.pk),
                "review_path": pui_url(f"/memory/?proposal={proposal.pk}"),
            }
        except (proposals.ProposalError, ValueError, TypeError):
            return {"success": False, "error": "memory_action_unavailable"}

    return await sync_to_async(prepare, thread_sensitive=True)()


MEMORY_ACTION_TOOLS = [propose_memory_action]
