"""
Email Integration Module

Provides email client implementations:
- EmailProvider: Abstract protocol for email operations
- GmailClient: Gmail API implementation for parts@equa.work
- Supports future M365 migration via provider abstraction
"""

from ai.core.integrations.email.gmail import (
    GmailClient,
    GmailError,
    get_gmail_client,
)
from ai.core.integrations.email.provider import (
    EmailAttachment,
    EmailMessage,
    EmailProvider,
    EmailQuery,
    build_gmail_query,
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
    # Provider protocol
    "EmailProvider",
    "EmailMessage",
    "EmailAttachment",
    "EmailQuery",
    "build_gmail_query",
    # Gmail client
    "GmailClient",
    "GmailError",
    "get_gmail_client",
    # Tools
    "EMAIL_TOOLS",
    "list_emails",
    "get_email_details",
    "download_attachment",
    "mark_email_processed",
    "send_email",
    "generate_and_send_document",
]
