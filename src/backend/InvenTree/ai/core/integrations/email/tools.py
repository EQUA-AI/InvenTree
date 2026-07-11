"""
Email AI-Function Tools

@ai_function decorated tools for MAF agents to interact with email.
Uses the EmailProvider abstraction for flexibility.

Includes:
- list_emails / get_email_details / download_attachment / mark_email_processed
- send_email (Gmail API, MIME multipart, supports attachments)
- generate_and_send_document (PDF generation + email in one call)
"""

import base64 as _b64_mod
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

from ai.core.integrations.email.gmail import GmailError, get_gmail_client
from ai.core.integrations.email.provider import EmailQuery
from ai.core.maf_compat import ai_function

logger = structlog.get_logger(__name__)


@ai_function
async def list_emails(
    is_unread: bool | None = None,
    has_attachment: bool | None = None,
    from_address: str | None = None,
    subject_contains: str | None = None,
    after_date: str | None = None,
    max_results: int = 25,
) -> dict[str, Any]:
    """
    List emails from the parts inbox (parts@equa.work).
    
    Use this tool to search for and list emails, especially those with
    attachments like invoices, purchase orders, or technical documents.
    
    Args:
        is_unread: Filter for unread messages only. Set to True for new emails.
        has_attachment: Filter for emails with attachments. Set to True for
                       documents that need processing.
        from_address: Filter by sender email address.
                     Example: "supplier@example.com"
        subject_contains: Filter by text in subject line.
                         Example: "Invoice" or "PO-"
        after_date: Only return emails after this date (YYYY-MM-DD format).
                   Example: "2024-01-15"
        max_results: Maximum number of emails to return (default 25).
    
    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - emails: list - List of emails:
            - message_id: Unique email ID (use for get_email_details)
            - subject: Email subject line
            - sender: Sender email address
            - date: Date/time received
            - snippet: Preview of email content
            - is_read: Whether email has been read
            - has_attachments: Whether email has attachments
            - attachment_count: Number of attachments
        - count: int - Number of emails returned
        - error: str - Error message if query failed
    
    Example:
        >>> result = await list_emails(is_unread=True, has_attachment=True)
        >>> for email in result["emails"]:
        >>>     print(f"{email['sender']}: {email['subject']}")
    """
    try:
        client = get_gmail_client()
        
        # Build query
        query = EmailQuery(
            is_unread=is_unread,
            has_attachment=has_attachment,
            from_address=from_address,
            subject=subject_contains,
            max_results=max_results,
        )
        
        if after_date:
            try:
                query.after_date = datetime.strptime(after_date, "%Y-%m-%d")
            except ValueError:
                return {
                    "success": False,
                    "emails": [],
                    "count": 0,
                    "error": f"Invalid date format: {after_date}. Use YYYY-MM-DD.",
                    "error_type": "VALIDATION",
                }
        
        messages = await client.list_messages(query)
        
        simplified = [
            {
                "message_id": msg.message_id,
                "thread_id": msg.thread_id,
                "subject": msg.subject,
                "sender": msg.sender,
                "date": msg.date.isoformat() if msg.date else None,
                "snippet": msg.snippet[:200],
                "is_read": msg.is_read,
                "has_attachments": msg.has_attachments,
                "attachment_count": len(msg.attachments),
            }
            for msg in messages
        ]
        
        logger.info("Listed emails", count=len(simplified))
        
        return {
            "success": True,
            "emails": simplified,
            "count": len(simplified),
        }
        
    except GmailError as e:
        logger.error("Email list failed", error=str(e))
        return {
            "success": False,
            "emails": [],
            "count": 0,
            "error": str(e),
            "error_type": "TRANSIENT_INFRA" if e.status_code and e.status_code >= 500 else "BUSINESS_RULE",
        }
    except Exception as e:
        logger.exception("Unexpected error listing emails")
        return {
            "success": False,
            "emails": [],
            "count": 0,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


@ai_function
async def get_email_details(
    message_id: str,
    include_body: bool = True,
) -> dict[str, Any]:
    """
    Get full details of a specific email including body and attachments.
    
    Use this tool when you need the complete content of an email,
    including the full body text and list of attachments to process.
    
    Args:
        message_id: The message ID from list_emails result.
        include_body: Whether to include the full email body (default True).
    
    Returns:
        A dictionary containing:
        - success: bool - Whether the query succeeded
        - email: dict - Full email details:
            - message_id: Unique email ID
            - thread_id: Conversation thread ID
            - subject: Email subject
            - sender: Sender email address
            - recipients: List of recipient addresses
            - cc: List of CC addresses
            - date: Date/time received
            - body_text: Plain text body content
            - body_html: HTML body content (if available)
            - attachments: List of attachments:
                - attachment_id: ID for downloading
                - filename: Attachment filename
                - mime_type: MIME type
                - size: Size in bytes
            - is_read: Whether email has been read
        - error: str - Error message if query failed
    
    Example:
        >>> result = await get_email_details("abc123")
        >>> if result["success"]:
        >>>     print(result["email"]["body_text"])
        >>>     for att in result["email"]["attachments"]:
        >>>         print(f"Attachment: {att['filename']}")
    """
    try:
        client = get_gmail_client()
        
        message = await client.get_message(message_id, include_body=include_body)
        
        if message is None:
            return {
                "success": False,
                "email": None,
                "error": f"Email not found: {message_id}",
                "error_type": "BUSINESS_RULE",
            }
        
        email_data = {
            "message_id": message.message_id,
            "thread_id": message.thread_id,
            "subject": message.subject,
            "sender": message.sender,
            "recipients": message.recipients,
            "cc": message.cc,
            "date": message.date.isoformat() if message.date else None,
            "body_text": message.body_text,
            "body_html": message.body_html[:5000] if message.body_html else "",  # Limit HTML size
            "attachments": [
                {
                    "attachment_id": att.attachment_id,
                    "filename": att.filename,
                    "mime_type": att.mime_type,
                    "size": att.size,
                }
                for att in message.attachments
            ],
            "is_read": message.is_read,
        }
        
        logger.info(
            "Retrieved email details",
            message_id=message_id,
            attachment_count=len(message.attachments),
        )
        
        return {
            "success": True,
            "email": email_data,
        }
        
    except GmailError as e:
        logger.error("Email get failed", error=str(e))
        return {
            "success": False,
            "email": None,
            "error": str(e),
            "error_type": "TRANSIENT_INFRA" if e.status_code and e.status_code >= 500 else "BUSINESS_RULE",
        }
    except Exception as e:
        logger.exception("Unexpected error getting email")
        return {
            "success": False,
            "email": None,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


@ai_function
async def download_attachment(
    message_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """
    Download an email attachment.
    
    Use this tool to get the actual content of an attachment for processing.
    The data is returned as base64 encoded for transport.
    
    Args:
        message_id: The message ID containing the attachment.
        attachment_id: The attachment ID from get_email_details result.
    
    Returns:
        A dictionary containing:
        - success: bool - Whether the download succeeded
        - attachment: dict - Attachment details:
            - attachment_id: The attachment ID
            - filename: Original filename
            - mime_type: MIME type of the file
            - size: Size in bytes
            - data_base64: Base64 encoded file content
        - error: str - Error message if download failed
    
    Example:
        >>> result = await download_attachment("msg123", "att456")
        >>> if result["success"]:
        >>>     import base64
        >>>     data = base64.b64decode(result["attachment"]["data_base64"])
        >>>     with open(result["attachment"]["filename"], "wb") as f:
        >>>         f.write(data)
    """
    try:
        import base64 as b64
        
        client = get_gmail_client()
        
        attachment = await client.get_attachment(message_id, attachment_id)
        
        if attachment is None:
            return {
                "success": False,
                "attachment": None,
                "error": f"Attachment not found: {attachment_id}",
                "error_type": "BUSINESS_RULE",
            }
        
        # Encode data for transport
        data_base64 = b64.b64encode(attachment.data or b"").decode("utf-8")
        
        logger.info(
            "Downloaded attachment",
            message_id=message_id,
            attachment_id=attachment_id,
            filename=attachment.filename,
            size=attachment.size,
        )
        
        return {
            "success": True,
            "attachment": {
                "attachment_id": attachment.attachment_id,
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "size": attachment.size,
                "data_base64": data_base64,
            },
        }
        
    except GmailError as e:
        logger.error("Attachment download failed", error=str(e))
        return {
            "success": False,
            "attachment": None,
            "error": str(e),
            "error_type": "TRANSIENT_INFRA" if e.status_code and e.status_code >= 500 else "BUSINESS_RULE",
        }
    except Exception as e:
        logger.exception("Unexpected error downloading attachment")
        return {
            "success": False,
            "attachment": None,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


@ai_function
async def mark_email_processed(
    message_id: str,
    add_label: str = "AIMMS-Processed",
) -> dict[str, Any]:
    """
    Mark an email as processed by AIMMS.
    
    Use this tool after successfully processing an email and its attachments.
    Marks the email as read and adds a label for tracking.
    
    Args:
        message_id: The message ID to mark as processed.
        add_label: Label to add (default "AIMMS-Processed").
    
    Returns:
        A dictionary containing:
        - success: bool - Whether the operation succeeded
        - error: str - Error message if operation failed
    
    Example:
        >>> result = await mark_email_processed("msg123")
        >>> if result["success"]:
        >>>     print("Email marked as processed")
    """
    try:
        client = get_gmail_client()
        
        # Mark as read
        await client.mark_as_read(message_id)
        
        # Add processed label
        await client.add_label(message_id, add_label)
        
        logger.info(
            "Marked email as processed",
            message_id=message_id,
            label=add_label,
        )
        
        return {"success": True}
        
    except GmailError as e:
        logger.error("Mark processed failed", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": "TRANSIENT_INFRA" if e.status_code and e.status_code >= 500 else "BUSINESS_RULE",
        }
    except Exception as e:
        logger.exception("Unexpected error marking email")
        return {
            "success": False,
            "error": f"Unexpected error: {e}",
            "error_type": "UNKNOWN",
        }


# ---------------------------------------------------------------------------
# New tools: send_email  &  generate_and_send_document
# ---------------------------------------------------------------------------

@ai_function
async def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    reply_to: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Send an email via the Gmail API (uses the configured service account).

    Supports plain-text body and one or more file attachments
    (typically PDFs generated by the PDF service).

    Args:
        to: Recipient email address(es).
        subject: Email subject line.
        body: Plain-text email body.
        cc: CC addresses (optional).
        bcc: BCC addresses (optional).
        reply_to: Reply-To header (optional).
        attachments: List of attachment dicts, each containing:
            - filename: str   (e.g. ``"SO-0042.pdf"``)
            - data_bytes: bytes  (raw file bytes)
            - mime_type: str  (default ``"application/pdf"``)

    Returns:
        A dictionary containing:
        - success: bool
        - message_id: str (Gmail message id on success)
        - error: str (on failure)
    """
    try:
        client = get_gmail_client()
        service = client._get_service()

        # ── Build MIME message ──────────────────────────────────────
        msg = MIMEMultipart()
        msg["To"] = ", ".join(to) if isinstance(to, list) else to
        msg["From"] = client.email  # impersonated user
        msg["Subject"] = subject

        if cc:
            msg["Cc"] = ", ".join(cc) if isinstance(cc, list) else cc
        if bcc:
            msg["Bcc"] = ", ".join(bcc) if isinstance(bcc, list) else bcc
        if reply_to:
            msg["Reply-To"] = reply_to

        msg.attach(MIMEText(body, "plain"))

        # ── Attach files ────────────────────────────────────────────
        for att in attachments or []:
            mime = att.get("mime_type", "application/pdf")
            _maintype, subtype = mime.split("/", 1)
            part = MIMEApplication(att["data_bytes"], _subtype=subtype)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=att["filename"],
            )
            msg.attach(part)

        # ── Send via Gmail API ──────────────────────────────────────
        raw = _b64_mod.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        result = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )

        logger.info("Email sent", message_id=result["id"], to=to)
        return {"success": True, "message_id": result["id"]}

    except GmailError as e:
        logger.error("Send email failed (GmailError)", error=str(e))
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("Send email failed")
        return {"success": False, "error": f"Unexpected error: {e}"}


def _build_sample_data(document_type: str) -> dict[str, Any]:
    """Return realistic sample data for a given document type."""
    from datetime import date, timedelta

    today = date.today()

    _sample_lines = [
        {"part_name": "Widget A",  "part_ipn": "WDG-001", "description": "Standard widget",       "quantity": 10, "unit_price": 25.50,  "total_price": 255.00},
        {"part_name": "Gizmo B",   "part_ipn": "GZM-002", "description": "Premium gizmo",          "quantity": 5,  "unit_price": 42.00,  "total_price": 210.00},
        {"part_name": "Bolt M8x20", "part_ipn": "BLT-008", "description": "Hex head bolt, M8×20",  "quantity": 100,"unit_price": 0.35,   "total_price": 35.00},
        {"part_name": "Sensor X",  "part_ipn": "SNS-010", "description": "Temperature sensor 0-200°C","quantity": 3, "unit_price": 89.99, "total_price": 269.97},
    ]

    if document_type == "purchase_order":
        return {
            "reference": "PO-2026-0042",
            "status": "Issued",
            "issue_date": today.isoformat(),
            "target_date": (today + timedelta(days=30)).isoformat(),
            "supplier_name": "Acme Industrial Supply",
            "supplier_address": "123 Factory Rd, Houston TX 77001",
            "supplier_email": "orders@acme-industrial.example",
            "currency_symbol": "$",
            "lines": _sample_lines,
            "subtotal": 769.97,
            "tax_rate": "8.25%",
            "tax": 63.52,
            "total_price": 833.49,
            "payment_terms": "Net 30 from date of invoice.",
            "company_name": "AIMMS / Equa AI",
        }

    if document_type == "sales_order":
        return {
            "reference": "SO-2026-0107",
            "status": "In Progress",
            "issue_date": today.isoformat(),
            "target_date": (today + timedelta(days=14)).isoformat(),
            "customer_name": "Greenfield Engineering Ltd.",
            "customer_address": "456 Innovation Blvd, Austin TX 78701",
            "customer_email": "purchasing@greenfield.example",
            "currency_symbol": "$",
            "lines": _sample_lines,
            "subtotal": 769.97,
            "tax_rate": "8.25%",
            "tax": 63.52,
            "total_price": 833.49,
            "terms": "Payment due within 14 days of delivery.",
            "company_name": "AIMMS / Equa AI",
        }

    if document_type == "bom":
        bom_lines = [
            {"part_name": "Widget A",   "part_ipn": "WDG-001", "reference": "R1,R2",  "quantity": 2, "units": "pcs", "optional": False},
            {"part_name": "Gizmo B",    "part_ipn": "GZM-002", "reference": "U1",     "quantity": 1, "units": "pcs", "optional": False},
            {"part_name": "Bolt M8x20", "part_ipn": "BLT-008", "reference": "",       "quantity": 8, "units": "pcs", "optional": False},
            {"part_name": "Sensor X",   "part_ipn": "SNS-010", "reference": "J1",     "quantity": 1, "units": "pcs", "optional": True},
            {"part_name": "PCB Rev-C",  "part_ipn": "PCB-003", "reference": "",       "quantity": 1, "units": "pcs", "optional": False},
        ]
        return {
            "part_name": "Controller Assembly v2",
            "part_ipn": "ASSY-100",
            "revision": "C",
            "build_quantity": 1,
            "description": "Main controller assembly including sensors and fasteners.",
            "lines": bom_lines,
            "company_name": "AIMMS / Equa AI",
        }

    if document_type == "quote":
        return {
            "reference": "QT-2026-0019",
            "status": "Open",
            "issue_date": today.isoformat(),
            "valid_until": (today + timedelta(days=60)).isoformat(),
            "customer_name": "Greenfield Engineering Ltd.",
            "contact_address": "456 Innovation Blvd, Austin TX 78701",
            "contact_email": "rfq@greenfield.example",
            "currency_symbol": "$",
            "lines": _sample_lines,
            "subtotal": 769.97,
            "tax_rate": "8.25%",
            "tax": 63.52,
            "total_price": 833.49,
            "terms": "Quote valid for 60 days. Prices FOB Houston.",
            "acceptance_block": True,
            "company_name": "AIMMS / Equa AI",
        }

    if document_type == "rfq":
        rfq_lines = [
            {"part_name": "Widget A",   "part_ipn": "WDG-001", "description": "Standard widget",             "quantity": 50,  "target_price": 22.00, "your_price": None, "lead_time": "", "total_price": None},
            {"part_name": "Gizmo B",    "part_ipn": "GZM-002", "description": "Premium gizmo",               "quantity": 20,  "target_price": 38.00, "your_price": None, "lead_time": "", "total_price": None},
            {"part_name": "Sensor X",   "part_ipn": "SNS-010", "description": "Temperature sensor 0-200°C",  "quantity": 10,  "target_price": 80.00, "your_price": None, "lead_time": "", "total_price": None},
        ]
        return {
            "reference": "RFQ-2026-0008",
            "status": "Open",
            "issue_date": today.isoformat(),
            "response_due": (today + timedelta(days=14)).isoformat(),
            "supplier_name": "Acme Industrial Supply",
            "supplier_address": "123 Factory Rd, Houston TX 77001",
            "supplier_email": "sales@acme-industrial.example",
            "buyer_name": "AIMMS Procurement",
            "buyer_email": "procurement@equa.work",
            "currency_symbol": "$",
            "show_target_price": True,
            "lines": rfq_lines,
            "total_price": None,
            "delivery_location": "AIMMS Warehouse, 789 Commerce Dr, Austin TX 78701",
            "requirements": "All items must meet ISO 9001 certification. Material test certificates required.",
            "terms": "Payment Net 30 from date of invoice. FOB destination.",
            "instructions": "Please return completed pricing by the Response Due date. Email your quote to procurement@equa.work.",
            "company_name": "AIMMS / Equa AI",
        }

    if document_type == "work_order":
        wo_items = [
            {"name": "Disassemble unit",   "description": "Remove casing and inspect internals", "quantity": 1, "unit_price": 75.00,  "total": 75.00},
            {"name": "Replace Sensor X",   "description": "Swap faulty temperature sensor",     "quantity": 1, "unit_price": 89.99,  "total": 89.99},
            {"name": "Calibrate & test",   "description": "Full calibration cycle + QA test",    "quantity": 1, "unit_price": 120.00, "total": 120.00},
        ]
        return {
            "reference": "WO-2026-0005",
            "status": "Pending",
            "priority": "High",
            "created_date": today.isoformat(),
            "due_date": (today + timedelta(days=7)).isoformat(),
            "job_number": "JOB-8821",
            "assigned_to": "Tech Team Alpha",
            "description": "Service and repair of Controller Assembly v2 — replace faulty sensor and recalibrate.",
            "line_items": wo_items,
            "labor_total": 195.00,
            "materials_total": 89.99,
            "subtotal": 284.99,
            "tax": 23.51,
            "total": 308.50,
            "currency_symbol": "$",
            "customer": {
                "name": "Greenfield Engineering Ltd.",
                "address": "456 Innovation Blvd",
                "city": "Austin",
                "state": "TX",
                "postal_code": "78701",
            },
            "company_name": "AIMMS / Equa AI",
        }

    # Fallback — minimal data so the template won't crash
    return {"reference": f"SAMPLE-{document_type.upper()}", "company_name": "AIMMS / Equa AI"}


@ai_function
async def generate_and_send_document(
    document_type: str,
    to: str | list[str],
    document_data: dict[str, Any] | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
) -> dict[str, Any]:
    """
    Generate a standardised PDF and email it as an attachment.

    This is a compound tool that:
      1. Renders a Jinja2 HTML template with *document_data*,
      2. Converts the HTML to PDF via WeasyPrint, and
      3. Sends the PDF as an attachment via :func:`send_email`.

    Args:
        document_type: One of ``"sales_order"``, ``"purchase_order"``,
                       ``"bom"``, ``"quote"``, ``"rfq"``, ``"work_order"``.
        to: Recipient email address(es).
        document_data: Optional data dict for the template.  If omitted,
                       realistic sample data is generated automatically.
                       Key shapes vary by type (see
                       ``ai.core.pdf.service.TEMPLATE_MAP``).
        subject: Email subject (auto-generated from *document_type* + reference
                 if omitted).
        body: Email body text (auto-generated if omitted).
        cc: CC addresses.
        bcc: BCC addresses.

    Returns:
        dict with ``success``, ``message_id``, ``filename``, ``pdf_size_kb``.
    """
    from ai.core.pdf.service import TEMPLATE_MAP, get_pdf_service

    if document_type not in TEMPLATE_MAP:
        return {
            "success": False,
            "error": (
                f"Unknown document_type '{document_type}'. "
                f"Must be one of: {list(TEMPLATE_MAP.keys())}"
            ),
        }

    # Auto-generate sample data when none provided
    if not document_data:
        document_data = _build_sample_data(document_type)

    pdf_service = get_pdf_service()

    # 1) Generate PDF ────────────────────────────────────────────────
    try:
        pdf_bytes = pdf_service.generate_pdf(
            TEMPLATE_MAP[document_type], document_data
        )
    except Exception as e:
        logger.exception("PDF generation failed", document_type=document_type)
        return {"success": False, "error": f"PDF generation failed: {e}"}

    ref = document_data.get("reference", document_type)
    filename = f"{ref}.pdf"

    # 2) Auto-generate subject / body if not provided ────────────────
    if not subject:
        subject = f"{document_type.replace('_', ' ').title()}: {ref}"
    if not body:
        company = document_data.get("company_name", "Equa")
        body = (
            f"Please find the attached {document_type.replace('_', ' ')}.\n\n"
            f"Reference: {ref}\n"
            f"Generated by AIMMS on behalf of {company}."
        )

    # 3) Send email with attachment ──────────────────────────────────
    result = await send_email(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        attachments=[
            {
                "filename": filename,
                "data_bytes": pdf_bytes,
                "mime_type": "application/pdf",
            }
        ],
    )

    if result.get("success"):
        result["filename"] = filename
        result["pdf_size_kb"] = round(len(pdf_bytes) / 1024, 1)

    return result


# ── Export all tools ────────────────────────────────────────────────────────
EMAIL_TOOLS = [
    list_emails,
    get_email_details,
    download_attachment,
    mark_email_processed,
    send_email,
    generate_and_send_document,
]

