"""Compatibility facade: the new coordinator is the only execution authority."""

from ai.core.decisions.adapters.registry import action_spec
from ai.core.decisions.resolver import CompositeVoiceActionResolver, WorkOrderIntent
from ai.core.voice.write_gate import WriteProposalResult


class DecisionVoiceWriteGate:
    """Keep legacy service seams while refusing tool writes without a governed adapter."""

    def __init__(self, store):
        self.store = store
        self.resolver = CompositeVoiceActionResolver()

    async def begin(self, content, *, actor, trusted_context, thread_id, nonce):
        """Resolve for explanation only; proposal installation belongs to the coordinator."""
        resolved = await self.resolver.resolve(
            content, actor=actor, trusted_context=trusted_context
        )
        if resolved is None:
            return None
        action = (
            resolved.action
            if isinstance(resolved, WorkOrderIntent)
            else resolved.executable.tool_name
        )
        spec = action_spec(action)
        spoken = (
            "Say the exact work order reference and the hold reason."
            if spec.available
            else spec.unavailable_reason
        )
        return WriteProposalResult(spoken=spoken, awaiting_confirmation=False, audit_events=())

    async def resolve_pending(self, *args, **kwargs):
        """Never consume or execute a legacy pending slot while decisions are enabled."""
        return None
