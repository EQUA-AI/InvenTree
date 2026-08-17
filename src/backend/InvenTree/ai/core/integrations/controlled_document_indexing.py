"""Direct Azure AI Search indexing for Azure Files controlled Markdown sources."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from ai.core.integrations.controlled_document_ingestion import (
    MarkdownChunk,
    build_ingestion_manifest,
    build_search_documents,
    chunk_markdown_sections,
    parse_markdown_sections,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from aichat.models import ControlledDocument


class ControlledDocumentIngestionError(Exception):
    """A bounded source, embedding, or Search projection failure."""

    code = "CONTROLLED_DOCUMENT_INGESTION_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class EmbeddingClient(Protocol):
    """Generate embedding vectors for a bounded batch of chunk text."""

    def embed_batch(self, inputs: list[str]) -> list[list[float]]:
        """Return one embedding vector for every input text."""


class SearchProjection(Protocol):
    """Replace all Search chunks projected from one registry row."""

    def replace_documents(
        self, *, parent_document_key: str, documents: list[dict[str, object]]
    ) -> None:
        """Delete stale chunks and upload the supplied governed projection."""

    def retire_stale_revisions(self, *, document: ControlledDocument) -> None:
        """Clear the current projection flag from superseded Search revisions."""

    def vector_dimensions(self) -> int | None:
        """Return the index's stored vector dimensions, or None if unreadable."""

    def ensure_stamp_fields(self) -> None:
        """Additively ensure the index schema carries the embedding stamp fields."""


@dataclass(frozen=True)
class ControlledDocumentMetadata:
    """Trusted metadata supplied by a server-side ingestion command or service."""

    document_id: str
    revision: str
    title: str
    document_class: str
    scope_key: str
    access_class: str
    source_location: str
    revision_date: date | None = None
    facility: str = ""
    process_area: str = ""
    asset_id: str = ""
    child_asset_id: str = ""
    work_order_id: str = ""
    repair_packet_id: str = ""
    created_by: Any = None
    approved_by: Any = None


@dataclass(frozen=True)
class ControlledDocumentIngestionResult:
    """Non-sensitive audit result of a controlled-document ingestion request."""

    document_id: str
    revision: str
    source_sha256: str
    search_index_name: str
    manifest: dict[str, object]
    already_indexed: bool


class AzureOpenAIEmbeddingClient:
    """Azure OpenAI embedding adapter with managed identity production auth."""

    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        api_version: str,
        api_key: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._deployment = deployment
        self._api_version = api_version
        self._api_key = api_key
        self._client: Any | None = None

    @classmethod
    def from_settings(cls) -> AzureOpenAIEmbeddingClient:
        """Build the adapter from the existing AIMMS Azure OpenAI configuration."""
        from ai.core.config import get_settings

        settings = get_settings()
        if not settings.azure_openai_endpoint or not settings.azure_openai_embedding_deployment:
            raise ControlledDocumentIngestionError(
                "Azure OpenAI embedding configuration is unavailable",
                code="CONTROLLED_DOCUMENT_EMBEDDING_CONFIG_INVALID",
            )
        return cls(
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_embedding_deployment,
            api_version=settings.azure_openai_api_version,
            api_key=settings.azure_openai_api_key,
        )

    def _get_client(self) -> Any:
        """Lazily create a key-backed local client or managed-identity client."""
        if self._client is not None:
            return self._client
        try:
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise ControlledDocumentIngestionError(
                "OpenAI SDK is unavailable", code="CONTROLLED_DOCUMENT_EMBEDDING_UNAVAILABLE"
            ) from exc
        if self._api_key:
            self._client = AzureOpenAI(
                azure_endpoint=self._endpoint,
                api_key=self._api_key,
                api_version=self._api_version,
            )
            return self._client
        try:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise ControlledDocumentIngestionError(
                "Azure Identity SDK is unavailable",
                code="CONTROLLED_DOCUMENT_EMBEDDING_UNAVAILABLE",
            ) from exc
        self._client = AzureOpenAI(
            azure_endpoint=self._endpoint,
            api_version=self._api_version,
            azure_ad_token_provider=get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            ),
        )
        return self._client

    def embed_batch(self, inputs: list[str]) -> list[list[float]]:
        """Embed a bounded input batch without logging any source text."""
        try:
            response = self._get_client().embeddings.create(
                model=self._deployment,
                input=inputs,
            )
            from ai.core.integrations.model_pins import record_resolved_model

            record_resolved_model(self._deployment, str(getattr(response, "model", "") or ""))
            # Order by the SDK's required per-item index — the wire array
            # carries no ordering contract (review finding F-20).
            items = list(response.data)
            if items and all(isinstance(getattr(item, "index", None), int) for item in items):
                items.sort(key=lambda item: item.index)
            return [item.embedding for item in items]
        except ControlledDocumentIngestionError:
            raise
        except Exception as exc:
            raise ControlledDocumentIngestionError(
                "Embedding request failed", code="CONTROLLED_DOCUMENT_EMBEDDING_FAILED"
            ) from exc


class AzureSearchProjection:
    """Azure AI Search projection adapter with managed identity production auth."""

    def __init__(self, *, endpoint: str, index_name: str, api_key: str = "") -> None:
        self._endpoint = endpoint
        self._index_name = index_name
        self._api_key = api_key
        self._client: Any | None = None

    @classmethod
    def from_settings(cls) -> AzureSearchProjection:
        """Build the adapter from the controlled-document Search configuration."""
        from ai.core.config import get_settings

        settings = get_settings()
        if (
            not settings.azure_search_endpoint
            or not settings.azure_search_controlled_documents_index
        ):
            raise ControlledDocumentIngestionError(
                "Controlled-document Search configuration is unavailable",
                code="CONTROLLED_DOCUMENT_SEARCH_CONFIG_INVALID",
            )
        return cls(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_controlled_documents_index,
            api_key=settings.azure_search_api_key,
        )

    def _get_client(self) -> Any:
        """Lazily create a key-backed local client or managed-identity client."""
        if self._client is not None:
            return self._client
        try:
            from azure.core.credentials import AzureKeyCredential
            from azure.search.documents import SearchClient
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise ControlledDocumentIngestionError(
                "Azure Search SDK is unavailable", code="CONTROLLED_DOCUMENT_SEARCH_UNAVAILABLE"
            ) from exc
        credential: Any
        if self._api_key:
            credential = AzureKeyCredential(self._api_key)
        else:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment packaging
                raise ControlledDocumentIngestionError(
                    "Azure Identity SDK is unavailable",
                    code="CONTROLLED_DOCUMENT_SEARCH_UNAVAILABLE",
                ) from exc
            credential = DefaultAzureCredential()
        self._client = SearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=credential,
        )
        return self._client

    def _get_index_client(self) -> Any:
        """Create a schema-level client with the same credential posture."""
        try:
            from azure.core.credentials import AzureKeyCredential
            from azure.search.documents.indexes import SearchIndexClient
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise ControlledDocumentIngestionError(
                "Azure Search SDK is unavailable", code="CONTROLLED_DOCUMENT_SEARCH_UNAVAILABLE"
            ) from exc
        credential: Any
        if self._api_key:
            credential = AzureKeyCredential(self._api_key)
        else:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment packaging
                raise ControlledDocumentIngestionError(
                    "Azure Identity SDK is unavailable",
                    code="CONTROLLED_DOCUMENT_SEARCH_UNAVAILABLE",
                ) from exc
            credential = DefaultAzureCredential()
        return SearchIndexClient(endpoint=self._endpoint, credential=credential)

    def vector_dimensions(self) -> int | None:
        """Return the live index's ``text_vector`` dimensions, or None if unreadable.

        ``None`` means the schema could not be read (typically a data-plane-only
        credential) — callers must treat that as "unknown", never as a match.
        """
        try:
            index = self._get_index_client().get_index(self._index_name)
            for field in index.fields:
                if field.name == "text_vector":
                    return getattr(field, "vector_search_dimensions", None)
            return None
        except Exception:
            return None

    def ensure_stamp_fields(self) -> None:
        """Additively add the S17 embedding stamp fields to the index schema.

        Adding retrievable non-key fields is a safe, non-destructive index
        update. A credential that can upload documents but not update the
        schema fails closed here: ingestion must not silently produce
        unstamped chunks once the stamp is part of the contract.
        """
        try:
            from azure.search.documents.indexes.models import (
                SearchFieldDataType,
                SimpleField,
            )

            index_client = self._get_index_client()
            index = index_client.get_index(self._index_name)
            existing = {field.name for field in index.fields}
            additions = []
            if "embedding_model" not in existing:
                additions.append(
                    SimpleField(
                        name="embedding_model",
                        type=SearchFieldDataType.String,
                        filterable=True,
                    )
                )
            if "embedding_dimensions" not in existing:
                additions.append(
                    SimpleField(
                        name="embedding_dimensions",
                        type=SearchFieldDataType.Int32,
                        filterable=True,
                    )
                )
            if not additions:
                return
            index.fields.extend(additions)
            index_client.create_or_update_index(index)
        except ControlledDocumentIngestionError:
            raise
        except Exception as exc:
            raise ControlledDocumentIngestionError(
                "Search index schema cannot carry the embedding stamp",
                code="CONTROLLED_DOCUMENT_INDEX_STAMP_FAILED",
            ) from exc

    @staticmethod
    def _all_succeeded(results: Any) -> bool:
        """Accept SDK results only when each document operation succeeded."""
        for result in results:
            if isinstance(result, dict):
                succeeded = result.get("succeeded", False)
            else:
                succeeded = getattr(result, "succeeded", False)
            if not succeeded:
                return False
        return True

    def replace_documents(
        self, *, parent_document_key: str, documents: list[dict[str, object]]
    ) -> None:
        """Delete old chunks then upload the complete current projection in batches."""
        client = self._get_client()
        escaped_parent_key = parent_document_key.replace("'", "''")
        try:
            stale = client.search(
                search_text="*",
                filter=f"parent_document_key eq '{escaped_parent_key}'",
                select=["id"],
                top=1000,
            )
            stale_ids = [{"id": row["id"]} for row in stale]
            if stale_ids:
                deleted = client.delete_documents(documents=stale_ids)
                if not self._all_succeeded(deleted):
                    raise ControlledDocumentIngestionError(
                        "Search projection deletion failed",
                        code="CONTROLLED_DOCUMENT_SEARCH_DELETE_FAILED",
                    )
            for start in range(0, len(documents), 100):
                uploaded = client.upload_documents(documents=documents[start : start + 100])
                if not self._all_succeeded(uploaded):
                    raise ControlledDocumentIngestionError(
                        "Search projection upload failed",
                        code="CONTROLLED_DOCUMENT_SEARCH_UPLOAD_FAILED",
                    )
        except ControlledDocumentIngestionError:
            raise
        except Exception as exc:
            raise ControlledDocumentIngestionError(
                "Search projection failed", code="CONTROLLED_DOCUMENT_SEARCH_UPLOAD_FAILED"
            ) from exc

    def retire_stale_revisions(self, *, document: ControlledDocument) -> None:
        """Set is_current false on prior source revisions for the same scope/document."""
        client = self._get_client()
        scope_key = document.scope_key.replace("'", "''")
        document_id = document.document_id.replace("'", "''")
        source_sha256 = document.source_sha256.replace("'", "''")
        try:
            stale = client.search(
                search_text="*",
                filter=(
                    f"scope_key eq '{scope_key}' and "
                    f"document_id eq '{document_id}' and "
                    f"source_sha256 ne '{source_sha256}' and is_current eq true"
                ),
                select=["id"],
                top=1000,
            )
            updates = [{"id": row["id"], "is_current": False} for row in stale]
            if updates:
                merged = client.merge_documents(documents=updates)
                if not self._all_succeeded(merged):
                    raise ControlledDocumentIngestionError(
                        "Search projection retirement failed",
                        code="CONTROLLED_DOCUMENT_SEARCH_RETIRE_FAILED",
                    )
        except ControlledDocumentIngestionError:
            raise
        except Exception as exc:
            raise ControlledDocumentIngestionError(
                "Search projection retirement failed",
                code="CONTROLLED_DOCUMENT_SEARCH_RETIRE_FAILED",
            ) from exc


class ControlledDocumentIndexer:
    """Coordinate exact-byte registration, embedding, Search projection, and publication."""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        search_projection: SearchProjection,
        search_index_name: str,
        embedding_dimensions: int = 3072,
        embedding_batch_size: int = 16,
        embedding_model: str = "",
        allow_model_change: bool = False,
        registry=None,
    ) -> None:
        self.embedding_client = embedding_client
        self.search_projection = search_projection
        self.search_index_name = search_index_name
        self.embedding_dimensions = embedding_dimensions
        self.embedding_batch_size = embedding_batch_size
        self.embedding_model = embedding_model
        # Only the governed re-embed command may ingest with a model that
        # differs from what the current corpus was embedded with.
        self.allow_model_change = allow_model_change
        if registry is None:
            from aichat.services import controlled_documents

            registry = controlled_documents
        self.registry = registry

    @classmethod
    def from_settings(cls, *, allow_model_change: bool = False) -> ControlledDocumentIndexer:
        """Build an indexer using only typed AIMMS configuration values."""
        from ai.core.config import get_settings

        settings = get_settings()
        if not settings.azure_search_controlled_documents_index:
            raise ControlledDocumentIngestionError(
                "Controlled-document Search index is not configured",
                code="CONTROLLED_DOCUMENT_SEARCH_CONFIG_INVALID",
            )
        return cls(
            embedding_client=AzureOpenAIEmbeddingClient.from_settings(),
            search_projection=AzureSearchProjection.from_settings(),
            search_index_name=settings.azure_search_controlled_documents_index,
            embedding_dimensions=settings.controlled_document_embedding_dimensions,
            embedding_model=settings.azure_openai_embedding_deployment,
            allow_model_change=allow_model_change,
        )

    def _refuse_on_drift(self) -> None:
        """Refuse ingestion into an index or corpus embedded differently (S17 A4).

        Two independent checks, each authoritative when it can see:
        * the live index's stored vector dimensions (skipped when the schema is
          unreadable — the per-vector check in ``_embed`` still holds), and
        * the registry stamp of already-current revisions, which must match the
          configured model unless the governed re-embed path acknowledged the
          migration.
        """
        live_dims = self.search_projection.vector_dimensions()
        if live_dims is not None and live_dims != self.embedding_dimensions:
            raise ControlledDocumentIngestionError(
                "Configured embedding dimensions do not match the live index",
                code="CONTROLLED_DOCUMENT_EMBEDDING_DIMENSION_DRIFT",
            )
        if self.allow_model_change or not self.embedding_model:
            return
        stamped = getattr(self.registry, "indexed_embedding_models", None)
        if stamped is None:
            return
        others = set(stamped()) - {"", self.embedding_model}
        if others:
            raise ControlledDocumentIngestionError(
                "Corpus already embedded with a different model; use the governed "
                "re-embed command to migrate",
                code="CONTROLLED_DOCUMENT_EMBEDDING_MODEL_DRIFT",
            )

    def _embed(self, chunks: list[MarkdownChunk]) -> list[list[float]]:
        """Generate and validate exactly one fixed-dimension vector per chunk."""
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), self.embedding_batch_size):
            batch = chunks[start : start + self.embedding_batch_size]
            embedded = self.embedding_client.embed_batch([chunk.text for chunk in batch])
            if len(embedded) != len(batch) or any(
                len(vector) != self.embedding_dimensions for vector in embedded
            ):
                raise ControlledDocumentIngestionError(
                    "Embedding dimensions do not match the controlled Search index",
                    code="CONTROLLED_DOCUMENT_EMBEDDING_DIMENSION_INVALID",
                )
            vectors.extend(embedded)
        return vectors

    def _mark_failed(self, document, error_code: str) -> None:
        """Best-effort failure recording that never replaces the original error."""
        try:
            self.registry.mark_failed(
                scope_key=document.scope_key,
                scope_hash=document.scope_hash,
                document_id=document.document_id,
                revision=document.revision,
                error_code=error_code,
            )
        except Exception:
            logger.warning(
                "Could not record controlled-document indexing failure",
                extra={"error_code": error_code, "document_pk": document.pk},
            )

    def ingest(
        self,
        *,
        source_path: Path,
        metadata: ControlledDocumentMetadata,
        force: bool = False,
    ) -> ControlledDocumentIngestionResult:
        """Ingest one trusted Azure Files Markdown source into the shared Search index.

        ``force`` re-projects an already-indexed revision — the governed
        re-embed path — instead of short-circuiting on the registry state.
        """
        try:
            source_bytes = source_path.read_bytes()
        except OSError as exc:
            raise ControlledDocumentIngestionError(
                "Controlled source cannot be read", code="CONTROLLED_DOCUMENT_SOURCE_UNAVAILABLE"
            ) from exc
        try:
            markdown = source_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ControlledDocumentIngestionError(
                "Controlled source must be UTF-8 Markdown",
                code="CONTROLLED_DOCUMENT_SOURCE_ENCODING_INVALID",
            ) from exc
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        sections = parse_markdown_sections(markdown)
        chunks = chunk_markdown_sections(sections)
        manifest = build_ingestion_manifest(
            source_sha256=source_sha256,
            sections=sections,
            chunks=chunks,
            embedding_model=self.embedding_model,
            embedding_dimensions=self.embedding_dimensions,
        )
        self._refuse_on_drift()
        scope_hash = hashlib.sha256(metadata.scope_key.encode("utf-8")).hexdigest()
        document = self.registry.register_document(
            document_id=metadata.document_id,
            revision=metadata.revision,
            title=metadata.title,
            document_class=metadata.document_class,
            scope_key=metadata.scope_key,
            scope_hash=scope_hash,
            access_class=metadata.access_class,
            source_filename=source_path.name,
            source_location=metadata.source_location,
            source_sha256=source_sha256,
            revision_date=metadata.revision_date,
            facility=metadata.facility,
            process_area=metadata.process_area,
            asset_id=metadata.asset_id,
            child_asset_id=metadata.child_asset_id,
            work_order_id=metadata.work_order_id,
            repair_packet_id=metadata.repair_packet_id,
            created_by=metadata.created_by,
            approved_by=metadata.approved_by,
        )
        started = False
        if document.state == "indexed":
            if not force:
                return ControlledDocumentIngestionResult(
                    document_id=document.document_id,
                    revision=document.revision,
                    source_sha256=document.source_sha256,
                    search_index_name=document.search_index_name,
                    manifest=manifest,
                    already_indexed=True,
                )
            document = self.registry.begin_reindex(
                scope_key=document.scope_key,
                scope_hash=document.scope_hash,
                document_id=document.document_id,
                revision=document.revision,
            )
            started = True

        try:
            if not started:
                document = self.registry.start_indexing(
                    scope_key=document.scope_key,
                    scope_hash=document.scope_hash,
                    document_id=document.document_id,
                    revision=document.revision,
                )
                started = True
            indexed_at = datetime.now(UTC)
            documents = build_search_documents(
                document=document,
                chunks=chunks,
                indexed_at=indexed_at,
                embedding_model=self.embedding_model,
                embedding_dimensions=self.embedding_dimensions,
            )
            for row, vector in zip(documents, self._embed(chunks), strict=True):
                row["text_vector"] = vector
            self.search_projection.ensure_stamp_fields()
            self.search_projection.replace_documents(
                parent_document_key=str(document.pk), documents=documents
            )
            self.search_projection.retire_stale_revisions(document=document)
            document = self.registry.mark_indexed(
                scope_key=document.scope_key,
                scope_hash=document.scope_hash,
                document_id=document.document_id,
                revision=document.revision,
                source_sha256=source_sha256,
                search_index_name=self.search_index_name,
                embedding_model=self.embedding_model,
                embedding_dimensions=self.embedding_dimensions,
            )
        except ControlledDocumentIngestionError as exc:
            if started:
                self._mark_failed(document, exc.code)
            raise
        except Exception as exc:
            error = ControlledDocumentIngestionError(
                "Controlled-document ingestion failed", code="CONTROLLED_DOCUMENT_INGESTION_FAILED"
            )
            if started:
                self._mark_failed(document, error.code)
            raise error from exc

        return ControlledDocumentIngestionResult(
            document_id=document.document_id,
            revision=document.revision,
            source_sha256=source_sha256,
            search_index_name=self.search_index_name,
            manifest=manifest,
            already_indexed=False,
        )
