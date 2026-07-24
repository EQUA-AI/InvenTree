"""Compatibility helpers for the MAF (agent_framework) SDK.

The production container image may ship with different versions of the MAF SDK.
Some versions do not expose the `tool` decorator at `agent_framework.tool`.

This module provides a stable import surface (`ai_function`) and falls back to a
no-op decorator so the application can still start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def _noop_tool[F: Callable[..., Any]](func: F | None = None, **_: Any):
    def decorator(f: F) -> F:
        return f

    return decorator if func is None else func


def _resolve_tool() -> Callable[..., Any]:
    # Preferred (newer) location
    try:
        from agent_framework import tool as tool_impl  # type: ignore

        return tool_impl
    except Exception:
        pass

    # Alternative locations across versions
    for module_path in (
        "agent_framework.decorators",
        "agent_framework.tools",
        "agent_framework.tool",
        "agent_framework_core",
        "agent_framework_core.decorators",
        "agent_framework_core.tools",
        "agent_framework_core.tool",
    ):
        try:
            module = __import__(module_path, fromlist=["tool"])
            tool_impl = getattr(module, "tool", None)
            if callable(tool_impl):
                return tool_impl
        except Exception:
            continue

    return _noop_tool


ai_function = _resolve_tool()
