"""Cohere Embed v4 adapter for the attachment-RAG text space (R0).

Serves the auto-ingested attachment corpus only. The governed
controlled-document corpus stays on ``AzureOpenAIEmbeddingClient``
(text-embedding-3-large); the two spaces are never mixed.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Cohere's embed API accepts at most 96 texts per request.
COHERE_BATCH_LIMIT = 96


class AttachmentEmbeddingError(Exception):
    """A bounded attachment-embedding failure with a value-free code."""

    code = "ATTACHMENT_EMBEDDING_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class CohereEmbeddingClient:
    """Azure AI Foundry serverless Embed v4 adapter (key or managed identity).

    Uses the retrieval-tuned asymmetric input types: index-time chunks embed as
    ``document`` and query text embeds as ``query``.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        dimensions: int,
        api_key: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._dimensions = dimensions
        self._api_key = api_key
        self._client: Any | None = None

    @property
    def model(self) -> str:
        """Embedding model pin stamped onto every indexed document."""
        return self._model

    @property
    def dimensions(self) -> int:
        """Configured vector width; a response of any other width is fatal."""
        return self._dimensions

    @classmethod
    def from_settings(cls) -> CohereEmbeddingClient:
        """Build the adapter from the attachment-RAG configuration."""
        from ai.core.config import get_settings

        settings = get_settings()
        if not settings.cohere_embed_endpoint or not settings.cohere_embed_model:
            raise AttachmentEmbeddingError(
                "Cohere embedding configuration is unavailable",
                code="ATTACHMENT_EMBEDDING_CONFIG_INVALID",
            )
        return cls(
            endpoint=settings.cohere_embed_endpoint,
            model=settings.cohere_embed_model,
            dimensions=settings.cohere_embed_dimensions,
            api_key=settings.cohere_embed_key,
        )

    def _get_client(self) -> Any:
        """Lazily create a key-backed local client or managed-identity client."""
        if self._client is not None:
            return self._client
        try:
            from azure.ai.inference import EmbeddingsClient
            from azure.core.credentials import AzureKeyCredential
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise AttachmentEmbeddingError(
                "Azure AI Inference SDK is unavailable",
                code="ATTACHMENT_EMBEDDING_UNAVAILABLE",
            ) from exc
        if self._api_key:
            self._client = EmbeddingsClient(
                endpoint=self._endpoint,
                credential=AzureKeyCredential(self._api_key),
            )
            return self._client
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise AttachmentEmbeddingError(
                "Azure Identity SDK is unavailable",
                code="ATTACHMENT_EMBEDDING_UNAVAILABLE",
            ) from exc
        self._client = EmbeddingsClient(
            endpoint=self._endpoint,
            credential=DefaultAzureCredential(),
            credential_scopes=["https://cognitiveservices.azure.com/.default"],
        )
        return self._client

    def close(self) -> None:
        """Release the underlying SDK client (it owns a connection pool)."""
        import contextlib

        client, self._client = self._client, None
        closer = getattr(client, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                closer()

    @staticmethod
    def _ordered_items(data: Any) -> list[Any]:
        """Order response items by their required ``index`` field.

        The SDK deserializes the wire array as-is with no ordering contract
        (review finding F-20); trusting wire order would silently mis-pair
        every chunk with its vector.
        """
        items = list(data or [])
        if items and all(isinstance(getattr(item, "index", None), int) for item in items):
            items.sort(key=lambda item: item.index)
        return items

    def embed_batch(self, inputs: list[str], *, input_type: str = "document") -> list[list[float]]:
        """Embed inputs in provider-sized sub-batches without logging source text."""
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), COHERE_BATCH_LIMIT):
            chunk = inputs[start : start + COHERE_BATCH_LIMIT]
            try:
                response = self._get_client().embed(
                    input=chunk,
                    model=self._model,
                    dimensions=self._dimensions,
                    input_type=input_type,
                    encoding_format="float",
                )
            except AttachmentEmbeddingError:
                raise
            except Exception as exc:
                # Value-free: provider errors can carry credentials.
                from ai.core.faults import log_fault

                log_fault(
                    logger, "Attachment embedding request failed", exc, stage="attachment_embed"
                )
                raise AttachmentEmbeddingError(
                    "Embedding request failed", code="ATTACHMENT_EMBEDDING_FAILED"
                ) from exc
            from ai.core.integrations.model_pins import record_resolved_model

            record_resolved_model(self._model, str(getattr(response, "model", "") or ""))
            for item in self._ordered_items(response.data):
                vector = getattr(item, "embedding", None)
                if not isinstance(vector, list):
                    raise AttachmentEmbeddingError(
                        "Embedding response is not float-encoded",
                        code="ATTACHMENT_EMBEDDING_MALFORMED",
                    )
                if len(vector) != self._dimensions:
                    raise AttachmentEmbeddingError(
                        "Embedding width disagrees with the configured pin",
                        code="ATTACHMENT_EMBEDDING_DIMENSION_DRIFT",
                    )
                vectors.append([float(value) for value in vector])
        if len(vectors) != len(inputs):
            raise AttachmentEmbeddingError(
                "Embedding response cardinality mismatch",
                code="ATTACHMENT_EMBEDDING_MALFORMED",
            )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Index-time chunk embedding (Cohere ``search_document`` semantics)."""
        return self.embed_batch(texts, input_type="document")

    def embed_query(self, text: str) -> list[float]:
        """Query-time embedding (Cohere ``search_query`` semantics)."""
        return self.embed_batch([text], input_type="query")[0]
