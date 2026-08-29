"""Document Read Tools.

Read-only tools for extracting usable text from local documents.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ai.core.maf_compat import ai_function

logger = logging.getLogger(__name__)


def _extract_text_pypdf(path: Path) -> tuple[str, int]:
    """Extract text from a PDF using pypdf.

    Returns:
        (text, page_count)
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))

    text_parts: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover
            logger.info("pypdf failed extracting page %s: %s", page_number, exc)
            page_text = ""

        page_text = page_text.strip()
        if page_text:
            text_parts.append(f"--- Page {page_number} ---\n{page_text}")

    return "\n\n".join(text_parts).strip(), len(reader.pages)


def _extract_text_ocr(
    path: Path,
    *,
    dpi: int = 200,
    ocr_language: str = "eng",
) -> tuple[str, int]:
    """Extract text from a PDF by converting pages to images then running OCR.

    Notes:
        - Requires `pdf2image` and a Poppler install on the host.
        - Requires `pytesseract` and the `tesseract` binary on the host.

    Returns:
        (text, page_count)
    """
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:  # pragma: no cover
        raise ImportError("OCR fallback requires pdf2image to be installed.") from exc

    try:
        # Optional OCR dependency; guarded by the ImportError handler below
        import pytesseract  # ty: ignore[unresolved-import]
    except ImportError as exc:  # pragma: no cover
        raise ImportError("OCR fallback requires pytesseract to be installed.") from exc

    try:
        images = convert_from_path(str(path), dpi=dpi)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Failed to convert PDF to images for OCR. "
            "This commonly means Poppler is not installed or the PDF is invalid."
        ) from exc

    text_parts: list[str] = []
    for idx, image in enumerate(images, start=1):
        try:
            page_text = pytesseract.image_to_string(image, lang=ocr_language) or ""
        except Exception as exc:  # pragma: no cover
            logger.info("pytesseract failed on page %s: %s", idx, exc)
            page_text = ""

        page_text = page_text.strip()
        if page_text:
            text_parts.append(f"--- Page {idx} (OCR) ---\n{page_text}")

    return "\n\n".join(text_parts).strip(), len(images)


@ai_function
async def read_pdf_text(  # noqa: RUF029 - ai_function contract is async
    file_path: str,
    *,
    min_text_chars: int = 200,
    enable_ocr_fallback: bool = True,
    ocr_language: str = "eng",
    ocr_dpi: int = 200,
) -> dict[str, object]:
    """Read a PDF and return extracted text.

    The tool attempts native text extraction using `pypdf` first.
    If the extracted text is too short (default threshold: 200 chars), it will
    optionally fall back to OCR using `pdf2image` + `pytesseract`.

    Args:
        file_path: Path to the PDF on the server.
        min_text_chars: Minimum extracted character count to consider the PDF "readable".
        enable_ocr_fallback: If True, run OCR when pypdf extraction yields too little text.
        ocr_language: Tesseract language code (default: 'eng').
        ocr_dpi: DPI to use for PDF->image conversion.

    Returns:
        A dict containing:
        - text: Extracted text
        - extraction_method: 'pypdf' or 'ocr'
        - pages: Page count seen by the extractor
        - warnings: List of warnings / notes
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not path.is_file():
        raise ValueError(f"Not a file: {file_path}")

    if path.suffix.lower() != ".pdf":
        raise ValueError("read_pdf_text only supports .pdf files")

    warnings: list[str] = []

    # 1) Try pypdf
    text, pages = _extract_text_pypdf(path)
    if len(text.strip()) >= max(0, int(min_text_chars)):
        return {
            "text": text,
            "extraction_method": "pypdf",
            "pages": pages,
            "warnings": warnings,
        }

    warnings.append(
        f"pypdf extracted only {len(text.strip())} characters; PDF may be scanned/image-based."
    )

    # 2) OCR fallback
    if not enable_ocr_fallback:
        return {
            "text": text,
            "extraction_method": "pypdf",
            "pages": pages,
            "warnings": warnings,
        }

    ocr_text, ocr_pages = _extract_text_ocr(
        path,
        dpi=ocr_dpi,
        ocr_language=ocr_language,
    )

    if not ocr_text.strip():
        warnings.append("OCR produced no text; PDF may be empty, encrypted, or too low quality.")

    return {
        "text": ocr_text,
        "extraction_method": "ocr",
        "pages": ocr_pages,
        "warnings": warnings,
    }


DOCUMENT_READ_TOOLS = [
    read_pdf_text,
]
