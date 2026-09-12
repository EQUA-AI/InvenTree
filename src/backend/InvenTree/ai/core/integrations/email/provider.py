"""
Email Provider Protocol

Abstract protocol for email operations.
Enables Gmail → M365 migration without changing agent code.
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from .contracts import (
    AccountConfig as AccountConfig,
)
from .contracts import (
    Capabilities as Capabilities,
)
from .contracts import (
    MailboxError as MailboxError,
)
from .contracts import (
    MailboxProvider as MailboxProvider,
)
from .contracts import (
    MessageChange as MessageChange,
)
from .contracts import (
    PreparedEmail as PreparedEmail,
)
from .contracts import (
    SendObservation as SendObservation,
)
from .contracts import (
    SyncPage as SyncPage,
)


@dataclass
class EmailAttachment:
    """Email attachment data."""

    attachment_id: str
    filename: str
    mime_type: str
    size: int
    data: bytes | None = None  # Loaded on demand


@dataclass
class EmailMessage:
    """Email message data."""

    message_id: str
    thread_id: str
    subject: str
    sender: str
    recipients: list[str]
    cc: list[str] = field(default_factory=list)
    date: datetime | None = None
    body_text: str = ""
    body_html: str = ""
    attachments: list[EmailAttachment] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    is_read: bool = False
    snippet: str = ""

    @property
    def has_attachments(self) -> bool:
        """Check if email has attachments."""
        return len(self.attachments) > 0


@dataclass
class EmailQuery:
    """Email search query parameters."""

    query: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    subject: str | None = None
    has_attachment: bool | None = None
    is_unread: bool | None = None
    after_date: datetime | None = None
    before_date: datetime | None = None
    label: str | None = None
    max_results: int = 25


@runtime_checkable
class EmailProvider(Protocol):
    """
    Protocol for email operations.

    Implementations:
    - GmailClient: Gmail API via Google service account
    - M365Client: Microsoft Graph API (future)

    All methods are async for non-blocking I/O.
    """

    @abstractmethod
    async def list_messages(
        self,
        query: EmailQuery | None = None,
    ) -> list[EmailMessage]:
        """
        List email messages matching query.

        Args:
            query: Search parameters. If None, lists recent emails.

        Returns:
            List of email messages (without full body/attachments).
        """
        ...

    @abstractmethod
    async def get_message(
        self,
        message_id: str,
        include_body: bool = True,
    ) -> EmailMessage | None:
        """
        Get a single email message.

        Args:
            message_id: The message ID.
            include_body: Whether to include the full body.

        Returns:
            EmailMessage or None if not found.
        """
        ...

    @abstractmethod
    async def get_attachment(
        self,
        message_id: str,
        attachment_id: str,
    ) -> EmailAttachment | None:
        """
        Get attachment data.

        Args:
            message_id: The message ID.
            attachment_id: The attachment ID.

        Returns:
            EmailAttachment with data loaded, or None.
        """
        ...

    @abstractmethod
    async def mark_as_read(self, message_id: str) -> bool:
        """
        Mark a message as read.

        Args:
            message_id: The message ID.

        Returns:
            True if successful.
        """
        ...

    @abstractmethod
    async def add_label(self, message_id: str, label: str) -> bool:
        """
        Add a label to a message.

        Args:
            message_id: The message ID.
            label: Label to add.

        Returns:
            True if successful.
        """
        ...

    @abstractmethod
    async def remove_label(self, message_id: str, label: str) -> bool:
        """
        Remove a label from a message.

        Args:
            message_id: The message ID.
            label: Label to remove.

        Returns:
            True if successful.
        """
        ...
