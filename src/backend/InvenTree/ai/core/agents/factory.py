"""The agent factory: the ONE ``ChatAgent`` constructor site (M1 PR A, plan §9.3).

Twenty-six rails built their own ``AzureOpenAIChatClient`` + ``ChatAgent``
pair. They now describe the agent with an :class:`AgentSpec` and call
:func:`build_agent`, which is the only place ``ChatAgent(`` appears outside
``ai/core/memory/maf_adapter`` (``test_agent_factory`` pins that by AST).

Double-replay invariants (plan §9.4 / GR-19): ``context_providers`` and
``chat_message_store_factory`` are ALWAYS passed explicitly — the memory
seam attaches through ``AgentSpec.context_providers`` later, never through
an SDK default that could start replaying history on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_framework import ChatAgent
from ai.core.config import get_settings
from ai.core.integrations.azure_openai_client import build_chat_client

#: The only rail allowed a constructor toolset: wf1's legacy diagnostic
#: agents pre-date run_with_rbac and keep their static tools (S11 carve-out).
CONSTRUCTOR_TOOLS_WORKFLOWS = frozenset({"wf1"})

#: The prompt-cache key modes wf8 distinguishes (plan §9.9 / GR-33).
PROMPT_CACHE_MODES = ("default", "voice", "read", "clarify", "workflow")


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Everything a rail needs to say about the agent it wants."""

    deployment: str
    instructions: str
    name: str
    description: str = ""
    #: ``CapabilityInvocationMiddleware()`` on every catalogued tool rail.
    middleware: Any = None
    max_tool_iterations: int | None = None
    include_detailed_errors: bool | None = None
    #: Catalogue id (wf1..wf9, routing, reflection, voice) — for the AST
    #: tests and the constructor-toolset carve-out, never for routing.
    workflow: str = ""
    #: Static tools unioned into every run (wf1 only — see the frozenset).
    constructor_tools: tuple[Any, ...] = ()
    #: MAF context providers (the memory seam's pin adapter, M1 PR C+).
    context_providers: tuple[Any, ...] = ()


def build_agent(spec: AgentSpec) -> ChatAgent:
    """Build the agent ``spec`` describes; the sole ``ChatAgent(`` site."""
    if spec.constructor_tools and spec.workflow not in CONSTRUCTOR_TOOLS_WORKFLOWS:
        raise ValueError(
            f"{spec.workflow or 'unnamed rail'}: a constructor toolset bypasses "
            "run_with_rbac; only wf1's legacy agents may carry one"
        )
    client = build_chat_client(
        spec.deployment,
        max_iterations=spec.max_tool_iterations,
        include_detailed_errors=spec.include_detailed_errors,
    )
    return ChatAgent(
        chat_client=client,
        instructions=spec.instructions,
        name=spec.name,
        description=spec.description or None,
        middleware=spec.middleware,
        tools=list(spec.constructor_tools) or None,
        context_providers=list(spec.context_providers) or None,
        chat_message_store_factory=None,
    )


def prompt_cache_key_deployments() -> frozenset[str]:
    """Deployments the cache key may ride on (dark until probe (i) passes)."""
    raw = getattr(get_settings(), "aimms_prompt_cache_key_deployments", "") or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def prompt_cache_options(
    deployment: str, *, client_code: str, thread_id: str, mode: str
) -> dict[str, Any]:
    """``additional_chat_options`` carrying the cache key, or ``{}`` (plan §9.9).

    The key is ``{client_code}:{thread_id}:{mode}`` — one cache lineage per
    thread and prompt variant, never shared across tenants. Only for
    deployments listed in ``AIMMS_PROMPT_CACHE_KEY_DEPLOYMENTS`` (an
    unsupported parameter would 400 the turn), and only with a thread id.
    """
    if mode not in PROMPT_CACHE_MODES:
        raise ValueError(f"unknown prompt cache mode {mode!r}")
    if not thread_id or not deployment or deployment not in prompt_cache_key_deployments():
        return {}
    options: dict[str, Any] = {"prompt_cache_key": f"{client_code or 'site'}:{thread_id}:{mode}"}
    retention = prompt_cache_retention()
    if retention:
        # Extended retention rides only where the key rides: the same
        # deployment list gates both (both verified live on 2024-10-21).
        options["prompt_cache_retention"] = retention
    return options


def prompt_cache_retention() -> str:
    """``AIMMS_PROMPT_CACHE_RETENTION`` ("" = provider default, else in_memory/24h)."""
    return str(getattr(get_settings(), "aimms_prompt_cache_retention", "") or "").strip()


__all__ = [
    "CONSTRUCTOR_TOOLS_WORKFLOWS",
    "PROMPT_CACHE_MODES",
    "AgentSpec",
    "build_agent",
    "prompt_cache_key_deployments",
    "prompt_cache_options",
    "prompt_cache_retention",
]
