"""
PDF Generation Service

Renders Jinja2 HTML templates and converts to PDF using WeasyPrint.
Templates live in the ``templates/`` subdirectory next to this file.

Supported document types
------------------------
- sales_order
- purchase_order
- bom  (Bill of Materials)
- quote

Each type maps to an HTML template that extends ``base.html``.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

# ── Template directory (relative to this file) ──────────────────────────────
TEMPLATE_DIR = Path(__file__).parent / "templates"

# ── Supported document types → template filenames ───────────────────────────
TEMPLATE_MAP: dict[str, str] = {
    "sales_order": "sales_order.html",
    "purchase_order": "purchase_order.html",
    "bom": "bom.html",
    "quote": "quote.html",
    "rfq": "rfq.html",
    "work_order": "work_order.html",
}


# ── Custom Jinja2 filters ──────────────────────────────────────────────────
def _format_currency(value: Any, symbol: str = "$", decimals: int = 2) -> str:
    """Format a number as currency.  ``{{ 1234.5 | currency }}`` → ``$1,234.50``"""
    try:
        num = float(value)
        return f"{symbol}{num:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _format_number(value: Any, decimals: int = 2) -> str:
    """Format a number with thousands separator.  ``{{ 10000 | number }}`` → ``10,000.00``"""
    try:
        num = float(value)
        return f"{num:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


# ── PDFService ──────────────────────────────────────────────────────────────
class PDFService:
    """
    Generate PDFs from Jinja2 HTML templates via WeasyPrint.

    The class maintains a single Jinja2 ``Environment`` with a
    ``FileSystemLoader`` pointed at the templates directory.  WeasyPrint is
    imported lazily so the module can be loaded even when the system libs
    aren't available (useful for unit-test environments that only validate
    templates without rendering to PDF).
    """

    def __init__(self, template_dir: Path | None = None) -> None:
        self.template_dir = template_dir or TEMPLATE_DIR
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html"]),
        )
        # Register custom filters
        self.env.filters["currency"] = _format_currency
        self.env.filters["number"] = _format_number

    # ── Core methods ────────────────────────────────────────────────────────

    def render_html(self, template_name: str, context: dict[str, Any]) -> str:
        """Render a Jinja2 template to an HTML string."""
        context.setdefault(
            "generated_date", datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        template = self.env.get_template(template_name)
        return template.render(**context)

    def generate_pdf(self, template_name: str, context: dict[str, Any]) -> bytes:
        """Render *template_name* with *context* and return PDF bytes."""
        from weasyprint import HTML  # lazy import – heavy C deps

        html_string = self.render_html(template_name, context)
        pdf_buffer = io.BytesIO()
        HTML(
            string=html_string,
            base_url=str(self.template_dir),
        ).write_pdf(pdf_buffer)
        pdf_bytes = pdf_buffer.getvalue()

        logger.info(
            "Generated PDF  template=%s  size=%.1f KB",
            template_name,
            len(pdf_bytes) / 1024,
        )
        return pdf_bytes

    # ── Convenience per-document-type methods ───────────────────────────────

    def sales_order_pdf(self, data: dict[str, Any]) -> bytes:
        """Generate a Sales Order PDF."""
        return self.generate_pdf("sales_order.html", data)

    def purchase_order_pdf(self, data: dict[str, Any]) -> bytes:
        """Generate a Purchase Order PDF."""
        return self.generate_pdf("purchase_order.html", data)

    def bom_pdf(self, data: dict[str, Any]) -> bytes:
        """Generate a Bill of Materials PDF."""
        return self.generate_pdf("bom.html", data)

    def quote_pdf(self, data: dict[str, Any]) -> bytes:
        """Generate a Quote PDF."""
        return self.generate_pdf("quote.html", data)

    def rfq_pdf(self, data: dict[str, Any]) -> bytes:
        """Generate a Request for Quote (RFQ) PDF."""
        return self.generate_pdf("rfq.html", data)

    def work_order_pdf(self, data: dict[str, Any]) -> bytes:
        """Generate a Work Order PDF."""
        return self.generate_pdf("work_order.html", data)

    # ── Validation helper ───────────────────────────────────────────────────

    @staticmethod
    def supported_types() -> list[str]:
        """Return the list of supported document type keys."""
        return list(TEMPLATE_MAP.keys())

    def resolve_template(self, document_type: str) -> str | None:
        """Map a document_type key to a template filename (or ``None``)."""
        return TEMPLATE_MAP.get(document_type)


# ── Module-level singleton ──────────────────────────────────────────────────
_pdf_service: PDFService | None = None


def get_pdf_service() -> PDFService:
    """Return (and lazily create) the singleton ``PDFService``."""
    global _pdf_service
    if _pdf_service is None:
        _pdf_service = PDFService()
    return _pdf_service
