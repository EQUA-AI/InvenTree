# Gmail (Google Workspace) setup for AI email tools

This repo’s AI workflows that “use email” are implemented via the Gmail API using a **Google Cloud Service Account** with **Domain‑Wide Delegation** (DWD). The mailbox the agent operates as is configured by `GMAIL_EMAIL`.

The implementation lives in:
- Gmail client: `src/backend/InvenTree/ai/core/integrations/email/gmail.py`
- Email tools exposed to agents: `src/backend/InvenTree/ai/core/integrations/email/tools.py`
- Settings (env vars): `src/backend/InvenTree/ai/core/config.py` (`GmailSettings`)

---

## What you will end up with

- A Google Cloud **service account JSON key** stored locally (or in your secret store)
- Google Workspace Admin configured to allow that service account to impersonate users
- A `.env` containing at least:
  - `GMAIL_EMAIL=your-mailbox@yourdomain.com`
  - `GOOGLE_SERVICE_ACCOUNT_PATH=./secrets/google-service-account.json`

---

## Prerequisites

- Your mailbox is **Google Workspace Gmail** (managed domain)
- You have access to:
  - **Google Cloud Console** for your Workspace org/project, and
  - **Google Workspace Admin Console** with permission to manage API controls

Security note:
- Do **not** commit service account JSON keys to git.
- Store the JSON key in a secrets store in production.

---

## Step 1 — Create/choose a Google Cloud project

1. Go to Google Cloud Console.
2. Select an existing project or create a new one.
3. (Optional but recommended) Name it something like `inventree-ai-email`.

---

## Step 2 — Enable the Gmail API

1. Google Cloud Console → **APIs & Services** → **Library**
2. Search for **Gmail API**
3. Click **Enable**

---

## Step 3 — Create a Service Account + JSON key

1. Google Cloud Console → **IAM & Admin** → **Service Accounts**
2. **Create service account**
   - Name: e.g. `inventree-ai-gmail`
3. Create a key:
   - Open the service account → **Keys** tab
   - **Add Key** → **Create new key** → JSON
   - Download the JSON key

Place the key file in your repo (recommended dev path):

- `./secrets/google-service-account.json`

If you use a different path, you’ll set it via `GOOGLE_SERVICE_ACCOUNT_PATH`.

---

## Step 4 — Enable Domain‑Wide Delegation (DWD)

This is the critical Workspace step that allows a service account to impersonate a user.

### 4A) Enable DWD on the service account

1. Google Cloud Console → **IAM & Admin** → **Service Accounts**
2. Open your service account
3. Find the setting **“Domain-wide delegation”** and enable it
4. Save

### 4B) Copy the OAuth Client ID

You’ll need the service account’s **OAuth 2.0 Client ID**.

- In the service account details, find **Client ID** (OAuth2 client id)
- Copy it

---

## Step 5 — Authorize the service account in Google Workspace Admin

1. Google Workspace Admin Console → **Security** → **Access and data control** → **API controls**
2. Under **Domain-wide delegation**, click **Manage Domain Wide Delegation**
3. Click **Add new**
4. Paste the **Client ID** from Step 4B
5. Add the **OAuth Scopes**

Recommended scopes (match repo defaults):

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/gmail.modify`

These enable:
- reading emails and attachments (`gmail.readonly`)
- marking messages read and applying labels (`gmail.modify`) via the tools

If you want the agent to be strictly read-only, you can omit `gmail.modify`, but then any “mark processed” tool behavior may fail.

---

## Step 6 — Configure this repo (.env)

This repo’s Gmail settings are defined in `src/backend/InvenTree/ai/core/config.py` (`GmailSettings`).

Add/update these variables in your `.env` (the AI subsystem loads `.env`):

```bash
# Gmail mailbox the agent will impersonate
GMAIL_EMAIL=your-mailbox@yourdomain.com

# Path to the service account JSON key
GOOGLE_SERVICE_ACCOUNT_PATH=./secrets/google-service-account.json

# Optional: override scopes (comma-separated)
# GMAIL_SCOPES=https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.modify
```

Notes:
- `GMAIL_EMAIL` is the *user mailbox* to impersonate.
- `GOOGLE_SERVICE_ACCOUNT_PATH` points to the **service account JSON**.

---

## Step 6.1 — Use a dedicated Workspace user mailbox (recommended)

If you created a Google Workspace user specifically for the agent (for example `agent-bot@yourdomain.com`), you do **not** create new credentials.

You simply set `GMAIL_EMAIL` to that user’s email address.

Why this works:
- The Gmail client uses Domain‑Wide Delegation and calls `credentials.with_subject(GMAIL_EMAIL)`.
- All API calls use `userId="me"`, which becomes “the impersonated user”.

### Local dev

In `.env`:

```bash
GMAIL_EMAIL=agent-bot@yourdomain.com
GOOGLE_SERVICE_ACCOUNT_PATH=./secrets/google-service-account.json
```

### Azure Container Apps (prod)

Update the Container App env var:

```bash
az containerapp update \
  --name <ACA_APP_NAME> \
  --resource-group <RG_NAME> \
  --set-env-vars GMAIL_EMAIL=agent-bot@yourdomain.com
```

### Common gotchas

- Make sure the Workspace user is **active** (not suspended).
- Make sure **Gmail is enabled** for that user and they have an appropriate license.
- Prefer using the user’s **primary email address** for `GMAIL_EMAIL`.
  - Aliases can work, but primary is the least surprising.
- If you use labels / mark-as-read, keep the `gmail.modify` scope authorized in Admin Console.

---

## Step 7 — Verify locally (quick smoke test)

There are two straightforward ways to verify.

### Option A: Run the server + use the agent tools

1. Start the backend (whatever you normally use, e.g. the repo task)
2. In DevUI / agent chat, ask something like:

- “List my unread emails with attachments”

Under the hood, the agent calls `list_emails(...)` from `src/backend/InvenTree/ai/core/integrations/email/tools.py`.

### Option B: Run a one-off Python check

If your runtime environment can import the AI backend package, run something like:

```bash
python3 -c "
import asyncio
from ai.core.integrations.email.tools import list_emails

async def main():
    res = await list_emails(is_unread=True, has_attachment=True, max_results=5)
    print(res)

asyncio.run(main())
"
```

If credentials are correct, you should see `success: True` and some emails.

---

## Step 8 — Production deployment notes

For production, do **not** bake a `.env` file or the service account JSON into the container image.

### Azure Container Apps (ACA) production setup

This approach stores the Google service account JSON key as an **ACA secret**, mounts it into the container as a **file**, and sets the two required env vars.

#### 8A) Create / update the ACA secret

Pick a secret name (example: `gmail-sa-json`). Then set it on the container app:

```bash
# Required: logged in and pointing at the right subscription
az login

# Set the secret value to the JSON contents
az containerapp secret set \
  --name <ACA_APP_NAME> \
  --resource-group <RG_NAME> \
  --secrets gmail-sa-json="$(cat ./secrets/google-service-account.json)"
```

Notes:
- The JSON contains quotes/newlines; use the exact command form above.
- If your shell has trouble with this, you can set the secret in the Azure Portal instead (Container App → **Secrets**).

#### 8B) Mount the secret as a file (recommended)

The Gmail client expects a **file path** for `GOOGLE_SERVICE_ACCOUNT_PATH`, so mount the secret as a file inside the container.

The most reliable way is to update the Container App using a YAML template.

1) Export the current Container App YAML:

```bash
az containerapp show \
  --name <ACA_APP_NAME> \
  --resource-group <RG_NAME> \
  --output yaml > containerapp.yaml
```

2) Edit `containerapp.yaml` to include a secret volume + mount. In the container definition, add:

```yaml
properties:
  template:
    containers:
      - name: <YOUR_CONTAINER_NAME>
        volumeMounts:
          - volumeName: gmail-sa
            mountPath: /mnt/secrets
    volumes:
      - name: gmail-sa
        storageType: Secret
        secrets:
          - secretRef: gmail-sa-json
            path: google-service-account.json
```

3) Apply the updated YAML:

```bash
az containerapp update \
  --name <ACA_APP_NAME> \
  --resource-group <RG_NAME> \
  --yaml containerapp.yaml
```

After this, the JSON key file will be available at:

- `/mnt/secrets/google-service-account.json`

#### 8C) Set environment variables on the Container App

Set the mailbox and the file path:

```bash
az containerapp update \
  --name <ACA_APP_NAME> \
  --resource-group <RG_NAME> \
  --set-env-vars \
    GMAIL_EMAIL=your-mailbox@yourdomain.com \
    GOOGLE_SERVICE_ACCOUNT_PATH=/mnt/secrets/google-service-account.json
```

Optional (only if you want to override defaults):

```bash
az containerapp update \
  --name <ACA_APP_NAME> \
  --resource-group <RG_NAME> \
  --set-env-vars \
    GMAIL_SCOPES=https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.modify
```

#### 8D) Verify in production

- Confirm the container starts successfully.
- Use your app’s UI / agent endpoint to call `list_emails(...)`.
- If it fails, check ACA logs for 401/403 errors.

### Alternative: Key Vault (optional)

If you already use Azure Key Vault, you can store the JSON key there and inject it into ACA as a secret (or mount via CSI driver patterns). The important part is still the same:

- the runtime must expose a **file path** for `GOOGLE_SERVICE_ACCOUNT_PATH`, and
- `GMAIL_EMAIL` must be set.

---

## Troubleshooting

### 403: “Not Authorized to access this resource/api”

Most common causes:
- Domain-wide delegation not enabled on the service account
- Workspace Admin did not add the client id + scopes
- Scopes mismatch (admin authorized one set, app requests another)

Fix:
- Re-check Step 4 and Step 5.
- Ensure Admin Console scopes include `gmail.readonly` and (if needed) `gmail.modify`.

### 401 / invalid_grant / credential errors

Most common causes:
- Wrong JSON key file (not the service account key)
- `GOOGLE_SERVICE_ACCOUNT_PATH` points to a missing file

Fix:
- Confirm the JSON file exists and is the one downloaded from the service account.

### Works in Cloud Console but not in code

Most common causes:
- Your `.env` isn’t being loaded in the environment where the backend runs

Fix:
- Ensure the backend process starts with the correct working directory / `.env` present.
- Confirm the configured path is correct relative to the process working dir.

---

## Reference: env vars used by the code

- `GMAIL_EMAIL` (default in code is `parts@equa.work`)
- `GOOGLE_SERVICE_ACCOUNT_PATH` (default `./secrets/google-service-account.json`)
- `GMAIL_SCOPES` (defaults to readonly + modify)

See `GmailSettings` in `src/backend/InvenTree/ai/core/config.py`.

---

## PDF Generation & Email Sending (Agent-driven documents)

This section covers how the AI agent can **generate standardized PDFs** (Sales Orders, Purchase Orders, BOMs, Quotes, etc.) and **send them as email attachments** via the Gmail API.

---

### Architecture overview

```
Agent receives user request
  │
  ├─ 1. Gather data from InvenTree  (existing read tools)
  │     └─ get_sales_orders, get_purchase_orders, get_bom, etc.
  │
  ├─ 2. Render HTML from Jinja2 template  (new PDFService)
  │     └─ templates/sales_order.html, purchase_order.html, bom.html
  │
  ├─ 3. Convert HTML → PDF  (WeasyPrint)
  │     └─ returns bytes
  │
  └─ 4. Send email with PDF attachment  (new send_email tool)
        └─ Gmail API: users.messages.send  (MIME multipart)
```

### Why this approach

| Option | Pros | Cons |
|--------|------|------|
| **Jinja2 + WeasyPrint** (recommended) | HTML templates are easy to design/maintain; CSS for styling; page headers/footers; version-controllable; InvenTree already uses this pattern for its Django reports | Needs `weasyprint` system deps |
| ReportLab | Pure Python, no system deps | Very verbose; templates are code, not markup |
| fpdf2 | Lightweight | No CSS; limited layout control |
| Puppeteer/wkhtmltopdf | External binary | Heavy; hard to containerize |

**InvenTree's own report engine** already uses HTML/CSS → PDF via WeasyPrint (see `src/backend/InvenTree/report/templates/report/`). The AI backend can reuse the same pattern with its own standalone Jinja2 templates — no Django dependency required.

---

### Step 1 — Install dependencies

Add to `src/backend/InvenTree/ai/requirements.txt`:

```
# PDF generation
weasyprint>=62.0
Jinja2>=3.1.0   # (already installed as a transitive dep)
```

System packages needed for WeasyPrint on Debian/Ubuntu:

```bash
sudo apt install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev libcairo2
```

Then install in the venv:

```bash
.venv/bin/pip install weasyprint Jinja2
```

---

### Step 2 — Create HTML templates

Create a templates directory:

```
src/backend/InvenTree/ai/core/pdf/
├── __init__.py
├── service.py          # PDFService class
└── templates/
    ├── base.html        # Shared layout (logo, page margins, footer)
    ├── sales_order.html
    ├── purchase_order.html
    ├── bom.html
    └── quote.html
```

#### `templates/base.html` — shared layout

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @page {
    size: A4;
    margin: 2cm;
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 9px; }
    @bottom-left  { content: "Generated {{ generated_date }}"; font-size: 9px; color: #999; }
  }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 11px; color: #333; }
  h1 { font-size: 18px; margin-bottom: 4px; }
  h2 { font-size: 14px; color: #555; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 2px solid #2563eb; padding-bottom: 10px; }
  .header .logo { max-height: 50px; }
  .meta-table { width: 100%; margin-bottom: 20px; }
  .meta-table td { padding: 3px 8px; }
  .meta-table .label { font-weight: bold; width: 140px; background: #f3f4f6; }
  table.line-items { width: 100%; border-collapse: collapse; margin-top: 10px; }
  table.line-items th { background: #2563eb; color: white; padding: 6px 8px; text-align: left; font-size: 10px; }
  table.line-items td { padding: 5px 8px; border-bottom: 1px solid #e5e7eb; }
  table.line-items tr:nth-child(even) { background: #f9fafb; }
  .total-row td { font-weight: bold; border-top: 2px solid #333; }
  .notes { margin-top: 20px; padding: 10px; background: #fffbeb; border-left: 3px solid #f59e0b; }
</style>
</head>
<body>
  <div class="header">
    {% if company_logo %}<img class="logo" src="{{ company_logo }}" alt="Logo">{% endif %}
    <div>
      <h1>{% block title %}Document{% endblock %}</h1>
      <h2>{% block subtitle %}{% endblock %}</h2>
    </div>
  </div>
  {% block content %}{% endblock %}
  {% if notes %}
  <div class="notes"><strong>Notes:</strong><br>{{ notes }}</div>
  {% endif %}
</body>
</html>
```

#### `templates/sales_order.html`

```html
{% extends "base.html" %}

{% block title %}Sales Order{% endblock %}
{% block subtitle %}{{ reference }}{% endblock %}

{% block content %}
<table class="meta-table">
  <tr><td class="label">Customer</td><td>{{ customer_name }}</td><td class="label">Date</td><td>{{ issue_date }}</td></tr>
  <tr><td class="label">Reference</td><td>{{ reference }}</td><td class="label">Target Date</td><td>{{ target_date }}</td></tr>
  <tr><td class="label">Status</td><td>{{ status }}</td><td class="label">Currency</td><td>{{ currency }}</td></tr>
</table>

<table class="line-items">
  <thead>
    <tr><th>#</th><th>Part</th><th>Reference</th><th>Qty</th><th>Unit Price</th><th>Total</th></tr>
  </thead>
  <tbody>
    {% for line in lines %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ line.part_name }}</td>
      <td>{{ line.reference }}</td>
      <td>{{ line.quantity }}</td>
      <td>{{ line.unit_price }}</td>
      <td>{{ line.total_price }}</td>
    </tr>
    {% endfor %}
    <tr class="total-row">
      <td colspan="5" style="text-align:right">Total</td>
      <td>{{ total_price }}</td>
    </tr>
  </tbody>
</table>
{% endblock %}
```

_(Create similar `purchase_order.html`, `bom.html`, `quote.html` following the same pattern.)_

---

### Step 3 — PDF generation service

#### `src/backend/InvenTree/ai/core/pdf/service.py`

```python
"""
PDF Generation Service

Renders Jinja2 HTML templates and converts to PDF using WeasyPrint.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

# Template directory (relative to this file)
TEMPLATE_DIR = Path(__file__).parent / "templates"


class PDFService:
    """Generate PDFs from HTML templates."""

    def __init__(self, template_dir: Path | None = None):
        self.template_dir = template_dir or TEMPLATE_DIR
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=True,
        )

    def render_html(self, template_name: str, context: dict[str, Any]) -> str:
        """Render a Jinja2 template to HTML string."""
        context.setdefault("generated_date", datetime.now().strftime("%Y-%m-%d %H:%M"))
        template = self.env.get_template(template_name)
        return template.render(**context)

    def generate_pdf(self, template_name: str, context: dict[str, Any]) -> bytes:
        """Render template and convert to PDF bytes."""
        from weasyprint import HTML  # lazy import

        html_string = self.render_html(template_name, context)
        pdf_buffer = io.BytesIO()
        HTML(string=html_string, base_url=str(self.template_dir)).write_pdf(pdf_buffer)
        pdf_bytes = pdf_buffer.getvalue()
        logger.info(
            "Generated PDF",
            template=template_name,
            size_kb=len(pdf_bytes) / 1024,
        )
        return pdf_bytes

    # ── Convenience methods per document type ──

    def sales_order_pdf(self, data: dict[str, Any]) -> bytes:
        return self.generate_pdf("sales_order.html", data)

    def purchase_order_pdf(self, data: dict[str, Any]) -> bytes:
        return self.generate_pdf("purchase_order.html", data)

    def bom_pdf(self, data: dict[str, Any]) -> bytes:
        return self.generate_pdf("bom.html", data)

    def quote_pdf(self, data: dict[str, Any]) -> bytes:
        return self.generate_pdf("quote.html", data)


# Module-level singleton
_pdf_service: PDFService | None = None


def get_pdf_service() -> PDFService:
    global _pdf_service
    if _pdf_service is None:
        _pdf_service = PDFService()
    return _pdf_service
```

---

### Step 4 — Send email tool (with PDF attachment)

Add a `send_email` function to `src/backend/InvenTree/ai/core/integrations/email/tools.py`.

This requires `gmail.send` scope (already authorized in the current `.env`).

```python
import base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


async def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    cc: str | list[str] | None = None,
    attachments: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Send an email with optional PDF attachments.

    Args:
        to: Recipient email address(es).
        subject: Email subject line.
        body: Plain text email body.
        cc: CC addresses (optional).
        attachments: List of attachment dicts, each containing:
            - filename: str  (e.g. "SO-0042.pdf")
            - data_bytes: bytes  (raw PDF bytes)
            - mime_type: str  (default "application/pdf")

    Returns:
        dict with 'success', 'message_id', and optionally 'error'.
    """
    try:
        client = get_gmail_client()
        service = client._get_service()

        # Build MIME message
        msg = MIMEMultipart()
        msg["To"] = ", ".join(to) if isinstance(to, list) else to
        msg["From"] = client.email  # impersonated user
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc) if isinstance(cc, list) else cc

        msg.attach(MIMEText(body, "plain"))

        # Attach files
        for att in (attachments or []):
            mime = att.get("mime_type", "application/pdf")
            maintype, subtype = mime.split("/", 1)
            part = MIMEApplication(att["data_bytes"], _subtype=subtype)
            part.add_header(
                "Content-Disposition", "attachment",
                filename=att["filename"],
            )
            msg.attach(part)

        # Encode and send
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        result = service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        logger.info("Email sent", message_id=result["id"], to=to)
        return {"success": True, "message_id": result["id"]}

    except Exception as e:
        logger.exception("Failed to send email")
        return {"success": False, "error": str(e)}
```

---

### Step 5 — Agent-facing compound tool (generate + send)

This is the high-level agent tool that ties it all together:

```python
from ai.core.pdf.service import get_pdf_service


async def generate_and_send_document(
    document_type: str,
    document_data: dict[str, Any],
    to: str | list[str],
    subject: str | None = None,
    body: str | None = None,
    cc: str | list[str] | None = None,
) -> dict[str, Any]:
    """
    Generate a standardized PDF and send it to a recipient via email.

    Args:
        document_type: One of "sales_order", "purchase_order", "bom", "quote".
        document_data: The data to populate the document template.
            Sales Order keys:
              reference, customer_name, issue_date, target_date, status,
              currency, lines (list of {part_name, reference, quantity,
              unit_price, total_price}), total_price, notes
            Purchase Order keys:
              reference, supplier_name, issue_date, target_date, status,
              currency, lines (same shape), total_price, notes
            BOM keys:
              part_name, part_ipn, revision, lines (list of {part_name,
              reference, quantity, units, optional}), notes
        to: Recipient email address(es).
        subject: Email subject (auto-generated if omitted).
        body: Email body text (auto-generated if omitted).
        cc: Optional CC addresses.

    Returns:
        dict with 'success', 'message_id', 'filename', 'pdf_size_kb'.
    """
    TEMPLATE_MAP = {
        "sales_order": "sales_order.html",
        "purchase_order": "purchase_order.html",
        "bom": "bom.html",
        "quote": "quote.html",
    }

    if document_type not in TEMPLATE_MAP:
        return {
            "success": False,
            "error": f"Unknown document_type '{document_type}'. "
                     f"Must be one of: {list(TEMPLATE_MAP.keys())}",
        }

    pdf_service = get_pdf_service()

    # 1. Generate PDF
    pdf_bytes = pdf_service.generate_pdf(
        TEMPLATE_MAP[document_type], document_data
    )

    ref = document_data.get("reference", document_type)
    filename = f"{ref}.pdf"

    # 2. Default subject/body
    if not subject:
        subject = f"{document_type.replace('_', ' ').title()}: {ref}"
    if not body:
        body = (
            f"Please find the attached {document_type.replace('_', ' ')}.\n\n"
            f"Reference: {ref}\n"
            f"Generated by AIMMS on behalf of {document_data.get('company_name', 'Equa')}."
        )

    # 3. Send email with attachment
    result = await send_email(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        attachments=[{
            "filename": filename,
            "data_bytes": pdf_bytes,
            "mime_type": "application/pdf",
        }],
    )

    if result.get("success"):
        result["filename"] = filename
        result["pdf_size_kb"] = round(len(pdf_bytes) / 1024, 1)

    return result
```

---

### Step 6 — End-to-end agent workflow example

An agent conversation might look like:

> **User:** "Send the sales order SO-0042 to john@acme.com"

The agent would:

1. Call `get_sales_orders(reference="SO-0042")` → get order data + lines
2. Call `generate_and_send_document(document_type="sales_order", document_data={...}, to="john@acme.com")`
3. Return: "Sent SO-0042.pdf to john@acme.com (message ID: xxx)"

---

### Step 7 — Required scopes

Make sure the following scope is authorized in **Google Workspace Admin Console** (Step 5 of the main guide) and set in your `.env`:

```
GMAIL_SCOPES=https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send
```

The `gmail.send` scope is required. The current local `.env` already includes it.

---

### Template customization tips

- **Company logo**: Place a logo image in the `templates/` directory and reference with `<img src="logo.png">` — WeasyPrint resolves relative to `base_url`.
- **Currency formatting**: Use Jinja2 filters: `{{ amount | format_currency }}` (register a custom filter in `PDFService.__init__`).
- **Page orientation**: Use `@page { size: A4 landscape; }` in template CSS for BOM reports with many columns.
- **Matching InvenTree's look**: The existing InvenTree report templates in `src/backend/InvenTree/report/templates/report/` use the exact same HTML/CSS → PDF pattern. You can copy their styling or base layout.

---

### File structure summary

```
src/backend/InvenTree/ai/
├── core/
│   ├── pdf/
│   │   ├── __init__.py
│   │   ├── service.py            # PDFService (render + convert)
│   │   └── templates/
│   │       ├── base.html          # Shared page layout
│   │       ├── sales_order.html
│   │       ├── purchase_order.html
│   │       ├── bom.html
│   │       └── quote.html
│   └── integrations/
│       └── email/
│           └── tools.py           # + send_email()
│                                  # + generate_and_send_document()
```
