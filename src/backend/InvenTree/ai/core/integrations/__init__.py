"""Lazy public exports; optional integrations load only when requested."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.core.integrations.data_provider import (
        DemoDataProviderAsync,
        LiveDataProviderAsync,
        data_provider,
        get_data_provider,
        get_mode_status,
        is_demo_mode,
        reset_provider,
    )
    from ai.core.integrations.demo_dataset import (
        DemoDatasetProvider,
        get_demo_provider,
    )
    from ai.core.integrations.email import (
        EMAIL_TOOLS,
        EmailAttachment,
        EmailMessage,
        EmailProvider,
        EmailQuery,
        GmailClient,
        GmailError,
        get_gmail_client,
    )
    from ai.core.integrations.inventory_tools import (
        INVENTORY_TOOLS,
    )
    from ai.core.integrations.inventree import (
        INVENTREE_TOOLS,
        BusinessRuleError,
        InvenTreeClient,
        InvenTreeError,
        TransientError,
        ValidationError,
        get_inventree_client,
        inventree_client,
    )

__all__ = [
    "EMAIL_TOOLS",
    "INVENTORY_TOOLS",
    "INVENTREE_TOOLS",
    "BusinessRuleError",
    "DemoDataProviderAsync",
    "DemoDatasetProvider",
    "EmailAttachment",
    "EmailMessage",
    "EmailProvider",
    "EmailQuery",
    "GmailClient",
    "GmailError",
    "InvenTreeClient",
    "InvenTreeError",
    "LiveDataProviderAsync",
    "TransientError",
    "ValidationError",
    "data_provider",
    "get_data_provider",
    "get_demo_provider",
    "get_gmail_client",
    "get_inventree_client",
    "get_mode_status",
    "inventree_client",
    "is_demo_mode",
    "reset_provider",
]

_EXPORTS = {  # noqa: RUF067 - Lazy re-export map keeps optional providers unloaded.
    "DemoDataProviderAsync": ("ai.core.integrations.data_provider", "DemoDataProviderAsync"),
    "LiveDataProviderAsync": ("ai.core.integrations.data_provider", "LiveDataProviderAsync"),
    "data_provider": ("ai.core.integrations.data_provider", "data_provider"),
    "get_data_provider": ("ai.core.integrations.data_provider", "get_data_provider"),
    "get_mode_status": ("ai.core.integrations.data_provider", "get_mode_status"),
    "is_demo_mode": ("ai.core.integrations.data_provider", "is_demo_mode"),
    "reset_provider": ("ai.core.integrations.data_provider", "reset_provider"),
    "DemoDatasetProvider": ("ai.core.integrations.demo_dataset", "DemoDatasetProvider"),
    "get_demo_provider": ("ai.core.integrations.demo_dataset", "get_demo_provider"),
    "EMAIL_TOOLS": ("ai.core.integrations.email", "EMAIL_TOOLS"),
    "EmailAttachment": ("ai.core.integrations.email", "EmailAttachment"),
    "EmailMessage": ("ai.core.integrations.email", "EmailMessage"),
    "EmailProvider": ("ai.core.integrations.email", "EmailProvider"),
    "EmailQuery": ("ai.core.integrations.email", "EmailQuery"),
    "GmailClient": ("ai.core.integrations.email", "GmailClient"),
    "GmailError": ("ai.core.integrations.email", "GmailError"),
    "get_gmail_client": ("ai.core.integrations.email", "get_gmail_client"),
    "INVENTORY_TOOLS": ("ai.core.integrations.inventory_tools", "INVENTORY_TOOLS"),
    "INVENTREE_TOOLS": ("ai.core.integrations.inventree", "INVENTREE_TOOLS"),
    "BusinessRuleError": ("ai.core.integrations.inventree", "BusinessRuleError"),
    "InvenTreeClient": ("ai.core.integrations.inventree", "InvenTreeClient"),
    "InvenTreeError": ("ai.core.integrations.inventree", "InvenTreeError"),
    "TransientError": ("ai.core.integrations.inventree", "TransientError"),
    "ValidationError": ("ai.core.integrations.inventree", "ValidationError"),
    "get_inventree_client": ("ai.core.integrations.inventree", "get_inventree_client"),
    "inventree_client": ("ai.core.integrations.inventree", "inventree_client"),
}


def __getattr__(name):
    """Resolve a public export without eagerly importing optional providers."""
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
