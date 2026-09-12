"""Lazy public exports; optional integrations load only when requested."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.core.integrations.email.gmail import (
        GmailClient,
        GmailError,
        get_gmail_client,
    )
    from ai.core.integrations.email.gmail_query import (
        build_gmail_query,
    )
    from ai.core.integrations.email.provider import (
        EmailAttachment,
        EmailMessage,
        EmailProvider,
        EmailQuery,
    )
    from ai.core.integrations.email.tools import (
        EMAIL_TOOLS,
        download_attachment,
        generate_and_send_document,
        get_email_details,
        list_emails,
        mark_email_processed,
        send_email,
    )

__all__ = [
    "EMAIL_TOOLS",
    "EmailAttachment",
    "EmailMessage",
    "EmailProvider",
    "EmailQuery",
    "GmailClient",
    "GmailError",
    "build_gmail_query",
    "download_attachment",
    "generate_and_send_document",
    "get_email_details",
    "get_gmail_client",
    "list_emails",
    "mark_email_processed",
    "send_email",
]

_EXPORTS = {  # noqa: RUF067 - Lazy re-export map keeps optional providers unloaded.
    "GmailClient": ("ai.core.integrations.email.gmail", "GmailClient"),
    "GmailError": ("ai.core.integrations.email.gmail", "GmailError"),
    "get_gmail_client": ("ai.core.integrations.email.gmail", "get_gmail_client"),
    "EmailAttachment": ("ai.core.integrations.email.provider", "EmailAttachment"),
    "EmailMessage": ("ai.core.integrations.email.provider", "EmailMessage"),
    "EmailProvider": ("ai.core.integrations.email.provider", "EmailProvider"),
    "EmailQuery": ("ai.core.integrations.email.provider", "EmailQuery"),
    "build_gmail_query": ("ai.core.integrations.email.gmail_query", "build_gmail_query"),
    "EMAIL_TOOLS": ("ai.core.integrations.email.tools", "EMAIL_TOOLS"),
    "download_attachment": ("ai.core.integrations.email.tools", "download_attachment"),
    "generate_and_send_document": (
        "ai.core.integrations.email.tools",
        "generate_and_send_document",
    ),
    "get_email_details": ("ai.core.integrations.email.tools", "get_email_details"),
    "list_emails": ("ai.core.integrations.email.tools", "list_emails"),
    "mark_email_processed": ("ai.core.integrations.email.tools", "mark_email_processed"),
    "send_email": ("ai.core.integrations.email.tools", "send_email"),
}


def __getattr__(name):
    """Resolve a public export without eagerly importing optional providers."""
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
