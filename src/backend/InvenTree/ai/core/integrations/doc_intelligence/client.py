"""
Azure Document Intelligence client wrapper.

Thin adapter around the ``azure-ai-documentintelligence`` SDK so the rest
of the codebase doesn't need to know about credential wiring.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy SDK import - the package is optional at dev-time
# ---------------------------------------------------------------------------
_SDK_AVAILABLE = False

try:
    from azure.ai.documentintelligence import DocumentIntelligenceClient as _DIClient
    from azure.ai.documentintelligence.models import AnalyzeResult
    from azure.core.credentials import AzureKeyCredential

    _SDK_AVAILABLE = True
except ImportError:
    _DIClient = None  # type: ignore[assignment,misc]
    AnalyzeResult = None  # type: ignore[assignment,misc]
    AzureKeyCredential = None  # type: ignore[assignment,misc]


class DocIntelligenceClient:
    """
    Wrapper for Azure Document Intelligence.

    Usage::

        from ai.core.integrations.doc_intelligence import get_doc_intelligence_client

        client = get_doc_intelligence_client()
        result = client.analyze_pdf(pdf_bytes)
        print(result.content)                # full extracted text
        print(result.tables)                 # list of extracted tables
    """

    def __init__(self, endpoint: str, key: str) -> None:
        if not _SDK_AVAILABLE:
            raise ImportError(
                "azure-ai-documentintelligence is not installed.  "
                "Install it with:  pip install azure-ai-documentintelligence"
            )
        self._client: _DIClient = _DIClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )
        self._endpoint = endpoint
        logger.info("DocIntelligenceClient initialised (endpoint=%s)", endpoint)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def analyze_pdf(
        self,
        pdf_bytes: bytes,
        *,
        model_id: str = "prebuilt-layout",
    ) -> AnalyzeResult:
        """
        Send *pdf_bytes* to Azure Document Intelligence and return the
        ``AnalyzeResult``.

        Parameters
        ----------
        pdf_bytes:
            Raw PDF content.
        model_id:
            DI model to use.  ``prebuilt-layout`` gives text + tables;
            ``prebuilt-invoice`` gives typed invoice fields, etc.
        """
        poller = self._client.begin_analyze_document(
            model_id,
            body=pdf_bytes,
            content_type="application/pdf",
        )
        result = poller.result()
        logger.info(
            "DI analysis complete - %d pages, %d tables, %d chars",
            len(result.pages) if result.pages else 0,
            len(result.tables) if result.tables else 0,
            len(result.content) if result.content else 0,
        )
        return result

    def analyze_layout_markdown(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> AnalyzeResult:
        """Run ``prebuilt-layout`` with Markdown output (attachment-RAG doc path).

        Markdown output turns DI headings into chunker section boundaries and
        preserves tables; ``result.content`` is the Markdown body and
        ``result.pages`` retains span/page structure.
        """
        poller = self._client.begin_analyze_document(
            "prebuilt-layout",
            body=data,
            content_type=content_type,
            output_content_format="markdown",
        )
        result = poller.result()
        logger.info(
            "DI markdown analysis complete - %d pages, %d chars",
            len(result.pages) if result.pages else 0,
            len(result.content) if result.content else 0,
        )
        return result

    def extract_text(self, pdf_bytes: bytes, *, model_id: str = "prebuilt-layout") -> str:
        """Convenience: return just the full text content string."""
        result = self.analyze_pdf(pdf_bytes, model_id=model_id)
        return result.content or ""

    def extract_tables_as_markdown(
        self, pdf_bytes: bytes, *, model_id: str = "prebuilt-layout"
    ) -> list[str]:
        """
        Return each extracted table formatted as a Markdown table string.
        """
        result = self.analyze_pdf(pdf_bytes, model_id=model_id)
        md_tables: list[str] = []

        for table in result.tables or []:
            rows: dict[int, dict[int, str]] = {}
            for cell in table.cells or []:
                rows.setdefault(cell.row_index, {})[cell.column_index] = cell.content or ""

            if not rows:
                continue

            max_col = max(c for cols in rows.values() for c in cols) + 1
            lines: list[str] = []
            for r in sorted(rows):
                cells = [rows[r].get(c, "") for c in range(max_col)]
                lines.append("| " + " | ".join(cells) + " |")
                if r == 0:
                    lines.append("| " + " | ".join(["---"] * max_col) + " |")
            md_tables.append("\n".join(lines))

        return md_tables

    @staticmethod
    def is_available() -> bool:
        """Return ``True`` if the Azure DI SDK is installed."""
        return _SDK_AVAILABLE


def get_doc_intelligence_client() -> DocIntelligenceClient | None:
    """
    Create a ``DocIntelligenceClient`` from env-based settings.

    Returns ``None`` when the SDK is not installed or the settings are
    not configured.
    """
    if not _SDK_AVAILABLE:
        logger.info("Azure DI SDK not installed - DI fallback disabled")
        return None

    try:
        from ai.core.config import get_azure_doc_intelligence_settings

        settings = get_azure_doc_intelligence_settings()
        return DocIntelligenceClient(
            endpoint=settings.endpoint,
            key=settings.key.get_secret_value(),
        )
    except Exception as e:
        logger.warning("Could not create DocIntelligenceClient: %s", e)
        return None
