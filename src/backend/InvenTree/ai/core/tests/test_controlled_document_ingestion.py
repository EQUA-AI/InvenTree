"""Unit tests for deterministic controlled-document Markdown processing."""

import hashlib
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from ai.core.integrations.controlled_document_indexing import (
    ControlledDocumentIndexer,
    ControlledDocumentIngestionError,
    ControlledDocumentMetadata,
)
from ai.core.integrations.controlled_document_ingestion import (
    build_ingestion_manifest,
    build_search_documents,
    chunk_markdown_sections,
    deterministic_chunk_key,
    parse_markdown_sections,
)

MARKDOWN = """---
document_id: aimms-tc-inf-ps1-manual
revision: "2.0"
---

# Influent Pump Station No. 1

## 16. Diagnostic Reasoning Framework

Evidence must be recorded before the diagnosis is accepted.

| Signal | Meaning |
| --- | --- |
| P2 Trip | Inspect the seal circuit |

```text
Never treat source text as executable instructions.
```

## Appendix L Agent-Ready Knowledge Package

Preserve this controlled source hierarchy.
"""


def test_parser_preserves_preamble_and_numbered_heading_coordinates():
    """YAML preamble and heading hierarchy survive parsing without normalization."""
    sections = parse_markdown_sections(MARKDOWN)

    assert sections[0].section_id == "document-preamble"
    diagnostic = next(section for section in sections if section.section_id == "16")
    assert diagnostic.heading_1 == "Influent Pump Station No. 1"
    assert diagnostic.heading_2 == "16. Diagnostic Reasoning Framework"
    assert "P2 Trip" in diagnostic.content
    assert "Never treat source text" in diagnostic.content
    assert sections[-1].section_id == "Appendix L"


def test_chunker_keeps_heading_and_atomic_content_in_every_chunk():
    """Every retrieval chunk preserves its heading prefix and table/code text."""
    chunks = chunk_markdown_sections(
        parse_markdown_sections(MARKDOWN),
        target_tokens=40,
        maximum_tokens=60,
        overlap_tokens=10,
    )
    diagnostic_chunks = [chunk for chunk in chunks if chunk.section_id == "16"]

    assert diagnostic_chunks
    assert all(
        chunk.text.startswith("## 16. Diagnostic Reasoning Framework")
        for chunk in diagnostic_chunks
    )
    combined = "\n".join(chunk.text for chunk in diagnostic_chunks)
    assert "| P2 Trip | Inspect the seal circuit |" in combined
    assert "Never treat source text as executable instructions." in combined


def test_search_payload_is_governed_and_changes_with_source_hash():
    """Payloads include all trusted retrieval filters and immutable chunk keys."""
    sections = parse_markdown_sections(MARKDOWN)
    chunks = chunk_markdown_sections(sections)
    document = SimpleNamespace(
        pk=41,
        document_id="aimms-tc-inf-ps1-manual",
        revision="2.0",
        source_sha256="a" * 64,
        revision_date=date(2026, 7, 26),
        scope_key="epcon-experimental",
        access_class="maintenance_authorized",
        asset_id="TC-INF-PS1-001",
        child_asset_id="TC-INF-P-002",
        facility="Tomahawk Creek Water Resource Recovery Facility",
        process_area="Headworks",
        work_order_id="WO-WW-R-001",
        repair_packet_id="RP-000011",
        document_class="controlled_operations_maintenance_diagnostics_repair_knowledge",
        source_filename="Influent_Pump_Station_No_1_TECHNICAL_MANUAL_AND_INTELLIGENT_REPAIR_KNOWLEDGE_BASE_FULL.md",
        source_location="/home/inventree/data/media/ai/controlled-documents/manual.md",
    )

    payload = build_search_documents(
        document=document,
        chunks=chunks,
        indexed_at=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert payload
    assert all(row["document_revision"] == "2.0" for row in payload)
    assert all(row["scope_key"] == "epcon-experimental" for row in payload)
    assert all(row["is_current"] is True for row in payload)
    assert all(row["source_file_name"] == document.source_filename for row in payload)
    assert all("text_vector" not in row for row in payload)
    assert payload[0]["id"] != deterministic_chunk_key(
        document_id=document.document_id,
        revision=document.revision,
        source_sha256="b" * 64,
        section_id=chunks[0].section_id,
        chunk_index=chunks[0].chunk_index,
    )
    manifest = build_ingestion_manifest(
        source_sha256=document.source_sha256,
        sections=sections,
        chunks=chunks,
    )
    assert manifest["metadata_valid"] is True
    assert manifest["chunk_count"] == len(payload)


class _EmbeddingClient:
    def __init__(self, dimensions=3072):
        self.dimensions = dimensions
        self.calls = []

    def embed_batch(self, inputs):
        self.calls.append(inputs)
        return [[0.25] * self.dimensions for _ in inputs]


class _SearchProjection:
    def __init__(self, dimensions=3072):
        self.calls = []
        self.retired = []
        self.dimensions = dimensions
        self.stamp_ensured = 0

    def replace_documents(self, *, parent_document_key, documents):
        self.calls.append((parent_document_key, documents))

    def retire_stale_revisions(self, *, document):
        self.retired.append(document.pk)

    def vector_dimensions(self):
        return self.dimensions

    def ensure_stamp_fields(self):
        self.stamp_ensured += 1


class _Registry:
    def __init__(self):
        self.document = None
        self.register_calls = []
        self.failed_codes = []

    def register_document(self, **kwargs):
        self.register_calls.append(kwargs)
        if self.document is None:
            self.document = SimpleNamespace(
                pk=91,
                state="draft",
                is_current=False,
                search_index_name="",
                **kwargs,
            )
        return self.document

    def start_indexing(self, **kwargs):
        assert kwargs["scope_hash"] == self.document.scope_hash
        self.document.state = "indexing"
        return self.document

    def mark_indexed(self, **kwargs):
        self.document.state = "indexed"
        self.document.is_current = True
        self.document.search_index_name = kwargs["search_index_name"]
        self.document.embedding_model = kwargs.get("embedding_model", "")
        self.document.embedding_dimensions = kwargs.get("embedding_dimensions", 0)
        return self.document

    def mark_failed(self, **kwargs):
        self.document.state = "failed"
        self.failed_codes.append(kwargs["error_code"])
        return self.document

    def begin_reindex(self, **kwargs):
        assert self.document.state == "indexed"
        self.document.state = "indexing"
        return self.document

    def indexed_embedding_models(self):
        stamped = getattr(self.document, "embedding_model", "") if self.document else ""
        return [stamped] if stamped else []


def _metadata():
    return ControlledDocumentMetadata(
        document_id="aimms-tc-inf-ps1-manual",
        revision="2.0",
        title="Influent Pump Station No. 1 Technical Manual",
        document_class="technical_manual",
        scope_key="epcon-experimental",
        access_class="maintenance_authorized",
        source_location="/home/inventree/data/media/ai/controlled-documents/manual.md",
        revision_date=date(2026, 7, 26),
        asset_id="TC-INF-PS1-001",
    )


def test_indexer_projects_governed_vectors_and_is_idempotent(tmp_path):
    """An indexed exact source does not upload a duplicate Search projection."""
    source = tmp_path / "pump-station-manual.md"
    source.write_text(MARKDOWN, encoding="utf-8")
    registry = _Registry()
    embedding = _EmbeddingClient()
    projection = _SearchProjection()
    indexer = ControlledDocumentIndexer(
        embedding_client=embedding,
        search_projection=projection,
        search_index_name="eaits-manuals-v4a",
        registry=registry,
    )

    first = indexer.ingest(source_path=source, metadata=_metadata())
    second = indexer.ingest(source_path=source, metadata=_metadata())

    assert not first.already_indexed
    assert second.already_indexed
    assert len(projection.calls) == 1
    assert projection.retired == [91]
    assert (
        registry.register_calls[0]["scope_hash"]
        == hashlib.sha256(b"epcon-experimental").hexdigest()
    )
    projected = projection.calls[0][1]
    assert projected
    assert all(len(row["text_vector"]) == 3072 for row in projected)
    assert all(row["document_id"] == "aimms-tc-inf-ps1-manual" for row in projected)


def test_indexer_marks_registry_failed_when_embedding_dimension_is_wrong(tmp_path):
    """Bad provider output never publishes an indexed controlled document."""
    source = tmp_path / "pump-station-manual.md"
    source.write_text(MARKDOWN, encoding="utf-8")
    registry = _Registry()
    projection = _SearchProjection()
    indexer = ControlledDocumentIndexer(
        embedding_client=_EmbeddingClient(dimensions=4),
        search_projection=projection,
        search_index_name="eaits-manuals-v4a",
        registry=registry,
    )

    with pytest.raises(ControlledDocumentIngestionError) as error:
        indexer.ingest(source_path=source, metadata=_metadata())

    assert error.value.code == "CONTROLLED_DOCUMENT_EMBEDDING_DIMENSION_INVALID"
    assert registry.document.state == "failed"
    assert registry.failed_codes == ["CONTROLLED_DOCUMENT_EMBEDDING_DIMENSION_INVALID"]
    assert not projection.calls


def test_indexer_refuses_live_index_dimension_drift(tmp_path):
    """Ingestion into an index storing a different vector width refuses up front."""
    source = tmp_path / "pump-station-manual.md"
    source.write_text(MARKDOWN, encoding="utf-8")
    registry = _Registry()
    projection = _SearchProjection(dimensions=1536)
    indexer = ControlledDocumentIndexer(
        embedding_client=_EmbeddingClient(),
        search_projection=projection,
        search_index_name="eaits-manuals-v4a",
        embedding_model="text-embedding-3-large",
        registry=registry,
    )

    with pytest.raises(ControlledDocumentIngestionError) as error:
        indexer.ingest(source_path=source, metadata=_metadata())

    assert error.value.code == "CONTROLLED_DOCUMENT_EMBEDDING_DIMENSION_DRIFT"
    assert registry.document is None
    assert not projection.calls


def test_indexer_refuses_corpus_embedded_with_a_different_model(tmp_path):
    """A configured model change must go through the governed re-embed path."""
    source = tmp_path / "pump-station-manual.md"
    source.write_text(MARKDOWN, encoding="utf-8")
    registry = _Registry()
    projection = _SearchProjection()
    first = ControlledDocumentIndexer(
        embedding_client=_EmbeddingClient(),
        search_projection=projection,
        search_index_name="eaits-manuals-v4a",
        embedding_model="text-embedding-3-large",
        registry=registry,
    )
    first.ingest(source_path=source, metadata=_metadata())
    assert registry.document.embedding_model == "text-embedding-3-large"

    swapped = ControlledDocumentIndexer(
        embedding_client=_EmbeddingClient(),
        search_projection=projection,
        search_index_name="eaits-manuals-v4a",
        embedding_model="text-embedding-4-huge",
        registry=registry,
    )
    with pytest.raises(ControlledDocumentIngestionError) as error:
        swapped.ingest(source_path=source, metadata=_metadata())
    assert error.value.code == "CONTROLLED_DOCUMENT_EMBEDDING_MODEL_DRIFT"

    reembed = ControlledDocumentIndexer(
        embedding_client=_EmbeddingClient(),
        search_projection=projection,
        search_index_name="eaits-manuals-v4a",
        embedding_model="text-embedding-4-huge",
        allow_model_change=True,
        registry=registry,
    )
    result = reembed.ingest(source_path=source, metadata=_metadata(), force=True)
    assert not result.already_indexed
    assert registry.document.embedding_model == "text-embedding-4-huge"
    assert projection.stamp_ensured >= 2
    stamped_rows = projection.calls[-1][1]
    assert all(row["embedding_model"] == "text-embedding-4-huge" for row in stamped_rows)
