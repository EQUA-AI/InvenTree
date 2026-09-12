"""One MIME / Gmail command shared by tools and approval executors.

The caller owns the durable operation key. A provider exception after dispatch
is never described as not sent, and reconciliation only reads the Sent mailbox.
"""

from __future__ import annotations

import base64
import hashlib
import re
from email import policy
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser

from ai.core.integrations.email.gmail import get_gmail_client
from ai.core.integrations.email.policy import BLOCKED_ERROR, check_recipients, normalize_recipients


def message_reference(operation_id):
    """Return a safe deterministic RFC identifier, not a Gmail idempotency claim."""
    token = hashlib.sha256(str(operation_id).encode()).hexdigest()
    return f"<aimms.{token}@approvals.equa.work>"


def validate_message(payload):
    """Reject hidden recipients, header injection and unsupported content early."""
    if not isinstance(payload, dict):
        return ["Email payload must be an object"]
    if not check_recipients(payload.get("to"), payload.get("cc"), payload.get("bcc")).allowed:
        return [BLOCKED_ERROR]
    try:
        if not normalize_recipients(payload.get("to")):
            return ["At least one To recipient is required"]
        if payload.get("reply_to") and len(normalize_recipients(payload["reply_to"])) != 1:
            return ["Exactly one Reply-To address is required"]
    except ValueError as exc:
        return [str(exc)]
    if not isinstance(payload.get("subject"), str) or any(
        c in payload["subject"] for c in ("\r", "\n", "\x00")
    ):
        return ["Invalid subject header"]
    if not isinstance(payload.get("body"), str):
        return ["A plain-text email body is required"]
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        return ["Attachments must be a list"]
    for attachment in attachments:
        if not isinstance(attachment, dict) or not isinstance(attachment.get("data_bytes"), bytes):
            return ["Attachment content must be resolved before sending"]
        if (
            not isinstance(attachment.get("filename"), str)
            or not attachment["filename"]
            or any(c in attachment["filename"] for c in ("\r", "\n", "\x00"))
        ):
            return ["Invalid attachment filename"]
        mime_type = attachment.get("mime_type", "application/pdf")
        if not isinstance(mime_type, str) or not re.fullmatch(
            r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", mime_type
        ):
            return ["Invalid attachment content type"]
    return []


def require_sender(actor):
    """Rehydrate current capability; tool visibility is not execution permission."""
    from ai.core.integrations.email.authorization import require_email_permission

    return require_email_permission(actor, "send")


def _mime(payload, *, sender, operation_id):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["Subject"] = payload["subject"]
    msg["Message-ID"] = message_reference(operation_id)
    for field, header in (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("reply_to", "Reply-To")):
        recipients = normalize_recipients(payload.get(field))
        if recipients:
            msg[header] = ", ".join(recipients)
    msg.attach(MIMEText(payload["body"], "plain", "utf-8"))
    for attachment in payload.get("attachments") or []:
        subtype = attachment.get("mime_type", "application/pdf").split("/", 1)[-1]
        part = MIMEApplication(attachment["data_bytes"], _subtype=subtype)
        part.add_header("Content-Disposition", "attachment", filename=attachment["filename"])
        msg.attach(part)
    return msg


def send_message(payload, *, actor, operation_id):
    """Dispatch exactly once; failures after execute begins are uncertain."""
    reference = message_reference(operation_id)
    if not isinstance(payload, dict):
        return {
            "success": False,
            "outcome": "failed_before_effect",
            "error": "Invalid email payload; not sent.",
        }
    decision = check_recipients(payload.get("to"), payload.get("cc"), payload.get("bcc"))
    if not decision.allowed:
        return {
            "success": False,
            "outcome": "failed_before_effect",
            "error": BLOCKED_ERROR,
            "blocked_by_policy": True,
            "blocked_recipients": list(decision.blocked),
        }
    try:
        require_sender(actor)
        errors = validate_message(payload)
        if errors:
            raise ValueError("; ".join(errors))
        client = get_gmail_client()
        message = _mime(payload, sender=client.email, operation_id=operation_id)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        if len(raw) > 20 * 1024 * 1024:
            raise ValueError("Message exceeds the application send limit")
        request = client._get_service().users().messages().send(userId="me", body={"raw": raw})
    except Exception:
        return {
            "success": False,
            "outcome": "failed_before_effect",
            "error": "Email validation, permission or provider setup failed; not sent.",
            "rfc_message_id": reference,
        }
    try:
        response = request.execute(num_retries=0)
        if not all(
            isinstance(response.get(key), str) and response[key] for key in ("id", "threadId")
        ):
            raise ValueError("Provider receipt is incomplete")
        return {
            "success": True,
            "outcome": "succeeded",
            "message_id": response["id"],
            "thread_id": response["threadId"],
            "rfc_message_id": reference,
        }
    except Exception:
        return {
            "success": False,
            "outcome": "unknown",
            "rfc_message_id": reference,
            "error": "Email outcome is unverified; do not resend. Check its recorded status.",
        }


def reconcile_message(payload, *, operation_id):
    """Read-only exact identifier AND content verification; never send or mark read."""
    reference = message_reference(operation_id)
    unknown = {
        "success": False,
        "outcome": "unknown",
        "rfc_message_id": reference,
        "error": "Email outcome remains unverified; do not resend.",
    }
    try:
        client = get_gmail_client()
        messages = client._get_service().users().messages()
        hits = messages.list(
            userId="me", q=f"in:sent rfc822msgid:{reference[1:-1]}", maxResults=2
        ).execute(num_retries=0)
        rows = hits.get("messages") or []
        if len(rows) != 1 or hits.get("nextPageToken"):
            return unknown
        row = messages.get(userId="me", id=rows[0]["id"], format="raw").execute(num_retries=0)
        msg = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(row["raw"] + "=" * (-len(row["raw"]) % 4))
        )
        if any(
            len(msg.get_all(header, [])) > 1
            for header in ("From", "To", "Cc", "Bcc", "Reply-To", "Subject", "Message-ID")
        ):
            return unknown
        if (
            "SENT" not in row.get("labelIds", [])
            or str(msg.get("Message-ID", "")) != reference
            or str(msg.get("Subject", "")) != payload["subject"]
            or normalize_recipients(str(msg.get("From", ""))) != normalize_recipients(client.email)
        ):
            return unknown
        for field, header in (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("reply_to", "Reply-To")):
            if normalize_recipients(str(msg.get(header, ""))) != normalize_recipients(
                payload.get(field)
            ):
                return unknown
        body = msg.get_body(preferencelist=("plain",))
        if body is None or body.get_content().replace("\r\n", "\n") != payload["body"].replace(
            "\r\n", "\n"
        ):
            return unknown
        # Do not claim an attachment-bearing effect from text/header evidence.
        if (
            payload.get("attachments")
            or list(msg.iter_attachments())
            or msg.get_body(preferencelist=("html",)) is not None
        ):
            return unknown
        if not row.get("id") or not row.get("threadId"):
            return unknown
        return {
            "success": True,
            "outcome": "succeeded",
            "message_id": row["id"],
            "thread_id": row["threadId"],
            "rfc_message_id": reference,
        }
    except Exception:
        return unknown
