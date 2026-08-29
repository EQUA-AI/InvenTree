"""RBAC-safe voice proposals for the actions available to text chat."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_framework import ChatAgent
from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings
from ai.core.tools.capabilities import tool_name
from ai.core.tools.rbac import (
    action_tools,
    is_action_tool,
    permission_profile_for_user_pk,
    tool_requirement,
    tools_for_current_user,
)
from ai.core.voice.action_severity import (
    WriteSeverity,
    action_class_for_severity,
    confirm_phrase_for_tool_name,
    severity_for_tool_name,
)
from ai.core.voice.confirmation import (
    ProposedWriteAction,
    WriteActionClass,
    classify_write_intent,
)
from ai.core.voice.write_gate import (
    ExecutableWrite,
    PendingVoiceWriteStore,
    ResolvedVoiceWrite,
    StoredPendingWrite,
    VoiceWriteExecutionResult,
    VoiceWriteGate,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from ai.core.auth import AIPrincipal

logger = logging.getLogger(__name__)

_PLANNER_INSTRUCTIONS = """You convert a spoken action request into one tool call.
Use read tools when needed to resolve real InvenTree IDs. Never invent an ID, address,
quantity, status, or other required argument. When all required arguments are known,
call exactly one action tool. Action tools only capture a proposal; they do not execute.
Do not claim that an action completed. If required information is missing, call no action
tool and briefly identify what is missing."""

_SENSITIVE_ARGUMENTS = frozenset({"attachments", "body", "document_data"})

_ACTION_DOMAINS: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (
        re.compile(r"\b(?:email|mail|rfq|request for quote|pdf|document)\b", re.I),
        frozenset({"email"}),
    ),
    (
        re.compile(r"\b(?:kanban|card|board|task|work order)\b", re.I),
        frozenset({"kanban"}),
    ),
    (
        re.compile(r"\b(?:purchase order|supplier|vendor|procurement|company|po)\b", re.I),
        frozenset({"purchase_order"}),
    ),
    (
        re.compile(r"\b(?:sales order|customer|shipment|so)\b", re.I),
        frozenset({"sales_order"}),
    ),
    (
        re.compile(r"\b(?:stock|inventory|location|serial|quantity|on hand)\b", re.I),
        frozenset({"stock", "stock_location"}),
    ),
    (
        re.compile(r"\b(?:part|component|bom|bill of materials|category|parameter)\b", re.I),
        frozenset({"part", "part_category"}),
    ),
)


def text_chat_tools() -> tuple[Any, ...]:
    """Return the stable union of tools exposed by the text workflows.

    The direct-ORM kanban write tools no longer exist (S12 step 3): board
    mutations from every AI surface go through the governed proposal rail,
    so this union — and the voice gate catalog built from it — is
    structurally free of them.
    """
    from ai.core.integrations.attachment_corpus import ATTACHMENT_CORPUS_TOOLS
    from ai.core.integrations.controlled_document_corpus import (
        CONTROLLED_CORPUS_TOOLS,
    )
    from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS
    from ai.core.integrations.email.tools import EMAIL_TOOLS
    from ai.core.integrations.inventory_tools import INVENTORY_TOOLS
    from ai.core.integrations.kanban_tools import KANBAN_TOOLS
    from ai.core.integrations.media_corpus import EVIDENCE_MEDIA_TOOLS
    from ai.core.integrations.source_inventory_tools import SOURCE_INVENTORY_TOOLS
    from ai.core.tools.inventree.write.purchase_orders import (
        PURCHASE_ORDER_WRITE_TOOLS,
    )

    ordered = (
        *INVENTORY_TOOLS,
        *PURCHASE_ORDER_WRITE_TOOLS,
        *EMAIL_TOOLS,
        *KANBAN_TOOLS,
        *DOCUMENT_SEARCH_TOOLS,
        *CONTROLLED_CORPUS_TOOLS,
        *ATTACHMENT_CORPUS_TOOLS,
        *EVIDENCE_MEDIA_TOOLS,
        *SOURCE_INVENTORY_TOOLS,
    )
    unique: dict[str, Any] = {}
    for tool in ordered:
        name = tool_name(tool)
        existing = unique.get(name)
        if existing is not None and existing is not tool:
            raise RuntimeError(f"Conflicting text-chat tools share the name {name!r}")
        unique[name] = tool
    return tuple(unique.values())


def text_chat_action_tools() -> tuple[Any, ...]:
    """Return every mutating tool available through a text workflow."""
    return action_tools(text_chat_tools())


def capability_for_tool(tool: Any) -> str:
    """Encode the tool's existing text-chat RBAC requirement."""
    requirement = tool_requirement(tool)
    if requirement is None or requirement[1] == "view":
        raise ValueError(f"Action tool {tool_name(tool)!r} has no action RBAC mapping")
    return f"{requirement[0]}:{requirement[1]}"


def _requirement_from_capability(capability: str) -> tuple[str, str] | None:
    role, separator, permission = capability.partition(":")
    if not separator or not role or not permission:
        return None
    return role, permission


class TextToolRBACVoicePermission:
    """Freshly apply the same permission profile used to load text tools."""

    async def allows(self, actor: AIPrincipal, capability: str) -> bool:
        requirement = _requirement_from_capability(capability)
        if requirement is None:
            return False
        profile = await permission_profile_for_user_pk(actor.user_pk)
        return requirement in profile


class CachedPendingVoiceWriteStore(PendingVoiceWriteStore):
    """Share pending confirmations across workers through Django's cache."""

    def __init__(self, *, timeout_seconds: int = 15 * 60) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _key(thread_id: Any) -> str:
        return f"aimms:voice-action:{thread_id}"

    def save(self, thread_id: Any, stored: StoredPendingWrite) -> None:
        from django.core.cache import cache

        cache.set(self._key(thread_id), stored, timeout=self.timeout_seconds)

    def take(self, thread_id: Any) -> StoredPendingWrite | None:
        from django.core.cache import cache

        key = self._key(thread_id)
        lock_key = f"{key}:take"
        if not cache.add(lock_key, True, timeout=5):
            return None
        try:
            stored = cache.get(key)
            cache.delete(key)
            return stored if isinstance(stored, StoredPendingWrite) else None
        finally:
            cache.delete(lock_key)


@dataclass(frozen=True, slots=True)
class _CapturedAction:
    tool: Any
    arguments: dict[str, Any]


def _capture_proxy(tool: Any, captured: list[_CapturedAction]) -> Callable[..., Awaitable[dict]]:
    signature = inspect.signature(tool)

    @functools.wraps(tool)
    async def capture(*args: Any, **kwargs: Any) -> dict[str, bool]:  # noqa: RUF029 - wrapped tool contract is async
        bound = signature.bind(*args, **kwargs)
        arguments = dict(bound.arguments)
        json.dumps(arguments)
        captured.append(_CapturedAction(tool=tool, arguments=arguments))
        return {"proposal_captured": True, "executed": False}

    capture.__signature__ = signature  # type: ignore[attr-defined]
    return capture


def _display_value(key: str, value: Any) -> str | None:
    if value is None:
        return None
    if key in _SENSITIVE_ARGUMENTS:
        return "provided"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized[:64] + ("..." if len(normalized) > 64 else "")
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, (str, int, float)) for item in value[:3]):
            rendered = ", ".join(str(item) for item in value[:3])
            return rendered + (", and more" if len(value) > 3 else "")
        return f"{len(value)} items"
    if isinstance(value, dict):
        return "provided"
    return "provided"


#: Argument names that carry a record id, mapped to a resolver that turns the id
#: into something a technician can actually verify. A read-back naming only
#: "card id 127" cannot be checked before saying yes -- the speaker has no way to
#: tell a correct resolution from a wrong one, which is the whole point of
#: reading it back.
_RECORD_LABELERS: dict[str, tuple[str, str]] = {
    "card_id": ("ai.core.integrations.kanban_tools", "get_kanban_card"),
    "part_id": ("ai.core.tools.inventree.read.parts", "get_part"),
    "order_id": ("ai.core.tools.inventree.read.purchasing", "get_purchase_order"),
    "po_id": ("ai.core.tools.inventree.read.purchasing", "get_purchase_order"),
}
#: Fields to read a human label from, in preference order.
_LABEL_FIELDS = ("title", "name", "reference", "IPN", "description")


async def _record_label(key: str, value: Any) -> str | None:
    """Best-effort human label for one record id. Never raises, never blocks long."""
    target = _RECORD_LABELERS.get(key)
    if target is None or isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    module_path, function_name = target
    try:
        module = __import__(module_path, fromlist=[function_name])
        resolver = getattr(module, function_name, None)
        if resolver is None:
            return None
        record = resolver(**{key if key != "po_id" else "order_id": value})
        if inspect.isawaitable(record):
            record = await record
    except Exception:  # a missing label must never block the confirmation
        return None
    if not isinstance(record, dict):
        return None
    for field_name in _LABEL_FIELDS:
        label = record.get(field_name)
        if isinstance(label, str) and label.strip():
            return " ".join(label.split())[:64]
    return None


async def _action_summary_async(tool: Any, arguments: dict[str, Any]) -> str:
    """Read-back naming the record, falling back to the id-only form."""
    summary = _action_summary(tool, arguments)
    for key, value in arguments.items():
        label = await _record_label(str(key), value)
        if label:
            return f'{summary} ("{label}")'
    return summary


def _action_summary(tool: Any, arguments: dict[str, Any]) -> str:
    label = tool_name(tool).replace("_", " ")
    details: list[str] = []
    display_fields = getattr(tool, "_hitl_display_fields", None)
    keys = display_fields or tuple(arguments)
    for key in keys:
        if key not in arguments:
            continue
        rendered = _display_value(str(key), arguments[key])
        if rendered is not None:
            details.append(f"{str(key).replace('_', ' ')} {rendered}")
        if len(details) == 5:
            break
    summary = label.capitalize()
    if details:
        summary += " with " + ", ".join(details)
    return summary[:240].rstrip()


def _action_class(tool: Any, content: str) -> tuple[WriteActionClass, str]:
    """Confirmation bar for the action that will actually run.

    The resolved tool is the authority (``action_severity``); the utterance may
    only RAISE the bar, never lower it. Deriving the class from the transcript
    announced "this cannot be undone" for a reversible archive, and -- far worse
    -- left send_email/cancel_purchase_order/merge_stock on the lenient bare-yes
    bar whenever the request happened to avoid a destructive verb.
    """
    name = tool_name(tool).lower()
    severity = severity_for_tool_name(name)
    requirement = tool_requirement(tool)
    if requirement is not None and requirement[1] == "delete":
        severity = WriteSeverity.IRREVERSIBLE

    action_class = action_class_for_severity(severity)
    if (
        action_class is WriteActionClass.CONFIRMABLE
        and classify_write_intent(content, effect_intent=True) is WriteActionClass.IRREVERSIBLE
    ):
        # The speaker asked for something destructive. Even if the resolved tool
        # is reversible, hold them to the strict phrase rather than silently
        # accepting a bare "yes" for an action they may have mis-described.
        action_class = WriteActionClass.IRREVERSIBLE

    if action_class is not WriteActionClass.IRREVERSIBLE:
        return action_class, ""
    return action_class, confirm_phrase_for_tool_name(name)


def _action_candidates(content: str, actions: Sequence[Any]) -> tuple[Any, ...]:
    """Narrow actions to one high-confidence RBAC domain, or preserve all."""
    roles = next(
        (roles for pattern, roles in _ACTION_DOMAINS if pattern.search(content)),
        None,
    )
    if roles is None:
        return tuple(actions)
    selected = tuple(
        tool
        for tool in actions
        if (requirement := tool_requirement(tool)) is not None and requirement[0] in roles
    )
    return selected or tuple(actions)


def _related_reads(actions: Sequence[Any], reads: Sequence[Any]) -> tuple[Any, ...]:
    roles = {
        requirement[0] for tool in actions if (requirement := tool_requirement(tool)) is not None
    }
    selected = tuple(
        tool
        for tool in reads
        if (requirement := tool_requirement(tool)) is not None and requirement[0] in roles
    )
    return selected or tuple(reads)


class VoiceToolActionResolver:
    """Plan one authorized text-tool action without executing it."""

    def __init__(
        self,
        *,
        agent: Any | None = None,
        authorized_tool_loader: Callable[[], Awaitable[Sequence[Any]]] | None = None,
    ) -> None:
        self._agent = agent
        self._authorized_tool_loader = authorized_tool_loader

    async def _get_agent(self) -> Any:
        if self._agent is not None:
            return self._agent
        settings = get_settings()
        chat_client = AzureOpenAIChatClient(
            deployment_name=settings.azure_openai_fast_deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )
        invocation_config = getattr(chat_client, "function_invocation_config", None)
        if invocation_config is not None:
            # 3, not 8: this planner resolves ids for ONE tool call. Eight
            # iterations mostly bought retries of failing lookups (the live test
            # hit "Maximum consecutive function call errors reached (3)" after
            # ~26 s of them) on a path whose output is a fixed refusal string.
            invocation_config.max_iterations = 3
            invocation_config.include_detailed_errors = False
        self._agent = ChatAgent(
            chat_client=chat_client,
            instructions=_PLANNER_INSTRUCTIONS,
            name="Voice Action Planner",
            description="Plans one RBAC-authorized text-tool action for verbal confirmation",
        )
        return self._agent

    async def _authorized_tools(self) -> Sequence[Any]:
        if self._authorized_tool_loader is not None:
            return await self._authorized_tool_loader()
        return await tools_for_current_user(text_chat_tools())

    async def resolve(
        self,
        content: str,
        *,
        actor: AIPrincipal,
        trusted_context: Any,
    ) -> ResolvedVoiceWrite | None:
        from ai.core.auth import get_current_principal

        principal = get_current_principal()
        if principal is None or principal.user_pk != actor.user_pk:
            return None
        authorized = tuple(await self._authorized_tools())
        captured: list[_CapturedAction] = []
        all_actions = tuple(tool for tool in authorized if is_action_tool(tool))
        authorized_actions = _action_candidates(content, all_actions)
        if not authorized_actions:
            return None

        agent = await self._get_agent()
        # S12 (WP-B2): every binding pass is a real provider call the turn
        # ledger was blind to — voice turns run inside a bound ledger, so
        # recording here closes a documented uncounted-spend source.
        from ai.core.usage import maf_response_usage_metrics, record_usage

        try:
            action_proxies = [_capture_proxy(tool, captured) for tool in authorized_actions]
            response = await agent.run(content, tools=action_proxies)
            record_usage("voice_tool_actions", maf_response_usage_metrics(response))
            if not captured:
                authorized_reads = _related_reads(
                    authorized_actions,
                    [tool for tool in authorized if not is_action_tool(tool)],
                )
                if authorized_reads:
                    response = await agent.run(
                        content,
                        tools=[*authorized_reads, *action_proxies],
                    )
                    record_usage("voice_tool_actions", maf_response_usage_metrics(response))
            if not captured and authorized_actions != all_actions:
                # The domain shortlist can be wrong -- "Email the requested part
                # change" shortlists email when the action is create_part -- so
                # one widening pass over every authorized action is kept.
                all_action_proxies = [_capture_proxy(tool, captured) for tool in all_actions]
                response = await agent.run(content, tools=all_action_proxies)
                record_usage("voice_tool_actions", maf_response_usage_metrics(response))
            # The fourth pass (every action plus every read) is deliberately
            # gone. It was the most expensive rung and the least likely to bind
            # anything the previous three could not; together with four loops at
            # eight iterations it is how a refusal for "Order 50 more M3 screws"
            # took ~95 seconds to produce a constant, pre-decided string. If the
            # request is under-specified, saying so promptly is the better answer
            # -- and the caller now bounds this whole path with a timeout.
        except Exception as exc:
            logger.error("Voice action planning failed (error_type=%s)", type(exc).__name__)
            return None
        if len(captured) != 1:
            return None

        proposal = captured[0]
        capability = capability_for_tool(proposal.tool)
        action_class, confirm_phrase = _action_class(proposal.tool, content)
        return ResolvedVoiceWrite(
            action=ProposedWriteAction(
                capability=capability,
                summary=await _action_summary_async(proposal.tool, proposal.arguments),
                action_class=action_class,
                confirm_phrase=confirm_phrase,
            ),
            executable=ExecutableWrite(
                tool_name=tool_name(proposal.tool),
                capability=capability,
                arguments=proposal.arguments,
            ),
        )


class TextToolVoiceExecutor:
    """Execute one exact captured text tool after confirmation and reauthorization."""

    def __init__(
        self,
        *,
        tools: Sequence[Any] | None = None,
        permission: TextToolRBACVoicePermission | None = None,
    ) -> None:
        catalog = tools if tools is not None else text_chat_action_tools()
        self._tools = {tool_name(tool): tool for tool in catalog}
        self._permission = permission or TextToolRBACVoicePermission()

    async def execute(
        self,
        executable: ExecutableWrite,
        *,
        actor: AIPrincipal,
        trusted_context: Any,
    ) -> VoiceWriteExecutionResult:
        tool = self._tools.get(executable.tool_name)
        if tool is None:
            return VoiceWriteExecutionResult(ok=False, detail="unknown_tool")
        try:
            expected_capability = capability_for_tool(tool)
        except ValueError:
            return VoiceWriteExecutionResult(ok=False, detail="unmapped_tool")
        if expected_capability != executable.capability:
            return VoiceWriteExecutionResult(ok=False, detail="capability_mismatch")
        if not await self._permission.allows(actor, executable.capability):
            return VoiceWriteExecutionResult(ok=False, detail="not_authorized")

        try:
            inspect.signature(tool).bind(**executable.arguments)
            result = tool(**executable.arguments)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.error(
                "Confirmed voice action failed (tool=%s, error_type=%s)",
                executable.tool_name,
                type(exc).__name__,
            )
            return VoiceWriteExecutionResult(ok=False, detail="execution_failed")
        if isinstance(result, dict) and (
            result.get("success") is False or bool(result.get("error"))
        ):
            return VoiceWriteExecutionResult(ok=False, detail="tool_reported_failure")
        return VoiceWriteExecutionResult(ok=True, detail="executed")


_voice_write_gate: VoiceWriteGate | None = None


def get_voice_write_gate() -> VoiceWriteGate:
    """Return the process-wide confirmed-action gate used by voice turns."""
    global _voice_write_gate
    if _voice_write_gate is None:
        permission = TextToolRBACVoicePermission()
        _voice_write_gate = VoiceWriteGate(
            resolver=VoiceToolActionResolver(),
            permission=permission,
            executor=TextToolVoiceExecutor(permission=permission),
            store=CachedPendingVoiceWriteStore(),
        )
    return _voice_write_gate


__all__ = [
    "CachedPendingVoiceWriteStore",
    "TextToolRBACVoicePermission",
    "TextToolVoiceExecutor",
    "VoiceToolActionResolver",
    "capability_for_tool",
    "get_voice_write_gate",
    "text_chat_action_tools",
    "text_chat_tools",
]
