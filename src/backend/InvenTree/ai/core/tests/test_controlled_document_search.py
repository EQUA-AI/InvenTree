"""Exact-filter retrieval tests for selected controlled document Search."""

from types import SimpleNamespace

from ai.core.integrations.controlled_document_search import search_selected_document


class _EmbeddingClient:
    def embed_batch(self, inputs):
        assert inputs == ["Why did Pump 2 trip?"]
        return [[0.5] * 3072]


class _SearchClient:
    def __init__(self):
        self.kwargs = None

    def search(self, **kwargs):
        self.kwargs = kwargs
        return [
            {
                "id": "search-key-1",
                "chunk_id": "16:0000",
                "document_id": "aimms-tc-inf-ps1-manual",
                "document_revision": "2.0",
                "source_sha256": "a" * 64,
                "source_file_name": "pump-station-manual.md",
                "section_id": "16",
                "section_path": "Influent Pump Station No. 1 > 16. Diagnostic Reasoning Framework",
                "chunk": "Pump 2 tripped after seal leakage and rising current.",
                "as_of": "2026-07-26",
                "access_class": "maintenance_authorized",
                "@search.score": 2.5,
            }
        ]


def _document():
    return SimpleNamespace(
        selection_id="2b804d8f-1316-4c7c-85bb-fa484f47b1c4",
        document_id="aimms-tc-inf-ps1-manual",
        revision="2.0",
        source_sha256="a" * 64,
        source_filename="pump-station-manual.md",
        scope_key="epcon-experimental",
        access_class="maintenance_authorized",
        is_current=True,
        state="indexed",
    )


def test_selected_document_search_applies_trusted_pre_filter_to_hybrid_query():
    """Search receives only registry-derived filter coordinates and text_vector."""
    client = _SearchClient()
    result = search_selected_document(
        document=_document(),
        query="Why did Pump 2 trip?",
        search_client=client,
        embedding_client=_EmbeddingClient(),
    )

    assert client.kwargs["vector_filter_mode"] == "preFilter"
    assert client.kwargs["vector_queries"][0].fields == "text_vector"
    assert client.kwargs["filter"] == (
        "scope_key eq 'epcon-experimental' and "
        "document_id eq 'aimms-tc-inf-ps1-manual' and "
        "document_revision eq '2.0' and "
        f"source_sha256 eq '{'a' * 64}' and is_current eq true"
    )
    assert result["total"] == 1
    citation = result["chunks"][0]["citation"]
    assert citation["section_id"] == "16"
    assert citation["source_sha256_prefix"] == "a" * 12
    assert citation["authorization_class"] == "maintenance_authorized"
