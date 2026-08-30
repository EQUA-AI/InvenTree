"""Cohere Embed v4 adapter for the attachment-RAG text space (R0).

Serves the auto-ingested attachment corpus only. The governed
controlled-document corpus stays on ``AzureOpenAIEmbeddingClient``
(text-embedding-3-large); the two spaces are never mixed.

Transport (R5 WP-A): raw HTTP against the Foundry Model Inference API, after
``azure-ai-inference`` was retired on 2026-08-26. The wire is frozen exactly
as that SDK sent it -- ``POST {endpoint}/embeddings?api-version=...`` with a
Bearer token -- and was verified bit-identical against it live (maxdiff 0.0)
before the swap, so no stored vector was invalidated and no re-embed was
needed. ``test_cohere_wire_request_matches_the_frozen_sdk_contract`` is what
keeps that true.

Note this endpoint speaks ONLY the Model Inference API: both OpenAI routes
(``/openai/v1/embeddings`` and ``/openai/deployments/{model}/embeddings``)
return 404, so migrating to an OpenAI-shaped client is not available here --
and would in any case drop ``input_type``, whose asymmetry is real and
measured (cos(query, document) = 0.871 on identical text).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Cohere's embed API accepts at most 96 texts per request.
COHERE_BATCH_LIMIT = 96

#: Entra scope for the Foundry data plane (keyless is the deployed posture).
_MI_SCOPE = "https://cognitiveservices.azure.com/.default"

#: Generous enough for a 96-input batch; bounded so a hung provider cannot
#: hold an ingest claim past ``RAG_STALE_CLAIM_S``.
_REQUEST_TIMEOUT_S = 120.0


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
        api_version: str = "2024-05-01-preview",
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._dimensions = dimensions
        self._api_key = api_key
        # The retired SDK hardcoded this; as a setting it is the lever for the
        # real risk here -- Azure retiring the api-version, not the SDK.
        self._api_version = api_version
        self._token_provider: Any | None = None
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
            api_version=settings.cohere_embed_api_version,
        )

    def _auth_header(self) -> str:
        """Bearer value: the configured key, else a refreshing MI token.

        A token *provider* rather than a one-shot token: a full-corpus backfill
        outruns a token lifetime, and the retired SDK refreshed for us.
        """
        if self._api_key:
            return f"Bearer {self._api_key}"
        if self._token_provider is None:
            try:
                from azure.identity import (
                    DefaultAzureCredential,
                    get_bearer_token_provider,
                )
            except ImportError as exc:  # pragma: no cover - deployment packaging
                raise AttachmentEmbeddingError(
                    "Azure Identity SDK is unavailable",
                    code="ATTACHMENT_EMBEDDING_UNAVAILABLE",
                ) from exc
            self._token_provider = get_bearer_token_provider(DefaultAzureCredential(), _MI_SCOPE)
        return f"Bearer {self._token_provider()}"

    def _get_client(self) -> Any:
        """Lazily create the HTTP client bound to the Foundry endpoint."""
        if self._client is not None:
            return self._client
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise AttachmentEmbeddingError(
                "HTTP client library is unavailable",
                code="ATTACHMENT_EMBEDDING_UNAVAILABLE",
            ) from exc
        self._client = httpx.Client(base_url=self._endpoint.rstrip("/"), timeout=_REQUEST_TIMEOUT_S)
        return self._client

    def close(self) -> None:
        """Release the underlying client (it owns a connection pool)."""
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
        if items and all(isinstance(item.get("index"), int) for item in items):
            items.sort(key=lambda item: item["index"])
        return items

    def embed_batch(self, inputs: list[str], *, input_type: str = "document") -> list[list[float]]:
        """Embed inputs in provider-sized sub-batches without logging source text."""
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), COHERE_BATCH_LIMIT):
            chunk = inputs[start : start + COHERE_BATCH_LIMIT]
            try:
                # The wire contract is frozen from azure-ai-inference 1.0.0b9,
                # which this replaced when that SDK was retired (2026-08-26).
                # Verified bit-identical against it live before the swap, so
                # no vector in the corpus is invalidated by this transport.
                http = self._get_client()
                raw = http.post(
                    "/embeddings",
                    params={"api-version": self._api_version},
                    headers={"Authorization": self._auth_header()},
                    json={
                        "input": chunk,
                        "model": self._model,
                        "dimensions": self._dimensions,
                        "input_type": input_type,
                        "encoding_format": "float",
                    },
                )
                if raw.status_code != 200:
                    # Never interpolate the body: provider errors echo the
                    # endpoint and can carry credentials (faults.py convention).
                    raise AttachmentEmbeddingError(
                        "Embedding request failed",
                        code="ATTACHMENT_EMBEDDING_FAILED",
                    )
                response = raw.json()
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

            record_resolved_model(self._model, str(response.get("model") or ""))
            for item in self._ordered_items(response.get("data")):
                vector = item.get("embedding")
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
