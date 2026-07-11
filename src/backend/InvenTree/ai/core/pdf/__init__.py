"""
PDF Generation Module

Generates standardized PDF documents (Sales Orders, Purchase Orders, BOMs, Quotes)
from Jinja2 HTML templates using WeasyPrint.

Architecture:
    - PDFService: Singleton renders Jinja2 templates → HTML → PDF bytes
    - Templates: HTML/CSS in templates/ directory (base + per-document-type)
    - Integration: Agent tools call PDFService then attach to outgoing emails

Usage:
    from ai.core.pdf import get_pdf_service

    pdf = get_pdf_service()
    pdf_bytes = pdf.generate_pdf("sales_order.html", {"reference": "SO-0042", ...})
"""

from ai.core.pdf.service import PDFService, get_pdf_service

__all__ = [
    "PDFService",
    "get_pdf_service",
]
