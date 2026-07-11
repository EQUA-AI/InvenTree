"""
Gmail Client Implementation

Gmail API client implementing the EmailProvider protocol.
Uses Google service account for authentication.
"""

import base64
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import json

import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ai.core.config import get_gmail_settings
from ai.core.integrations.email.provider import (
    EmailAttachment,
    EmailMessage,
    EmailQuery,
    build_gmail_query,
)

logger = structlog.get_logger(__name__)


class GmailError(Exception):
    """Gmail API error."""
    
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GmailClient:
    """
    Gmail API client using service account authentication.
    
    Implements the EmailProvider protocol for email operations.
    
    Configuration:
    - Service account JSON file path: GOOGLE_SERVICE_ACCOUNT_PATH
    - Email address to impersonate: GMAIL_EMAIL
    - Scopes: GMAIL_SCOPES
    
    Example usage:
        ```python
        client = get_gmail_client()
        
        # List unread emails with attachments
        messages = await client.list_messages(
            EmailQuery(is_unread=True, has_attachment=True)
        )
        
        # Get full message with body
        message = await client.get_message(messages[0].message_id)
        
        # Download attachment
        attachment = await client.get_attachment(
            message.message_id,
            message.attachments[0].attachment_id,
        )
        ```
    """
    
    def __init__(
        self,
        service_account_path: str | None = None,
        email: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        """
        Initialize the Gmail client.
        
        Args:
            service_account_path: Path to service account JSON file.
            email: Email address to impersonate.
            scopes: OAuth scopes for Gmail API.
        """
        config = get_gmail_settings()
        
        self.service_account_path = service_account_path or str(config.service_account_path)
        self._service_account_json: str | None = config.service_account_json
        self.email = email or config.email
        self.scopes = scopes or config.scopes
        
        self._service: Any = None
        
        logger.info(
            "GmailClient initialized",
            email=self.email,
            has_json_creds=bool(self._service_account_json),
            scopes=self.scopes,
        )
    
    def _get_service(self) -> Any:
        """Get or create the Gmail API service."""
        if self._service is None:
            # Prefer inline JSON (from env var) — avoids needing a file on disk
            if self._service_account_json:
                info = json.loads(self._service_account_json)
                credentials = service_account.Credentials.from_service_account_info(
                    info,
                    scopes=self.scopes,
                )
            else:
                credentials = service_account.Credentials.from_service_account_file(
                    self.service_account_path,
                    scopes=self.scopes,
                )
            
            # Impersonate the target email
            delegated_credentials = credentials.with_subject(self.email)
            
            self._service = build(
                "gmail",
                "v1",
                credentials=delegated_credentials,
                cache_discovery=False,
            )
        
        return self._service
    
    async def list_messages(
        self,
        query: EmailQuery | None = None,
    ) -> list[EmailMessage]:
        """
        List email messages matching query.
        
        Args:
            query: Search parameters. If None, lists recent emails.
            
        Returns:
            List of email messages (metadata only, no body/attachments).
        """
        try:
            service = self._get_service()
            query = query or EmailQuery()
            
            gmail_query = build_gmail_query(query)
            
            # List message IDs
            result = service.users().messages().list(
                userId="me",
                q=gmail_query if gmail_query else None,
                maxResults=query.max_results,
            ).execute()
            
            messages = result.get("messages", [])
            
            # Fetch metadata for each message
            email_messages: list[EmailMessage] = []
            for msg in messages:
                message_data = service.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
                ).execute()
                
                email_msg = self._parse_message(message_data, include_body=False)
                email_messages.append(email_msg)
            
            logger.info(
                "Listed Gmail messages",
                count=len(email_messages),
                query=gmail_query,
            )
            
            return email_messages
            
        except HttpError as e:
            logger.error("Gmail list error", error=str(e), status=e.resp.status)
            raise GmailError(f"Failed to list messages: {e}", e.resp.status) from e
        except Exception as e:
            logger.exception("Gmail list error")
            raise GmailError(f"Unexpected error: {e}") from e
    
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
        try:
            service = self._get_service()
            
            message_format = "full" if include_body else "metadata"
            
            message_data = service.users().messages().get(
                userId="me",
                id=message_id,
                format=message_format,
            ).execute()
            
            email_msg = self._parse_message(message_data, include_body=include_body)
            
            logger.debug("Retrieved Gmail message", message_id=message_id)
            
            return email_msg
            
        except HttpError as e:
            if e.resp.status == 404:
                return None
            logger.error("Gmail get error", error=str(e), status=e.resp.status)
            raise GmailError(f"Failed to get message: {e}", e.resp.status) from e
        except Exception as e:
            logger.exception("Gmail get error")
            raise GmailError(f"Unexpected error: {e}") from e
    
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
        try:
            service = self._get_service()
            
            attachment_data = service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ).execute()
            
            data = base64.urlsafe_b64decode(attachment_data["data"])
            
            # Get attachment metadata from message
            message = await self.get_message(message_id, include_body=False)
            if message is None:
                return None
            
            attachment_meta = None
            for att in message.attachments:
                if att.attachment_id == attachment_id:
                    attachment_meta = att
                    break
            
            if attachment_meta is None:
                return None
            
            logger.debug(
                "Retrieved Gmail attachment",
                message_id=message_id,
                attachment_id=attachment_id,
                size=len(data),
            )
            
            return EmailAttachment(
                attachment_id=attachment_id,
                filename=attachment_meta.filename,
                mime_type=attachment_meta.mime_type,
                size=len(data),
                data=data,
            )
            
        except HttpError as e:
            if e.resp.status == 404:
                return None
            logger.error("Gmail attachment error", error=str(e))
            raise GmailError(f"Failed to get attachment: {e}", e.resp.status) from e
        except Exception as e:
            logger.exception("Gmail attachment error")
            raise GmailError(f"Unexpected error: {e}") from e
    
    async def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read."""
        try:
            service = self._get_service()
            
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            
            logger.debug("Marked message as read", message_id=message_id)
            return True
            
        except HttpError as e:
            logger.error("Gmail modify error", error=str(e))
            return False
    
    async def add_label(self, message_id: str, label: str) -> bool:
        """Add a label to a message."""
        try:
            service = self._get_service()
            
            # First, ensure label exists (or get its ID)
            label_id = await self._get_or_create_label(label)
            
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": [label_id]},
            ).execute()
            
            logger.debug("Added label to message", message_id=message_id, label=label)
            return True
            
        except HttpError as e:
            logger.error("Gmail add label error", error=str(e))
            return False
    
    async def remove_label(self, message_id: str, label: str) -> bool:
        """Remove a label from a message."""
        try:
            service = self._get_service()
            
            label_id = await self._get_label_id(label)
            if label_id is None:
                return True  # Label doesn't exist, nothing to remove
            
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": [label_id]},
            ).execute()
            
            logger.debug("Removed label from message", message_id=message_id, label=label)
            return True
            
        except HttpError as e:
            logger.error("Gmail remove label error", error=str(e))
            return False
    
    def _parse_message(
        self,
        message_data: dict[str, Any],
        include_body: bool = True,
    ) -> EmailMessage:
        """Parse Gmail API message response into EmailMessage."""
        headers = {
            h["name"].lower(): h["value"]
            for h in message_data.get("payload", {}).get("headers", [])
        }
        
        # Parse date
        date_str = headers.get("date")
        date = None
        if date_str:
            try:
                date = parsedate_to_datetime(date_str)
            except Exception:
                pass
        
        # Parse recipients
        to_str = headers.get("to", "")
        recipients = [r.strip() for r in to_str.split(",") if r.strip()]
        
        cc_str = headers.get("cc", "")
        cc = [c.strip() for c in cc_str.split(",") if c.strip()]
        
        # Parse body
        body_text = ""
        body_html = ""
        
        if include_body:
            body_text, body_html = self._extract_body(message_data.get("payload", {}))
        
        # Parse attachments
        attachments = self._extract_attachments(message_data.get("payload", {}))
        
        # Parse labels
        label_ids = message_data.get("labelIds", [])
        is_read = "UNREAD" not in label_ids
        
        return EmailMessage(
            message_id=message_data["id"],
            thread_id=message_data.get("threadId", ""),
            subject=headers.get("subject", "(No Subject)"),
            sender=headers.get("from", ""),
            recipients=recipients,
            cc=cc,
            date=date,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            labels=label_ids,
            is_read=is_read,
            snippet=message_data.get("snippet", ""),
        )
    
    def _extract_body(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str]:
        """Extract text and HTML body from message payload."""
        text_body = ""
        html_body = ""
        
        mime_type = payload.get("mimeType", "")
        
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                text_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        
        elif mime_type == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                html_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        
        elif mime_type.startswith("multipart/"):
            for part in payload.get("parts", []):
                part_text, part_html = self._extract_body(part)
                if part_text and not text_body:
                    text_body = part_text
                if part_html and not html_body:
                    html_body = part_html
        
        return text_body, html_body
    
    def _extract_attachments(
        self,
        payload: dict[str, Any],
    ) -> list[EmailAttachment]:
        """Extract attachment metadata from message payload."""
        attachments: list[EmailAttachment] = []
        
        # Check this part for attachment
        filename = payload.get("filename", "")
        body = payload.get("body", {})
        attachment_id = body.get("attachmentId")
        
        if filename and attachment_id:
            attachments.append(EmailAttachment(
                attachment_id=attachment_id,
                filename=filename,
                mime_type=payload.get("mimeType", "application/octet-stream"),
                size=body.get("size", 0),
            ))
        
        # Recurse into parts
        for part in payload.get("parts", []):
            attachments.extend(self._extract_attachments(part))
        
        return attachments
    
    async def _get_label_id(self, label_name: str) -> str | None:
        """Get label ID by name."""
        try:
            service = self._get_service()
            
            result = service.users().labels().list(userId="me").execute()
            
            for label in result.get("labels", []):
                if label["name"].lower() == label_name.lower():
                    return label["id"]
            
            return None
            
        except HttpError:
            return None
    
    async def _get_or_create_label(self, label_name: str) -> str:
        """Get existing label ID or create new label."""
        existing = await self._get_label_id(label_name)
        if existing:
            return existing
        
        # Create new label
        service = self._get_service()
        
        label = service.users().labels().create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        ).execute()
        
        return label["id"]


# Module-level singleton
_gmail_client: GmailClient | None = None


def get_gmail_client() -> GmailClient:
    """Get the singleton Gmail client instance."""
    global _gmail_client
    if _gmail_client is None:
        _gmail_client = GmailClient()
    return _gmail_client
