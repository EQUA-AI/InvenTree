"""Read-only reconciliation of the acting user's recorded voice actions."""

from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async


@ai_function
async def get_last_action_status(thread_id: str, operation_id: str | None = None) -> dict:
    """Read the verified result of your last action in this chat thread.

    Supply the current thread ID, and optionally a recorded operation ID. Never
    retry or re-execute an action because a result is pending or unavailable.
    This tool cannot select, arm, confirm or dispatch an action.
    """
    from ai.core.auth import get_current_principal
    from ai.core.decisions.receipts import lookup_operation, spoken_receipt
    from ai.core.tools.rbac import permission_profile_for_user_pk

    actor = get_current_principal()
    if actor is None:
        return {"available": False, "message": "Authentication is required."}
    if ("work_order", "view") not in await permission_profile_for_user_pk(actor.user_pk):
        return {"available": False, "message": "Action history access is unavailable."}
    result = await sync_to_async(lookup_operation)(
        actor=actor, thread_id=thread_id, operation_id=operation_id
    )
    return {"available": result is not None, "result": result, "message": spoken_receipt(result)}
