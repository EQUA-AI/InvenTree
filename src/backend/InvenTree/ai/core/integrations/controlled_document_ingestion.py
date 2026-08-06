"""Deterministic Markdown parsing and Search payloads for controlled documents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from aichat.models import ControlledDocument


_HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_NUMERIC_SECTION_RE = re.compile(r"^(?P<section>\d+(?:\.\d+)*)(?:[.)\s:=-]|$)")
_APPENDIX_SECTION_RE = re.compile(r"^(?P<section>appendix\s+[a-z]+(?:\.\d+)?)\b", re.I)
_TOKEN_RE = re.compile(r"\w+(?:['-]\w+)*|[^\s\w]", re.UNICODE)
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class MarkdownSection:
    """One Markdown heading and the content it governs."""

    section_id: str
    section_path: str
    heading_1: str
    heading_2: str
    heading_3: str
    heading_line: str
    content: str


@dataclass(frozen=True)
class MarkdownChunk:
    """A bounded retrieval chunk retaining its source heading coordinates."""

    section_id: str
    section_path: str
    heading_1: str
    heading_2: str
    heading_3: str
    chunk_index: int
    chunk_id: str
    text: str
    token_count: int


def approximate_token_count(text: str) -> int:
    """Return a stable conservative token estimate without a model-specific tokenizer."""
    return len(_TOKEN_RE.findall(text))


def _section_id(title: str) -> str:
    """Derive a stable heading identifier while preserving numbered sections."""
    normalized = title.strip()
    numeric = _NUMERIC_SECTION_RE.match(normalized)
    if numeric:
        return numeric.group("section")
    appendix = _APPENDIX_SECTION_RE.match(normalized)
    if appendix:
        return appendix.group("section").title()
    slug = _NON_SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug or "untitled-section"


def parse_markdown_sections(markdown: str) -> list[MarkdownSection]:
    """Split Markdown by headings and retain its hierarchy and preamble verbatim."""
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("markdown must not be empty")

    sections: list[MarkdownSection] = []
    headings = [""] * 6
    active_level = 0
    active_title = ""
    active_lines: list[str] = []
    preamble: list[str] = []
    seen_ids: dict[str, int] = {}

    def append_section() -> None:
        if not active_title:
            return
        content = "\n".join(active_lines).strip()
        heading_values = headings[:3]
        section_identifier = _section_id(active_title)
        occurrence = seen_ids.get(section_identifier, 0) + 1
        seen_ids[section_identifier] = occurrence
        if occurrence > 1:
            section_identifier = f"{section_identifier}-{occurrence}"
        sections.append(
            MarkdownSection(
                section_id=section_identifier,
                section_path=" > ".join(value for value in headings if value),
                heading_1=heading_values[0],
                heading_2=heading_values[1],
                heading_3=heading_values[2],
                heading_line=f"{'#' * active_level} {active_title}",
                content=content,
            )
        )

    for line in markdown.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            append_section()
            active_level = len(heading.group("level"))
            active_title = heading.group("title").strip()
            active_lines = []
            headings[active_level - 1] = active_title
            for index in range(active_level, len(headings)):
                headings[index] = ""
            continue
        if active_title:
            active_lines.append(line)
        else:
            preamble.append(line)

    append_section()
    preamble_text = "\n".join(preamble).strip()
    if preamble_text:
        sections.insert(
            0,
            MarkdownSection(
                section_id="document-preamble",
                section_path="Document preamble",
                heading_1="",
                heading_2="",
                heading_3="",
                heading_line="",
                content=preamble_text,
            ),
        )
    return sections


def _atomic_blocks(content: str) -> list[str]:
    """Group paragraphs, tables, and fenced code as indivisible chunking units."""
    blocks: list[str] = []
    current: list[str] = []
    fenced = False

    def append_current() -> None:
        text = "\n".join(current).strip()
        if text:
            blocks.append(text)
        current.clear()

    for line in content.splitlines():
        stripped = line.strip()
        is_fence = stripped.startswith("```") or stripped.startswith("~~~")
        if is_fence:
            current.append(line)
            fenced = not fenced
            continue
        if not fenced and not stripped:
            append_current()
            continue
        current.append(line)
    append_current()
    return blocks


def _chunk_text(section: MarkdownSection, blocks: list[str]) -> str:
    """Construct retrieval text with an immutable heading prefix."""
    prefix = section.heading_line
    if not prefix:
        return "\n\n".join(blocks)
    if not blocks:
        return prefix
    return f"{prefix}\n\n{'\n\n'.join(blocks)}"


def _tail_overlap(blocks: list[str], overlap_tokens: int) -> list[str]:
    """Keep whole trailing blocks within the configured overlap budget."""
    if overlap_tokens <= 0:
        return []
    overlap: list[str] = []
    count = 0
    for block in reversed(blocks):
        block_tokens = approximate_token_count(block)
        if overlap and count + block_tokens > overlap_tokens:
            break
        overlap.insert(0, block)
        count += block_tokens
    return overlap


def _markdown_chunk(section: MarkdownSection, blocks: list[str], chunk_index: int) -> MarkdownChunk:
    """Build one immutable chunk from the current section and block set."""
    text = _chunk_text(section, blocks)
    return MarkdownChunk(
        section_id=section.section_id,
        section_path=section.section_path,
        heading_1=section.heading_1,
        heading_2=section.heading_2,
        heading_3=section.heading_3,
        chunk_index=chunk_index,
        chunk_id=f"{section.section_id}:{chunk_index:04d}",
        text=text,
        token_count=approximate_token_count(text),
    )


def chunk_markdown_sections(
    sections: list[MarkdownSection],
    *,
    target_tokens: int = 800,
    maximum_tokens: int = 1000,
    overlap_tokens: int = 100,
) -> list[MarkdownChunk]:
    """Chunk sections at block boundaries while preserving all heading metadata."""
    if not 1 <= overlap_tokens < target_tokens <= maximum_tokens:
        raise ValueError("chunk token limits are invalid")

    chunks: list[MarkdownChunk] = []
    for section in sections:
        blocks = _atomic_blocks(section.content)
        if not blocks:
            blocks = [""]
        chunk_blocks: list[str] = []
        chunk_index = 0

        for block in blocks:
            candidate = [*chunk_blocks, block]
            candidate_tokens = approximate_token_count(_chunk_text(section, candidate))
            if chunk_blocks and candidate_tokens > target_tokens:
                chunks.append(_markdown_chunk(section, chunk_blocks, chunk_index))
                chunk_index += 1
                chunk_blocks = _tail_overlap(chunk_blocks, overlap_tokens)
            chunk_blocks.append(block)
        chunks.append(_markdown_chunk(section, chunk_blocks, chunk_index))
    return chunks


def deterministic_chunk_key(
    *,
    document_id: str,
    revision: str,
    source_sha256: str,
    section_id: str,
    chunk_index: int,
) -> str:
    """Return a Search-key-safe immutable chunk identity."""
    coordinate = "\x1f".join([document_id, revision, source_sha256, section_id, str(chunk_index)])
    return hashlib.sha256(coordinate.encode("utf-8")).hexdigest()


def build_search_documents(
    *,
    document: ControlledDocument,
    chunks: list[MarkdownChunk],
    indexed_at: datetime,
    embedding_model: str = "",
    embedding_dimensions: int = 0,
) -> list[dict[str, object]]:
    """Build governed Search documents before embedding vectors are attached.

    ``embedding_model``/``embedding_dimensions`` stamp which model produced the
    chunk vectors (S17 A4), so a future model migration can tell stale chunks
    from current ones instead of trusting configuration.
    """
    as_of = (
        document.revision_date.isoformat()
        if document.revision_date
        else indexed_at.date().isoformat()
    )
    indexed_at_text = indexed_at.isoformat()
    documents: list[dict[str, object]] = []
    for chunk in chunks:
        documents.append({
            "id": deterministic_chunk_key(
                document_id=document.document_id,
                revision=document.revision,
                source_sha256=document.source_sha256,
                section_id=chunk.section_id,
                chunk_index=chunk.chunk_index,
            ),
            "parent_document_key": str(document.pk),
            "chunk_id": chunk.chunk_id,
            "document_id": document.document_id,
            "document_revision": document.revision,
            "source_sha256": document.source_sha256,
            "is_current": True,
            "scope_key": document.scope_key,
            "access_class": document.access_class,
            "asset_id": document.asset_id,
            "child_asset_id": document.child_asset_id,
            "facility": document.facility,
            "process_area": document.process_area,
            "work_order_id": document.work_order_id,
            "repair_packet_id": document.repair_packet_id,
            "document_class": document.document_class,
            "source_file_name": document.source_filename,
            "source_blob_path": document.source_location,
            "section_id": chunk.section_id,
            "section_path": chunk.section_path,
            "heading_1": chunk.heading_1,
            "heading_2": chunk.heading_2,
            "heading_3": chunk.heading_3,
            "chunk_index": chunk.chunk_index,
            "chunk": chunk.text,
            "token_count": chunk.token_count,
            "as_of": as_of,
            "indexed_at": indexed_at_text,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_dimensions,
        })
    return documents


def build_ingestion_manifest(
    *,
    source_sha256: str,
    sections: list[MarkdownSection],
    chunks: list[MarkdownChunk],
    embedding_model: str = "",
    embedding_dimensions: int = 0,
) -> dict[str, object]:
    """Return bounded audit metadata without retaining source content."""
    return {
        "source_sha256": source_sha256,
        "section_count": len(sections),
        "chunk_count": len(chunks),
        "chunk_ids": [chunk.chunk_id for chunk in chunks],
        "metadata_valid": all(
            bool(chunk.section_id and chunk.section_path and chunk.text) for chunk in chunks
        ),
        "embedding_model": embedding_model,
        "embedding_dimensions": embedding_dimensions,
    }
