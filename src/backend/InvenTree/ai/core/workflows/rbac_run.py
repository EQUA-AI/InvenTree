"""Shared per-user RBAC run helper for workflow agents (Phase 2).

Applies the wf8 list-filter pattern uniformly across workflows: agents are
built **tools-less** (so MAF cannot union in unfiltered constructor tools), and
each run receives only the tools the acting user's permissions allow. Voice
turns additionally start from the read-only subset (Tier-1 safety) when
``feature_voice_readonly_tools`` is on -- this closes the email/kanban-by-voice
gap in every workflow, not just wf8.

Since S11 this is also the single place a capability run is bound: the catalog
covers every workflow's toolset, so ``run_with_rbac`` wraps each run in
``bind_capability_run`` and the agents carry ``CapabilityInvocationMiddleware``.
Binding here rather than at each of the eleven call sites means a new workflow
cannot forget it — an unbound run is denied by the guard (``missing_run_context``),
not silently unenforced.
"""

from __future__ import annotations

from typing import Any


def modality_of(context: dict[str, Any] | None) -> str:
    """'voice' when the turn context marks it, else 'text'."""
    return "voice" if context and context.get("modality") == "voice" else "text"


def voice_read_tools(full_tools: Any | None = None) -> tuple:
    """Read projection of the workflow's text tools for direct voice execution."""
    from ai.core.tools.rbac import read_tools

    if full_tools is None:
        from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
        from ai.core.integrations.email.tools import EMAIL_TOOLS
        from ai.core.integrations.inventory_tools import INVENTORY_READ_TOOLS
        from ai.core.integrations.kanban_tools import KANBAN_TOOLS

        full_tools = (
            *INVENTORY_READ_TOOLS,
            *EMAIL_TOOLS,
            *KANBAN_TOOLS,
            *DOCUMENT_SEARCH_TOOLS,
        )
    return read_tools(tuple(full_tools))


def rbac_base_tools(full_tools: Any, context: dict[str, Any] | None) -> tuple:
    """Base tool set before RBAC: workflow reads for voice, full set for text."""
    from ai.core.config import get_settings

    if modality_of(context) == "voice" and get_settings().feature_voice_readonly_tools:
        return voice_read_tools(full_tools)
    return tuple(full_tools)


async def run_with_rbac(
    agent: Any,
    query: str,
    *,
    workflow: str,
    full_tools: Any,
    context: dict[str, Any] | None = None,
) -> Any:
    """Run a tools-less agent with only the tools the current user may use.

    ``full_tools`` is the workflow's complete toolset for text; voice turns are
    narrowed to the read-only surface first. Either way the per-user RBAC filter
    is applied before the tools reach the model, and the run is bound so the
    invocation guard can re-authorize each call against ``workflow``.
    """
    from ai.core.tools.invocation_guard import bind_capability_run
    from ai.core.tools.rbac import tools_for_current_user

    base = rbac_base_tools(full_tools, context)
    tools = await tools_for_current_user(base)
    with bind_capability_run(
        workflow=workflow, modality=modality_of(context), selected_tools=tools
    ):
        return await agent.run(query, tools=tools)
