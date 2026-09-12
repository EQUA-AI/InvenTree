"""Tool adapter boundary, unavailable until a complete action spec is registered."""

from ai.core.decisions.adapters.registry import action_spec
from ai.core.decisions.receipts import finish_operation, start_operation


class ToolAdapter:
    """Persist-before-dispatch and retain unknown results for reconciliation."""

    async def execute(self, decision, *, executor, executable, actor, trusted_context):
        """Never enable an unmapped tool simply because a callable exists."""
        from ai.core.tools.read_only import confirmed_write_exception
        from asgiref.sync import sync_to_async

        spec = action_spec(executable.tool_name)
        if not spec.available or executable.tool_name == "work_order.hold":
            raise ValueError("No governed tool adapter is available for this action.")
        operation, created = await sync_to_async(start_operation)(decision)
        if not created:
            return operation
        try:
            with confirmed_write_exception():
                result = await executor.execute(
                    executable, actor=actor, trusted_context=trusted_context
                )
        except Exception:
            await sync_to_async(finish_operation)(operation, state="unknown")
            raise
        return await sync_to_async(finish_operation)(
            operation, state=str(result.resolved_outcome), detail=result.detail
        )
