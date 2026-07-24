"""
WF6: Unified Document Processing Workflow

Merges the original pipeline-style WF6 with the conversational v2 design
and adds an intelligent PDF extraction strategy:

    1. **Local-first** - try ``pypdf`` (fast, free, offline).
    2. **Local OCR** - if pypdf yields too little text, try ``pdf2image`` +
       ``pytesseract``.
    3. **Azure Document Intelligence fallback** - if extraction confidence
       is low *or* the document appears table-heavy, escalate to Azure DI
       for structured layout + table extraction.

Modes of operation (selected automatically or by caller):

*   **Pipeline** - classify -> extract -> validate -> route  (old WF6)
*   **Conversational** - single agent with tools + document context  (old v2)

Integrates with:
- Gmail for email / attachment retrieval
- Azure Document Intelligence for high-fidelity OCR / table extraction
- InvenTree inventory tools for validation & writes
"""

from __future__ import annotations

import io
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_framework import ChatAgent
from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings
from ai.core.integrations.email import EMAIL_TOOLS
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS
from ai.core.workflows.rbac_run import run_with_rbac

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimum character threshold - below this, local extraction is "insufficient"
# ---------------------------------------------------------------------------
_MIN_TEXT_CHARS = 200

# ---------------------------------------------------------------------------
# Table-detection heuristics
# ---------------------------------------------------------------------------
_TABLE_KEYWORDS = re.compile(
    r"\btotal\b|\bsubtotal\b|\bqty\b|\bquantity\b|\bunit\s*price\b|\bline\s*item\b"
    r"|\binvoice\b|\bpurchase\s*order\b|\bpo\s*#\b",
    re.IGNORECASE,
)
_TABLE_SEPARATOR_PATTERN = re.compile(r"[\|\+]{2,}")


# ===========================================================================
# Enums & data-classes
# ===========================================================================


class ExtractionMode(Enum):
    """How text was extracted from the source PDF."""

    PYPDF = "pypdf"
    OCR = "ocr"
    AZURE_DI = "azure_document_intelligence"
    BASIC_HEURISTIC = "basic_heuristic"
    PROVIDED = "provided"


class DocumentType(Enum):
    """Types of documents processed."""

    REQUEST_FOR_QUOTE = "rfq"
    PURCHASE_ORDER = "purchase_order"
    INVOICE = "invoice"
    TECHNICAL_SPEC = "technical_specification"
    PACKING_LIST = "packing_list"
    SHIPPING_DOCUMENT = "shipping_document"
    DRAWING = "drawing"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class ProcessingStatus(Enum):
    """Status of document processing."""

    RECEIVED = "received"
    ANALYZING = "analyzing"
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    PROCESSED = "processed"
    ERROR = "error"
    PENDING_REVIEW = "pending_review"


class WorkflowMode(Enum):
    """Which execution path the workflow uses."""

    PIPELINE = "pipeline"
    CONVERSATIONAL = "conversational"


@dataclass
class ExtractedField:
    """A field extracted from a document."""

    name: str
    value: Any
    confidence: float = 0.0
    page: int = 1
    bounding_box: list[float] | None = None


@dataclass
class ExtractedLineItem:
    """A line item extracted from a document."""

    line_number: int
    description: str
    part_number: str = ""
    quantity: float = 0.0
    unit_price: float = 0.0
    total_price: float = 0.0
    matched_part_id: int | None = None


@dataclass
class DocumentExtractionResult:
    """Result of document extraction."""

    document_id: str = field(default_factory=lambda: f"DOC-{uuid.uuid4().hex[:8].upper()}")
    document_type: DocumentType = DocumentType.UNKNOWN
    status: ProcessingStatus = ProcessingStatus.RECEIVED
    source_filename: str = ""
    source_email_id: str = ""

    # Extracted data
    fields: list[ExtractedField] = field(default_factory=list)
    line_items: list[ExtractedLineItem] = field(default_factory=list)

    # Key extracted values
    document_number: str = ""
    document_date: datetime | None = None
    vendor_name: str = ""
    vendor_id: str = ""
    total_amount: float = 0.0
    currency: str = "USD"

    # Processing metadata
    extraction_confidence: float = 0.0
    extraction_mode: ExtractionMode = ExtractionMode.PROVIDED
    azure_di_tables: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_type": self.document_type.value,
            "status": self.status.value,
            "source_filename": self.source_filename,
            "document_number": self.document_number,
            "document_date": (self.document_date.isoformat() if self.document_date else None),
            "vendor_name": self.vendor_name,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "line_items_count": len(self.line_items),
            "extraction_confidence": self.extraction_confidence,
            "extraction_mode": self.extraction_mode.value,
            "validation_errors": self.validation_errors,
            "validation_warnings": self.validation_warnings,
        }


@dataclass
class DocumentProcessingResult:
    """Result of complete document processing."""

    success: bool
    extraction: DocumentExtractionResult | None = None
    actions_taken: list[str] = field(default_factory=list)
    formatted_response: str = ""
    execution_time_ms: float = 0.0
    error: str | None = None


# ===========================================================================
# Sub-agents (pipeline mode)
# ===========================================================================


class DocumentClassificationAgent:
    """Classify the document type (RFQ, PO, invoice, datasheet ...)."""

    SYSTEM_PROMPT = """You are a document classification specialist.
Your job is to identify the type of business document based on its content.

Document Types:
- TECHNICAL_SPEC: Product datasheets, specification sheets, technical data, product manuals with specifications
- MANUAL: User manuals, instruction guides, installation guides
- REQUEST_FOR_QUOTE (RFQ): Request for pricing/quotation
- PURCHASE_ORDER (PO): Formal order for goods/services
- INVOICE: Bill for goods/services
- PACKING_LIST: List of items in a shipment
- SHIPPING_DOCUMENT: Shipping/delivery documents
- DRAWING: Technical drawings or schematics
- UNKNOWN: Cannot determine type

Classification Clues:
- Product specifications, model numbers, technical parameters -> TECHNICAL_SPEC
- Brand name + model series + specifications -> TECHNICAL_SPEC
- "Datasheet" or "Data Sheet" or "Technical Data" -> TECHNICAL_SPEC
- Installation instructions, wiring diagrams -> MANUAL
- "Request for Quote" or "RFQ" -> RFQ
- "Purchase Order" or "PO #" -> PURCHASE_ORDER
- "Invoice" or "Bill To" -> INVOICE

Analyze the document and respond with:
- Document type
- Confidence level (high/medium/low)
- Key identifying features found
- If TECHNICAL_SPEC: List the main product/part information you can identify"""

    def __init__(self) -> None:
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Document Classification Agent",
            )
        return self._agent


class DataExtractionAgent:
    """Extract structured data from document text."""

    SYSTEM_PROMPT = """You are a data extraction specialist.
Your job is to extract structured information from business documents.

For TECHNICAL SPECIFICATIONS / DATASHEETS:
1. PRODUCT INFORMATION:
   - Manufacturer/Brand name
   - Product series/family name
   - Model numbers (all variants listed)
   - Part numbers / Order codes
   - Product description
   - Category (valve, sensor, controller, etc.)

2. TECHNICAL SPECIFICATIONS:
   - Key specifications (voltage, pressure, temperature, flow rate, etc.)
   - Dimensions and weight
   - Materials
   - Certifications (CE, UL, etc.)
   - Operating conditions

3. VARIANTS/OPTIONS:
   - List all model variants with their specific features
   - Note differences between models

For PURCHASE ORDERS / INVOICES:
1. HEADER INFORMATION:
   - Document number (PO#, Invoice#, RFQ#)
   - Document date
   - Vendor/Customer name and address
   - Payment terms
   - Due date

2. LINE ITEMS:
   - Part number/SKU
   - Description
   - Quantity
   - Unit price
   - Extended price

3. TOTALS:
   - Subtotal, Tax, Shipping, Total amount

Extraction Rules:
- Use exact values from document
- Note confidence level for unclear fields
- Extract ALL model numbers and part codes you can find
- Preserve part numbers exactly as written

Format your extraction as structured data with clear sections."""

    def __init__(self) -> None:
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Data Extraction Agent",
            )
        return self._agent


class DataValidationAgent:
    """Validate extracted data against the InvenTree inventory."""

    SYSTEM_PROMPT = """You are a data validation specialist with inventory system access.
Your job is to validate extracted document data against the inventory system.

For TECHNICAL SPECIFICATIONS / DATASHEETS:
1. Search for the manufacturer/brand in inventory
2. Search for each model number / part number in inventory
3. Report which parts already exist in the system
4. For parts NOT found, indicate they can be added to the database
5. Provide a summary of what parts should be added

For PURCHASE ORDERS / INVOICES:
1. PART NUMBER VALIDATION:
   - Search inventory for each part number
   - Note any parts not found
   - Suggest corrections for near-matches

2. QUANTITY VALIDATION:
   - Check if quantities are reasonable
   - Flag unusually large orders

3. PRICE VALIDATION:
   - Compare prices to known pricing
   - Flag significant deviations

4. MATHEMATICAL VALIDATION:
   - Verify line totals match qty x price
   - Verify document total matches sum of lines

Report Format:
- Parts found in inventory (with stock levels if available)
- Parts NOT found (candidates for adding to database)
- Summary and recommendations"""

    def __init__(self) -> None:
        self._agent: ChatAgent | None = None

    async def get_agent(self) -> ChatAgent:
        if self._agent is None:
            settings = get_settings()
            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )
            self._agent = ChatAgent(
                chat_client=chat_client,
                instructions=self.SYSTEM_PROMPT,
                name="Data Validation Agent",
            )
        return self._agent


# ===========================================================================
# Conversational-mode system prompt (from v2)
# ===========================================================================

CONVERSATIONAL_SYSTEM_PROMPT = """You are an intelligent document processing assistant for an industrial parts inventory system.

## Your Capabilities:
1. **Read and understand documents** - You can analyze PDFs, datasheets, purchase orders, invoices, quotes, manuals, and other business documents
2. **Answer questions** - You can answer any questions about the document content
3. **Search inventory** - You can search for parts, check stock levels, find suppliers
4. **Add parts to database** - You can create new parts in the inventory system when requested

## Available Tools:
- `search_parts(query)` - Search for parts by name, description, or part number
- `get_part_details(part_id)` - Get detailed info about a specific part
- `get_stock_quantity(part_id)` - Check stock level for a part
- `list_categories()` - List all part categories (needed when creating parts)
- `list_suppliers()` - List available suppliers
- `create_part(...)` - Add a new part to the database

## How to Handle Requests:

### "What is this part / document?"
- Summarize key information from the document
- Identify manufacturer, product line, model numbers
- Describe what the product does and its key specifications

### "Do we have it in stock?"
- Use `search_parts` to look for manufacturer name and model numbers
- Report which parts were found and their stock levels
- Report which parts are NOT in the database

### "Add it to the database"
1. Use `list_categories` to find the appropriate category
2. Use `create_part` with all relevant information from the document

## Response Style:
- Be helpful and conversational
- Provide clear, organized answers
- When adding parts, confirm what was created with the part ID
- If you can't do something, explain why and suggest alternatives

## Document Context:
The document content will be provided below. Use it to answer user questions.
"""


# ===========================================================================
# PDF text-extraction helpers
# ===========================================================================


@dataclass
class _ExtractionOutcome:
    """Internal result of the multi-stage PDF text extraction."""

    text: str
    mode: ExtractionMode
    page_count: int = 0
    table_count: int = 0
    tables_md: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _extract_text_local(pdf_bytes: bytes) -> _ExtractionOutcome:
    """
    Try local extraction: pypdf first, then OCR, then raw heuristic.

    Returns an ``_ExtractionOutcome`` regardless of success - callers
    inspect ``.text`` length to decide whether to escalate.
    """
    warnings: list[str] = []

    # -- Method 1: pypdf ---------------------------------------------------
    try:
        from pypdf import PdfReader

        pdf_file = io.BytesIO(pdf_bytes)
        reader = PdfReader(pdf_file)

        text_parts: list[str] = []
        for page_num, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {page_num + 1} ---\n{page_text}")
            except Exception as exc:
                logger.debug("pypdf: page %d failed: %s", page_num + 1, exc)

        if text_parts:
            extracted = "\n\n".join(text_parts)
            logger.info(
                "pypdf extracted %d chars from %d pages",
                len(extracted),
                len(reader.pages),
            )
            if len(extracted.strip()) >= _MIN_TEXT_CHARS:
                return _ExtractionOutcome(
                    text=extracted,
                    mode=ExtractionMode.PYPDF,
                    page_count=len(reader.pages),
                )
            warnings.append(f"pypdf extracted only {len(extracted.strip())} chars; trying OCR")
        else:
            warnings.append("pypdf returned no text")

    except ImportError:
        warnings.append("pypdf not installed")
    except Exception as exc:
        warnings.append(f"pypdf failed: {exc}")

    # -- Method 2: OCR (pdf2image + pytesseract) ---------------------------
    try:
        # Optional OCR dependency; guarded by the ImportError handler below
        import pytesseract  # ty: ignore[unresolved-import]
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(pdf_bytes, dpi=200)
        ocr_parts: list[str] = []

        for page_num, image in enumerate(images, start=1):
            try:
                page_text = pytesseract.image_to_string(image, lang="eng") or ""
            except Exception as exc:
                logger.debug("OCR page %d failed: %s", page_num, exc)
                page_text = ""
            page_text = page_text.strip()
            if page_text:
                ocr_parts.append(f"--- Page {page_num} (OCR) ---\n{page_text}")

        ocr_text = "\n\n".join(ocr_parts).strip()

        if len(ocr_text) >= _MIN_TEXT_CHARS:
            logger.info("OCR extracted %d chars from %d pages", len(ocr_text), len(images))
            return _ExtractionOutcome(
                text=ocr_text,
                mode=ExtractionMode.OCR,
                page_count=len(images),
                warnings=warnings,
            )
        if images:
            warnings.append(f"OCR extracted only {len(ocr_text)} chars from {len(images)} pages")
    except ImportError:
        warnings.append("OCR deps (pdf2image/pytesseract) not available")
    except Exception as exc:
        warnings.append(f"OCR failed: {exc}")

    # -- Method 3: raw heuristic -------------------------------------------
    try:
        raw = pdf_bytes.decode("latin-1", errors="ignore")
        paren_texts = re.findall(r"\(([^)]{2,})\)", raw)
        parts = [t.strip() for t in paren_texts if t.isprintable() and len(t.strip()) > 1]
        combined = re.sub(r"\s+", " ", " ".join(parts)).strip()

        if len(combined) > 50:
            logger.info("Heuristic extraction: %d chars", len(combined))
            return _ExtractionOutcome(
                text=combined,
                mode=ExtractionMode.BASIC_HEURISTIC,
                warnings=warnings,
            )

        matches = re.findall(r"[A-Za-z0-9\s.,;:!?'\"()\-/$%@#&*+=]{10,}", raw)
        if matches:
            fallback = re.sub(r"\s+", " ", " ".join(matches)).strip()
            if len(fallback) > 50:
                return _ExtractionOutcome(
                    text=fallback,
                    mode=ExtractionMode.BASIC_HEURISTIC,
                    warnings=warnings,
                )
    except Exception as exc:
        warnings.append(f"Heuristic extraction failed: {exc}")

    return _ExtractionOutcome(
        text="[PDF content could not be extracted - document may be image-based or encrypted]",
        mode=ExtractionMode.BASIC_HEURISTIC,
        warnings=warnings,
    )


def _extract_text_azure_di(
    pdf_bytes: bytes,
    model_id: str = "prebuilt-layout",
) -> _ExtractionOutcome | None:
    """
    Try Azure Document Intelligence.  Returns ``None`` if the client
    cannot be created (SDK missing, creds missing, etc.).
    """
    try:
        from ai.core.integrations.doc_intelligence import get_doc_intelligence_client

        client = get_doc_intelligence_client()
        if client is None:
            return None

        result = client.analyze_pdf(pdf_bytes, model_id=model_id)
        tables_md = client.extract_tables_as_markdown(pdf_bytes, model_id=model_id)

        text = result.content or ""
        page_count = len(result.pages) if result.pages else 0
        table_count = len(result.tables) if result.tables else 0

        logger.info(
            "Azure DI extracted %d chars, %d pages, %d tables",
            len(text),
            page_count,
            table_count,
        )
        return _ExtractionOutcome(
            text=text,
            mode=ExtractionMode.AZURE_DI,
            page_count=page_count,
            table_count=table_count,
            tables_md=tables_md,
        )
    except Exception as exc:
        logger.warning("Azure DI extraction failed: %s", exc)
        return None


def _looks_table_heavy(text: str) -> bool:
    """
    Heuristic: return ``True`` when the extracted text *looks like* it
    contains significant tabular data that local extractors often mangle.
    """
    keyword_hits = len(_TABLE_KEYWORDS.findall(text))
    separator_hits = len(_TABLE_SEPARATOR_PATTERN.findall(text))
    lines = text.splitlines()
    short_line_ratio = sum(1 for ln in lines if 0 < len(ln.strip()) < 40) / max(len(lines), 1)
    return keyword_hits >= 3 or separator_hits >= 2 or short_line_ratio > 0.55


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> _ExtractionOutcome:
    """
    Smart PDF text extraction with automatic Azure DI escalation.

    Strategy:
        1. Try local extraction (pypdf -> OCR -> heuristic).
        2. Assess quality: if the text is short, low-quality, or
           table-heavy, escalate to Azure Document Intelligence.
        3. If Azure DI is unavailable or also fails, return the best
           local result we have.
    """
    local = _extract_text_local(pdf_bytes)

    text_ok = len(local.text.strip()) >= _MIN_TEXT_CHARS
    table_heavy = text_ok and _looks_table_heavy(local.text)
    needs_escalation = not text_ok or table_heavy

    if needs_escalation:
        reason = "table-heavy content" if table_heavy else "insufficient local text"
        logger.info("Escalating to Azure DI (%s)", reason)
        di_result = _extract_text_azure_di(pdf_bytes)
        if di_result is not None and len(di_result.text.strip()) > len(local.text.strip()):
            di_result.warnings = [*local.warnings, f"Escalated to Azure DI: {reason}"]
            return di_result
        else:
            logger.info("Azure DI unavailable or no improvement; keeping local result")
            local.warnings.append(f"Attempted Azure DI escalation ({reason}) but kept local result")

    return local


# ===========================================================================
# Unified WF6 Document Workflow
# ===========================================================================


class WF6DocumentWorkflow:
    """
    Unified document processing workflow.

    Supports two execution modes:

    * **pipeline** - classify -> extract -> validate -> route  (deterministic)
    * **conversational** - single agent with tools + document context
      (interactive, for DevUI / chat UX)

    The mode can be forced via ``workflow_mode``; if ``None`` (default) the
    workflow auto-selects based on the query and context.

    Usage::

        workflow = WF6DocumentWorkflow()
        result = await workflow.execute(
            query="Process the latest RFQ from supplier XYZ",
            thread_id="thread_123",
        )
    """

    def __init__(
        self,
        doc_intelligence_client: Any | None = None,
        *,
        workflow_mode: WorkflowMode | None = None,
    ) -> None:
        self.classification_agent = DocumentClassificationAgent()
        self.extraction_agent = DataExtractionAgent()
        self.validation_agent = DataValidationAgent()
        self.doc_intelligence = doc_intelligence_client

        self._forced_mode = workflow_mode
        self._conv_agent: ChatAgent | None = None
        self._current_document_content: str = ""

        logger.info(
            "WF6DocumentWorkflow initialised (mode=%s)",
            workflow_mode.value if workflow_mode else "auto",
        )

    # ------------------------------------------------------------------
    # Mode selection
    # ------------------------------------------------------------------

    def _select_mode(self, query: str, context: dict[str, Any]) -> WorkflowMode:
        """Pick pipeline vs conversational based on signals."""
        if self._forced_mode is not None:
            return self._forced_mode

        q = query.lower()
        conversational_cues = [
            # Questions
            "what is",
            "what's",
            "what are",
            "who is",
            "where is",
            "how many",
            "how much",
            "is there",
            "are there",
            "?",
            # Natural asks
            "tell me",
            "show me",
            "give me",
            "can you",
            "could you",
            "i need",
            "i want",
            "let me know",
            "help me",
            "describe",
            "summarize",
            "explain",
            "break down",
            # Inventory chat
            "do we have",
            "in stock",
            "stock level",
            "on hand",
            "look up",
            "lookup",
            "find",
            "search",
            "check",
            # Actions via conversation
            "add it",
            "add this",
            "add part",
            "add the",
            "create",
            "save",
            "put it in",
            "enter it",
        ]
        if any(cue in q for cue in conversational_cues):
            return WorkflowMode.CONVERSATIONAL

        pipeline_cues = [
            # Explicit pipeline verbs
            "process",
            "classify",
            "triage",
            "route",
            "extract",
            "validate",
            "parse",
            "scan",
            # Natural equivalents
            "run through",
            "go through",
            "handle this",
            "take care of",
            "file this",
            "sort this",
            "work through",
            # Document-type triggers
            "rfq",
            "purchase order",
            "invoice",
            "packing list",
            "ship doc",
            "shipping doc",
        ]
        if any(cue in q for cue in pipeline_cues):
            return WorkflowMode.PIPELINE

        return WorkflowMode.CONVERSATIONAL

    # ------------------------------------------------------------------
    # Unified execute()
    # ------------------------------------------------------------------

    async def execute(
        self,
        query: str,
        document_content: str | None = None,
        file_path: str | None = None,
        email_id: str | None = None,
        thread_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> DocumentProcessingResult:
        """Execute the document workflow."""
        start_time = time.perf_counter()
        context = context or {}

        logger.info(
            "WF6 execute (thread=%s, has_content=%s, has_file=%s, "
            "has_email=%s, has_attachments=%s)",
            thread_id,
            document_content is not None,
            file_path is not None,
            email_id is not None,
            "file_attachments" in context,
        )

        content, extraction_outcome = await self._resolve_content(
            document_content=document_content,
            file_path=file_path,
            email_id=email_id,
            query=query,
            context=context,
        )

        mode = self._select_mode(query, context)
        logger.info("Selected workflow mode: %s", mode.value)

        if mode is WorkflowMode.CONVERSATIONAL:
            return await self._execute_conversational(
                query=query,
                content=content,
                extraction_outcome=extraction_outcome,
                thread_id=thread_id,
                start_time=start_time,
            )
        else:
            return await self._execute_pipeline(
                query=query,
                content=content,
                extraction_outcome=extraction_outcome,
                email_id=email_id,
                thread_id=thread_id,
                start_time=start_time,
            )

    # ------------------------------------------------------------------
    # Content resolution (shared by both modes)
    # ------------------------------------------------------------------

    async def _resolve_content(
        self,
        *,
        document_content: str | None,
        file_path: str | None,
        email_id: str | None,
        query: str,
        context: dict[str, Any],
    ) -> tuple[str, _ExtractionOutcome | None]:
        """Resolve document content from the various possible sources."""
        outcome: _ExtractionOutcome | None = None

        # Priority 1: DevUI file attachments (in-memory bytes)
        if context.get("file_attachments"):
            attachments = context["file_attachments"]
            logger.info("Processing %d DevUI attachment(s)", len(attachments))

            for attachment in attachments:
                if not (attachment.get("decoded") and attachment.get("data")):
                    continue
                pdf_bytes: bytes = attachment["data"]
                media_type: str = attachment.get("media_type", "")
                logger.info("Attachment: %s, %d bytes", media_type, len(pdf_bytes))

                if "pdf" in media_type.lower():
                    outcome = extract_text_from_pdf_bytes(pdf_bytes)
                    return outcome.text, outcome
                if "text" in media_type.lower():
                    try:
                        return pdf_bytes.decode("utf-8"), None
                    except Exception:
                        return pdf_bytes.decode("latin-1", errors="ignore"), None

        # Priority 2: Caller-provided text
        if document_content:
            return document_content, None

        # Priority 3: File path on disk
        if file_path:
            path = Path(file_path)
            if path.suffix.lower() in {".txt", ".md", ".csv"}:
                return path.read_text(), None
            if path.suffix.lower() == ".pdf":
                outcome = extract_text_from_pdf_bytes(path.read_bytes())
                return outcome.text, outcome
            return f"[Binary file: {path.name}]", None

        # Priority 4: Email
        if email_id:
            return await self._get_email_content(email_id), None

        # Priority 5: Fallback to query itself
        logger.warning("No document content found - using query as content")
        return query, None

    # ------------------------------------------------------------------
    # Conversational execution (from v2)
    # ------------------------------------------------------------------

    async def _execute_conversational(
        self,
        *,
        query: str,
        content: str,
        extraction_outcome: _ExtractionOutcome | None,
        thread_id: str,
        start_time: float,
    ) -> DocumentProcessingResult:
        """Run the single-agent conversational path."""
        try:
            agent = await self._get_conversational_agent(content)
            response = await run_with_rbac(agent, query, full_tools=INVENTORY_TOOLS)

            response_text = self._extract_response_text(response)
            execution_time = (time.perf_counter() - start_time) * 1000

            logger.info("Conversational WF6 complete in %.0f ms", execution_time)

            extraction = DocumentExtractionResult()
            if extraction_outcome:
                extraction.extraction_mode = extraction_outcome.mode
                extraction.azure_di_tables = extraction_outcome.tables_md
            extraction.status = ProcessingStatus.PROCESSED

            return DocumentProcessingResult(
                success=True,
                extraction=extraction,
                formatted_response=response_text,
                execution_time_ms=execution_time,
            )
        except Exception as exc:
            execution_time = (time.perf_counter() - start_time) * 1000
            logger.error("Conversational WF6 failed: %s", exc)
            return DocumentProcessingResult(
                success=False,
                error=str(exc),
                formatted_response=f"Document processing failed: {exc}",
                execution_time_ms=execution_time,
            )

    async def _get_conversational_agent(self, document_content: str = "") -> ChatAgent:
        """Create a conversational agent with document context baked in."""
        settings = get_settings()

        if document_content:
            system_prompt = (
                f"{CONVERSATIONAL_SYSTEM_PROMPT}\n\n---\n"
                f"## DOCUMENT CONTENT:\n---\n{document_content}\n---\n"
            )
        else:
            system_prompt = CONVERSATIONAL_SYSTEM_PROMPT

        chat_client = AzureOpenAIChatClient(
            deployment_name=settings.azure_openai_deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )
        # Tools-less: run_with_rbac supplies the per-user-filtered toolset.
        return ChatAgent(
            chat_client=chat_client,
            instructions=system_prompt,
            name="Document Processing Agent",
        )

    # ------------------------------------------------------------------
    # Pipeline execution (from v1)
    # ------------------------------------------------------------------

    async def _execute_pipeline(
        self,
        *,
        query: str,
        content: str,
        extraction_outcome: _ExtractionOutcome | None,
        email_id: str | None,
        thread_id: str,
        start_time: float,
    ) -> DocumentProcessingResult:
        """Run the staged pipeline path."""
        extraction = DocumentExtractionResult()
        if extraction_outcome:
            extraction.extraction_mode = extraction_outcome.mode
            extraction.azure_di_tables = extraction_outcome.tables_md
        if email_id:
            extraction.source_email_id = email_id
        actions: list[str] = []

        try:
            extraction.status = ProcessingStatus.ANALYZING

            classification = await self._classify_document(content)
            extraction.document_type = self._parse_document_type(classification)
            actions.append(f"Classified document as: {extraction.document_type.value}")

            extracted_data = await self._extract_data(content, extraction.document_type)
            extraction.status = ProcessingStatus.EXTRACTED
            self._parse_extraction(extracted_data, extraction)
            actions.append(
                f"Extracted {len(extraction.fields)} fields and "
                f"{len(extraction.line_items)} line items"
            )

            validation = await self._validate_data(extraction, extracted_data)
            extraction.status = ProcessingStatus.VALIDATED
            self._parse_validation(validation, extraction)
            actions.append(
                f"Validation: {len(extraction.validation_errors)} errors, "
                f"{len(extraction.validation_warnings)} warnings"
            )

            routing_action = await self._route_document(extraction)
            actions.append(routing_action)
            extraction.status = ProcessingStatus.PROCESSED

            execution_time = (time.perf_counter() - start_time) * 1000
            logger.info(
                "Pipeline WF6 complete in %.0f ms (doc_type=%s)",
                execution_time,
                extraction.document_type.value,
            )

            return DocumentProcessingResult(
                success=True,
                extraction=extraction,
                actions_taken=actions,
                formatted_response=self._format_response(
                    extraction,
                    actions,
                    classification,
                    extracted_data,
                    validation,
                    query,
                ),
                execution_time_ms=execution_time,
            )

        except Exception as exc:
            execution_time = (time.perf_counter() - start_time) * 1000
            extraction.status = ProcessingStatus.ERROR
            logger.error("Pipeline WF6 failed: %s", exc, extra={"thread_id": thread_id})
            return DocumentProcessingResult(
                success=False,
                extraction=extraction,
                actions_taken=actions,
                error=str(exc),
                formatted_response=f"Document processing failed: {exc}",
                execution_time_ms=execution_time,
            )

    # ------------------------------------------------------------------
    # Pipeline helper methods
    # ------------------------------------------------------------------

    async def _get_email_content(self, email_id: str) -> str:
        return f"[Email content for ID: {email_id}]"

    async def _classify_document(self, content: str) -> str:
        agent = await self.classification_agent.get_agent()
        response = await agent.run(f"Classify this document:\n\n{content[:2000]}")
        return self._extract_response_text(response)

    async def _extract_data(self, content: str, doc_type: DocumentType) -> str:
        agent = await self.extraction_agent.get_agent()
        prompt = (
            f"Document Type: {doc_type.value}\n\n"
            f"Extract structured data from this document:\n\n{content[:3000]}"
        )
        response = await agent.run(prompt)
        return self._extract_response_text(response)

    async def _validate_data(
        self, extraction: DocumentExtractionResult, extracted_data: str = ""
    ) -> str:
        agent = await self.validation_agent.get_agent()

        if extraction.document_type == DocumentType.TECHNICAL_SPEC:
            prompt = (
                f"Check our inventory for these products from the datasheet:\n\n"
                f"{extracted_data}\n\n"
                "Tasks:\n1. Search for the manufacturer/brand name\n"
                "2. Search for any model numbers or part numbers mentioned\n"
                "3. Check stock levels for any parts found\n"
                "4. Report which parts exist and which are NOT in our system\n\n"
                "Use the inventory search tools to check our database."
            )
        elif extraction.document_type == DocumentType.MANUAL:
            prompt = (
                f"Check our inventory for products from this manual:\n\n"
                f"{extracted_data}\n\n"
                "Search for the manufacturer and any model/part numbers mentioned.\n"
                "Report what we have in inventory and what's missing."
            )
        else:
            items_summary = "\n".join(
                f"- {item.part_number}: {item.description} (Qty: {item.quantity})"
                for item in extraction.line_items[:20]
            )
            prompt = (
                f"Validate these extracted items against inventory:\n\n"
                f"Document Type: {extraction.document_type.value}\n"
                f"Vendor: {extraction.vendor_name}\n\n"
                f"Line Items:\n{items_summary}\n\n"
                "Search for each part number in inventory and validate."
            )

        response = await run_with_rbac(agent, prompt, full_tools=INVENTORY_TOOLS)
        return self._extract_response_text(response)

    async def _route_document(self, extraction: DocumentExtractionResult) -> str:
        routing = {
            DocumentType.REQUEST_FOR_QUOTE: "Routed to Sales team for quote preparation",
            DocumentType.PURCHASE_ORDER: "Routed to Order Processing for fulfillment",
            DocumentType.INVOICE: "Routed to Accounts Payable for payment processing",
            DocumentType.TECHNICAL_SPEC: "Product information extracted - ready to add to parts database",
            DocumentType.PACKING_LIST: "Routed to Receiving for verification",
            DocumentType.SHIPPING_DOCUMENT: "Routed to Logistics for tracking",
            DocumentType.DRAWING: "Routed to Engineering for review",
            DocumentType.MANUAL: "Product manual analyzed - information extracted for reference",
            DocumentType.UNKNOWN: "Flagged for manual review",
        }
        return routing.get(extraction.document_type, "Flagged for manual review")

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_document_type(self, classification: str) -> DocumentType:
        lower = classification.lower()
        type_keywords = {
            DocumentType.REQUEST_FOR_QUOTE: ["rfq", "request for quote", "quotation request"],
            DocumentType.PURCHASE_ORDER: ["purchase order", "po", "order"],
            DocumentType.INVOICE: ["invoice", "bill", "payment"],
            DocumentType.TECHNICAL_SPEC: ["specification", "technical", "spec"],
            DocumentType.PACKING_LIST: ["packing list", "packing slip"],
            DocumentType.SHIPPING_DOCUMENT: ["shipping", "delivery", "waybill"],
            DocumentType.DRAWING: ["drawing", "schematic", "diagram"],
            DocumentType.MANUAL: ["manual", "guide", "documentation"],
        }
        for doc_type, keywords in type_keywords.items():
            if any(kw in lower for kw in keywords):
                return doc_type
        return DocumentType.UNKNOWN

    def _parse_extraction(self, extraction_text: str, result: DocumentExtractionResult) -> None:
        doc_num_match = re.search(
            r"(?:PO|Invoice|RFQ)[\s#-]*([A-Z0-9-]+)",
            extraction_text,
            re.IGNORECASE,
        )
        if doc_num_match:
            result.document_number = doc_num_match.group(1)

        vendor_match = re.search(r"(?:Vendor|Supplier|From)[\s:]*([A-Za-z0-9\s]+)", extraction_text)
        if vendor_match:
            result.vendor_name = vendor_match.group(1).strip()

        result.extraction_confidence = 0.75

    def _parse_validation(self, validation_text: str, result: DocumentExtractionResult) -> None:
        for line in validation_text.split("\n"):
            lower = line.lower()
            content = line.strip().lstrip("-*\u2022 ")
            if any(kw in lower for kw in ["error", "invalid", "not found", "missing"]):
                result.validation_errors.append(content)
            elif any(kw in lower for kw in ["warning", "caution", "note"]):
                result.validation_warnings.append(content)

    # ------------------------------------------------------------------
    # Response formatting
    # ------------------------------------------------------------------

    def _format_response(
        self,
        extraction: DocumentExtractionResult,
        actions: list[str],
        classification: str = "",
        extracted_data: str = "",
        validation: str = "",
        original_query: str = "",
    ) -> str:
        mode_badge = f"**Extraction method:** {extraction.extraction_mode.value}"
        tables_section = ""
        if extraction.azure_di_tables:
            tables_section = "\n\n## Extracted Tables (Azure DI)\n" + "\n\n".join(
                extraction.azure_di_tables
            )

        if extraction.document_type == DocumentType.TECHNICAL_SPEC:
            return (
                "# Document Analysis Complete\n\n"
                "## Document Classification\n"
                "**Type:** Technical Specification / Datasheet\n"
                f"{mode_badge}\n\n"
                f"## Extracted Product Information\n{extracted_data}\n"
                f"{tables_section}\n\n"
                f"## Inventory Check\n{validation}\n\n"
                f"## Summary\n"
                f"- **Document ID:** {extraction.document_id}\n"
                f"- **Source:** {extraction.source_filename or 'Uploaded document'}\n\n"
                "---\n*Analyzed by AIMMS Document Workflow*\n"
            )

        if extraction.document_type == DocumentType.MANUAL:
            return (
                "# Document Analysis Complete\n\n"
                "## Document Classification\n"
                "**Type:** Product Manual / Documentation\n"
                f"{mode_badge}\n\n"
                f"## Extracted Information\n{extracted_data}\n"
                f"{tables_section}\n\n"
                f"## Inventory Check\n{validation}\n\n"
                f"## Summary\n"
                f"- **Document ID:** {extraction.document_id}\n"
                f"- **Source:** {extraction.source_filename or 'Uploaded document'}\n\n"
                "---\n*Analyzed by AIMMS Document Workflow*\n"
            )

        return (
            "# Document Processing Complete\n\n"
            "## Document Information\n"
            f"- **Document ID:** {extraction.document_id}\n"
            f"- **Type:** {extraction.document_type.value}\n"
            f"- **Status:** {extraction.status.value}\n"
            f"- **Document Number:** {extraction.document_number or 'Not extracted'}\n"
            f"- **Vendor:** {extraction.vendor_name or 'Not extracted'}\n"
            f"- {mode_badge}\n\n"
            "## Extraction Summary\n"
            f"- **Fields Extracted:** {len(extraction.fields)}\n"
            f"- **Line Items:** {len(extraction.line_items)}\n"
            f"- **Confidence:** {extraction.extraction_confidence:.0%}\n"
            f"\n## Extracted Data\n{extracted_data}\n"
            f"{tables_section}\n\n"
            "## Validation Results\n"
            f"- **Errors:** {len(extraction.validation_errors)}\n"
            f"- **Warnings:** {len(extraction.validation_warnings)}\n\n"
            f"{self._format_validation_issues(extraction)}\n\n"
            "## Actions Taken\n"
            + "\n".join(f"- {a}" for a in actions)
            + "\n\n---\n*Processed by AIMMS Document Workflow*\n"
        )

    def _format_validation_issues(self, extraction: DocumentExtractionResult) -> str:
        output: list[str] = []
        if extraction.validation_errors:
            output.append("### Errors")
            output.extend(f"- {e}" for e in extraction.validation_errors[:5])
        if extraction.validation_warnings:
            output.append("### Warnings")
            output.extend(f"- {w}" for w in extraction.validation_warnings[:5])
        return "\n".join(output) if output else "*No validation issues*"

    # ------------------------------------------------------------------
    # Streaming (pipeline mode)
    # ------------------------------------------------------------------

    async def stream_execute(
        self,
        query: str,
        document_content: str | None = None,
        thread_id: str = "",
    ) -> AsyncIterator[str]:
        yield "**Document Processing**\n\n"

        yield "Step 1: Classifying document...\n"
        content = document_content or query
        classification = await self._classify_document(content)
        yield f"Classification: {classification}\n"

        yield "\nStep 2: Extracting data...\n"
        doc_type = self._parse_document_type(classification)
        extracted = await self._extract_data(content, doc_type)
        yield f"{extracted[:500]}...\n"

        yield "\nStep 3: Validating...\n"
        yield "\n---\nProcessing complete.\n"

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        """Pull the text out of the last agent response message."""
        if not response.messages:
            return ""
        last = response.messages[-1]
        if hasattr(last, "text"):
            return last.text
        if hasattr(last, "contents"):
            return "".join(c.text for c in last.contents if hasattr(c, "text"))
        return str(last)


# ===========================================================================
# Builder (backward-compatible)
# ===========================================================================


class WF6DocumentBuilder:
    """Builder for WF6 Document Workflow (supports ``.as_agent()``)."""

    def __init__(self) -> None:
        self._doc_intelligence: Any | None = None
        self._custom_extractors: dict[DocumentType, Any] = {}
        self._mode: WorkflowMode | None = None

    def with_doc_intelligence(self, client: Any) -> WF6DocumentBuilder:
        self._doc_intelligence = client
        return self

    def with_mode(self, mode: WorkflowMode) -> WF6DocumentBuilder:
        self._mode = mode
        return self

    def with_custom_extractor(self, doc_type: DocumentType, extractor: Any) -> WF6DocumentBuilder:
        self._custom_extractors[doc_type] = extractor
        return self

    def build(self) -> WF6DocumentWorkflow:
        return WF6DocumentWorkflow(
            doc_intelligence_client=self._doc_intelligence,
            workflow_mode=self._mode,
        )

    def as_agent(self) -> ChatAgent:
        settings = get_settings()
        combined_prompt = (
            "You are a document processing specialist.\n\n"
            "You handle:\n"
            "- Document classification (RFQ, PO, Invoice, etc.)\n"
            "- Data extraction (fields, line items, totals)\n"
            "- Validation against inventory\n"
            "- Routing for action\n\n"
            "When processing documents:\n"
            "1. Classify the document type\n"
            "2. Extract all relevant data\n"
            "3. Validate against known data\n"
            "4. Route for appropriate action\n\n"
            "Provide complete processing summary with:\n"
            "- Document type and key identifiers\n"
            "- Extracted data summary\n"
            "- Validation results\n"
            "- Recommended actions"
        )
        all_tools = list(INVENTORY_TOOLS) + list(EMAIL_TOOLS)
        chat_client = AzureOpenAIChatClient(
            deployment_name=settings.azure_openai_deployment,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
        )
        return ChatAgent(
            chat_client=chat_client,
            instructions=combined_prompt,
            name="AIMMS Document Agent",
            description="Document processing and analysis",
            tools=all_tools,
        )


# ===========================================================================
# Factory functions (unchanged public API)
# ===========================================================================


def create_wf6_document_workflow(
    doc_intelligence_client: Any | None = None,
) -> WF6DocumentWorkflow:
    """Create a WF6 document workflow instance."""
    return WF6DocumentWorkflow(doc_intelligence_client=doc_intelligence_client)


def wf6_document_builder() -> WF6DocumentBuilder:
    """Get a WF6 document workflow builder."""
    return WF6DocumentBuilder()
