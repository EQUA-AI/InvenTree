"""The shared replay renderer (moved verbatim from wf8 ``_run_input``, PR C)."""

from __future__ import annotations

from typing import Any

from agent_framework import ChatMessage, Role, TextContent


def replay_messages(query: str, context: dict[str, Any] | None) -> Any:
    """The agent input: the bare query, or the replayed transcript + query.

    MAF accepts a message list, so prior turns are replayed as real messages
    instead of being flattened into the prompt. Only user/assistant rows
    replay — the transcript model also admits system/tool rows, and
    replaying one as user speech would let machine output masquerade as
    the human. Blank entries are skipped. Byte-for-byte the wf8 ``_run_input``
    behaviour (parity pinned by ``test_maf_adapter``).
    """
    history = (context or {}).get("conversation_history")
    if not isinstance(history, list) or not history:
        return query

    messages: list[ChatMessage] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        role_name = str(entry.get("role"))
        if role_name not in ("user", "assistant"):
            continue
        role = Role.ASSISTANT if role_name == "assistant" else Role.USER
        messages.append(ChatMessage(role=role, contents=[TextContent(text=content)]))
    if not messages:
        return query

    messages.append(ChatMessage(role=Role.USER, contents=[TextContent(text=query)]))
    return messages


def memory_block_message(bundle: Any) -> ChatMessage | None:
    """The builder's USER-role memory block as one SDK message (or None)."""
    block = bundle.memory_block() if bundle is not None else None
    if not block:
        return None
    return ChatMessage(role=Role.USER, contents=[TextContent(text=str(block["content"]))])


__all__ = ["memory_block_message", "replay_messages"]
