"""Shared per-user RBAC run helper for workflow agents (Phase 2).

Applies the wf8 list-filter pattern uniformly across workflows: agents are
built **tools-less** (so MAF cannot union in unfiltered constructor tools), and
each run receives only the tools the acting user's permissions allow. Voice
turns additionally start from the read-only subset (Tier-1 safety) when
``feature_voice_readonly_tools`` is on -- this closes the email/kanban-by-voice
gap in every workflow, not just wf8.

Execution-time middleware (CapabilityInvocationMiddleware) is intentionally not
applied here: the catalog currently covers only wf8's read tools, so extending
the middleware to wf2-wf6 needs a catalog overhaul (documented Phase 2
hardening follow-up). The per-run list filter is the enforcement boundary.
"""

from __future__ import annotations

from typing import Any


def modality_of(context: dict[str, Any] | None) -> str:
    """'voice' when the turn context marks it, else 'text'."""
    return "voice" if context and context.get("modality") == "voice" else "text"


def voice_read_tools() -> tuple:
    """Read-only tool surface for hands-free voice, shared with wf8."""
    from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
    from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS
    from ai.core.integrations.kanban_tools import KANBAN_READ_TOOLS

    return tuple(INVENTORY_READ_TOOLS + DOCUMENT_SEARCH_TOOLS + KANBAN_READ_TOOLS)


def rbac_base_tools(full_tools: Any, context: dict[str, Any] | None) -> tuple:
    """Base tool set before the per-user filter: read-only for voice, else full."""
    from ai.core.config import get_settings

    if modality_of(context) == "voice" and get_settings().feature_voice_readonly_tools:
        return voice_read_tools()
    return tuple(full_tools)


async def run_with_rbac(
    agent: Any,
    query: str,
    *,
    full_tools: Any,
    context: dict[str, Any] | None = None,
) -> Any:
    """Run a tools-less agent with only the tools the current user may use.

    ``full_tools`` is the workflow's complete toolset for text; voice turns are
    narrowed to the read-only surface first. Either way the per-user RBAC filter
    is applied before the tools reach the model.
    """
    from ai.core.tools.rbac import tools_for_current_user

    base = rbac_base_tools(full_tools, context)
    tools = await tools_for_current_user(base)
    return await agent.run(query, tools=tools)
