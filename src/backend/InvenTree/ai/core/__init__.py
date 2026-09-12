"""Lazy public exports; optional integrations load only when requested."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.core.config import (
        get_azure_openai_settings,
        get_devui_settings,
        get_gmail_settings,
        get_inventree_settings,
        get_settings,
    )
    from ai.core.events import (
        AGUIEvent,
        EventType,
        create_run_context,
        get_event_emitter,
    )
    from ai.core.middleware import (
        ErrorCategory,
        ReflectionFunctionMiddleware,
        get_reflection_middleware,
    )

__version__ = "2.3.0"
__author__ = "AIMMS Team"

__all__ = [
    "AGUIEvent",
    "ErrorCategory",
    "EventType",
    "ReflectionFunctionMiddleware",
    "__version__",
    "create_run_context",
    "get_azure_openai_settings",
    "get_devui_settings",
    "get_event_emitter",
    "get_gmail_settings",
    "get_inventree_settings",
    "get_reflection_middleware",
    "get_settings",
]

_EXPORTS = {  # noqa: RUF067 - Lazy re-export map keeps optional providers unloaded.
    "get_azure_openai_settings": ("ai.core.config", "get_azure_openai_settings"),
    "get_devui_settings": ("ai.core.config", "get_devui_settings"),
    "get_gmail_settings": ("ai.core.config", "get_gmail_settings"),
    "get_inventree_settings": ("ai.core.config", "get_inventree_settings"),
    "get_settings": ("ai.core.config", "get_settings"),
    "AGUIEvent": ("ai.core.events", "AGUIEvent"),
    "EventType": ("ai.core.events", "EventType"),
    "create_run_context": ("ai.core.events", "create_run_context"),
    "get_event_emitter": ("ai.core.events", "get_event_emitter"),
    "ErrorCategory": ("ai.core.middleware", "ErrorCategory"),
    "ReflectionFunctionMiddleware": ("ai.core.middleware", "ReflectionFunctionMiddleware"),
    "get_reflection_middleware": ("ai.core.middleware", "get_reflection_middleware"),
}


def __getattr__(name):
    """Resolve a public export without eagerly importing optional providers."""
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
