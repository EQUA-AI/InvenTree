"""Exact, scope-filtered Azure AI Search retrieval for a selected document."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Protocol

from ai.core.integrations.controlled_document_indexing import (
    AzureOpenAIEmbeddingClient,
    ControlledDocumentIngestionError,
)
from ai.core.integrations.search_query import semantic_hybrid_kwargs

if TYPE_CHECKING:
    from aichat.models import ControlledDocument


_SELECT_FIELDS = [
    "id",
    "chunk_id",
    "document_id",
    "document_revision",
    "source_sha256",
    "source_file_name",
    "section_id",
    "section_path",
    "chunk",
    "as_of",
    "access_class",
]


class ControlledDocumentSearchError(Exception):
    """A stable controlled-document retrieval failure."""

    code = "CONTROLLED_DOCUMENT_SEARCH_UNAVAILABLE"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class SearchClient(Protocol):
    """Narrow Azure Search operation required for selected-document retrieval."""

    def search(self, **kwargs: Any) -> Any:
        """Run a pre-filtered lexical and vector query."""


class EmbeddingClient(Protocol):
    """Create the query embedding with the index's configured model."""

    def embed_batch(self, inputs: list[str]) -> list[list[float]]:
        """Return one vector for every supplied input."""


def _odata_literal(value: str) -> str:
    """Escape a trusted string as an Azure Search OData literal."""
    return value.replace("'", "''")


def selected_document_filter(document: ControlledDocument) -> str:
    """Build the non-negotiable candidate filter from a registry row only."""
    clauses = {
        "scope_key": document.scope_key,
        "document_id": document.document_id,
        "document_revision": document.revision,
        "source_sha256": document.source_sha256,
    }
    return " and ".join(
        [f"{field} eq '{_odata_literal(value)}'" for field, value in clauses.items()]
        + ["is_current eq true"]
    )


class AzureSelectedDocumentSearch:
    """Lazy Azure AI Search client for the dedicated governed document index."""

    def __init__(self, *, endpoint: str, index_name: str, api_key: str = "") -> None:
        self._endpoint = endpoint
        self._index_name = index_name
        self._api_key = api_key
        self._client: SearchClient | None = None

    @classmethod
    def from_settings(cls) -> AzureSelectedDocumentSearch:
        """Build the client from typed controlled-document settings."""
        from ai.core.config import get_settings

        settings = get_settings()
        if (
            not settings.azure_search_endpoint
            or not settings.azure_search_controlled_documents_index
        ):
            raise ControlledDocumentSearchError(
                "Controlled-document Search configuration is unavailable",
                code="CONTROLLED_DOCUMENT_SEARCH_CONFIG_INVALID",
            )
        return cls(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_controlled_documents_index,
            api_key=settings.azure_search_api_key,
        )

    def client(self) -> SearchClient:
        """Create a managed-identity client in production or key client locally."""
        if self._client is not None:
            return self._client
        try:
            from azure.core.credentials import AzureKeyCredential
            from azure.search.documents import SearchClient as AzureSearchClient
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise ControlledDocumentSearchError(
                "Azure Search SDK is unavailable", code="CONTROLLED_DOCUMENT_SEARCH_UNAVAILABLE"
            ) from exc
        credential: Any
        if self._api_key:
            credential = AzureKeyCredential(self._api_key)
        else:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment packaging
                raise ControlledDocumentSearchError(
                    "Azure Identity SDK is unavailable",
                    code="CONTROLLED_DOCUMENT_SEARCH_UNAVAILABLE",
                ) from exc
            credential = DefaultAzureCredential()
        self._client = AzureSearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=credential,
        )
        return self._client


def _query_vector(*, query: str, embedding_client: EmbeddingClient, dimensions: int) -> list[float]:
    """Generate one dimension-checked query vector without a keyword fallback."""
    try:
        vectors = embedding_client.embed_batch([query])
    except ControlledDocumentIngestionError as exc:
        raise ControlledDocumentSearchError(
            "Controlled-document query embedding failed",
            code="CONTROLLED_DOCUMENT_QUERY_EMBEDDING_FAILED",
        ) from exc
    except Exception as exc:
        raise ControlledDocumentSearchError(
            "Controlled-document query embedding failed",
            code="CONTROLLED_DOCUMENT_QUERY_EMBEDDING_FAILED",
        ) from exc
    if len(vectors) != 1 or len(vectors[0]) != dimensions:
        raise ControlledDocumentSearchError(
            "Controlled-document query embedding dimensions are invalid",
            code="CONTROLLED_DOCUMENT_QUERY_EMBEDDING_DIMENSION_INVALID",
        )
    return vectors[0]


def search_selected_document(
    *,
    document: ControlledDocument,
    query: str,
    top_k: int = 5,
    search_client: SearchClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    embedding_dimensions: int = 3072,
) -> dict[str, object]:
    """Search only an already-authorized selected revision and return citations.

    ``document`` is a server-resolved registry row. There is deliberately no
    argument that accepts document ID, revision, scope, or source hash from the
    model or browser.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise ControlledDocumentSearchError(
            "query is invalid", code="CONTROLLED_DOCUMENT_QUERY_INVALID"
        )
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 5:
        raise ControlledDocumentSearchError(
            "top_k is invalid", code="CONTROLLED_DOCUMENT_QUERY_INVALID"
        )
    if not document.is_current or document.state != "indexed":
        raise ControlledDocumentSearchError(
            "controlled document is unavailable", code="CONTROLLED_DOCUMENT_UNAVAILABLE"
        )

    if embedding_client is None:
        embedding_client = AzureOpenAIEmbeddingClient.from_settings()
    if search_client is None:
        search_client = AzureSelectedDocumentSearch.from_settings().client()
    vector = _query_vector(
        query=query,
        embedding_client=embedding_client,
        dimensions=embedding_dimensions,
    )
    try:
        search_kwargs = semantic_hybrid_kwargs(
            query=query,
            vector=vector,
            vector_field="text_vector",
            filter_expression=selected_document_filter(document),
            select=_SELECT_FIELDS,
            top=top_k,
        )
    except ValueError as exc:
        # The builder's wildcard/blank guard, mapped to the typed refusal.
        raise ControlledDocumentSearchError(
            "query is invalid", code="CONTROLLED_DOCUMENT_QUERY_INVALID"
        ) from exc
    try:
        rows = search_client.search(**search_kwargs)
    except ControlledDocumentSearchError:
        raise
    except Exception as exc:
        raise ControlledDocumentSearchError(
            "Controlled-document Search query failed", code="CONTROLLED_DOCUMENT_SEARCH_FAILED"
        ) from exc

    chunks: list[dict[str, object]] = []
    for row in rows:
        text = str(row.get("chunk") or "")[:8000]
        source_sha256 = str(row.get("source_sha256") or "")
        chunks.append({
            "chunk": text,
            "score": row.get("@search.score", 0),
            "citation": {
                "document_id": str(row.get("document_id") or document.document_id),
                "revision": str(row.get("document_revision") or document.revision),
                "source_sha256_prefix": source_sha256[:12],
                "source_file_name": str(row.get("source_file_name") or document.source_filename),
                "section_id": str(row.get("section_id") or ""),
                "section_path": str(row.get("section_path") or ""),
                "chunk_id": str(row.get("chunk_id") or row.get("id") or ""),
                "as_of": str(row.get("as_of") or ""),
                "authorization_class": str(row.get("access_class") or document.access_class),
                "excerpt_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
        })
    return {
        "document": {
            "selection_id": str(document.selection_id),
            "document_id": document.document_id,
            "revision": document.revision,
            "source_sha256": document.source_sha256,
        },
        "filter": selected_document_filter(document),
        "chunks": chunks,
        "total": len(chunks),
    }
