"""Gemini Embedding 2 adapter for the media-RAG space (R0).

One unified cross-modal space: text queries, evidence images, and video
segments all map into the same 3072-dim vectors, so text-to-video retrieval
needs no glue model. Never compared against the Cohere text space.

Auth (decision #4, review finding F-16): credentials are loaded explicitly
from ``gcp_credentials_path`` and handed to the client — never via a mutation
of process-global ``GOOGLE_APPLICATION_CREDENTIALS``. The configured
``gcp_auth_mode`` is enforced against the credential file's JSON ``type``
(``wif`` ⇒ ``external_account``, ``sa_key`` ⇒ ``service_account``), so an SA
key can never silently satisfy a WIF-mode deployment.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Retired in R5: this model returns ONE mean-pooled vector per request, and the
# SDK folds a list of strings into a single content, so there is no text batch
# to size. Kept as a named constant only so an old pin cannot silently rebind.
GEMINI_TEXT_BATCH_LIMIT = 1

#: Vertex AI scope for explicitly-loaded credentials (SA keys are unscoped).
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: gcp_auth_mode -> required credential-file JSON ``type``.
_AUTH_MODE_CREDENTIAL_TYPES = {
    "wif": "external_account",
    "sa_key": "service_account",
}


class MediaEmbeddingError(Exception):
    """A bounded media-embedding failure with a value-free code."""

    code = "MEDIA_EMBEDDING_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


def _genai_types() -> Any:
    """Import the SDK types module with one consistent missing-SDK behavior."""
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - deployment packaging
        raise MediaEmbeddingError(
            "google-genai SDK is unavailable",
            code="MEDIA_EMBEDDING_UNAVAILABLE",
        ) from exc
    return types


class GeminiEmbeddingClient:
    """Vertex AI Gemini Embedding 2 adapter (explicit WIF/SA-key credentials)."""

    def __init__(
        self,
        *,
        project_id: str,
        location: str,
        model: str,
        dimensions: int,
        credentials_path: str = "",
        auth_mode: str = "wif",
        task_conditioning: str = "off",
        audio_track_extraction: bool = False,
        auto_truncate: bool | None = None,
    ) -> None:
        self._project_id = project_id
        self._location = location
        self._model = model
        self._dimensions = dimensions
        self._credentials_path = credentials_path
        self._auth_mode = auth_mode
        # R5 (WP-2a/2b). All three default to the R4 behaviour: no task
        # conditioning, no audio fusion, and auto_truncate omitted entirely so
        # the provider default applies. An unset knob must never appear in the
        # request payload -- sending an explicit null is not the same thing.
        self._task_conditioning = task_conditioning
        self._audio_track_extraction = audio_track_extraction
        self._auto_truncate = auto_truncate
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
    def from_settings(cls) -> GeminiEmbeddingClient:
        """Build the adapter from the media-RAG configuration."""
        from ai.core.config import get_settings

        settings = get_settings()
        if not settings.gcp_project_id or not settings.gcp_location:
            raise MediaEmbeddingError(
                "Vertex AI project configuration is unavailable",
                code="MEDIA_EMBEDDING_CONFIG_INVALID",
            )
        if not settings.gemini_embed_model:
            raise MediaEmbeddingError(
                "Gemini embedding model is not pinned",
                code="MEDIA_EMBEDDING_CONFIG_INVALID",
            )
        return cls(
            project_id=settings.gcp_project_id,
            location=settings.gcp_location,
            model=settings.gemini_embed_model,
            dimensions=settings.gemini_embed_dimensions,
            credentials_path=settings.gcp_credentials_path,
            auth_mode=settings.gcp_auth_mode,
            task_conditioning=settings.gemini_embed_task_conditioning,
            audio_track_extraction=settings.gemini_audio_track_extraction,
            auto_truncate=settings.gemini_auto_truncate,
        )

    def _load_credentials(self) -> Any:
        """Load and mode-check explicit credentials; None means ADC fallback."""
        if not self._credentials_path:
            return None
        required_type = _AUTH_MODE_CREDENTIAL_TYPES.get(self._auth_mode)
        try:
            from pathlib import Path

            with Path(self._credentials_path).open(encoding="utf-8") as handle:
                credential_type = json.load(handle).get("type", "")
        except Exception as exc:
            raise MediaEmbeddingError(
                "GCP credential file is unreadable",
                code="MEDIA_EMBEDDING_CONFIG_INVALID",
            ) from exc
        if required_type is not None and credential_type != required_type:
            # wif must be external_account; sa_key must be service_account —
            # a mismatch means the deployment is not what its config claims.
            raise MediaEmbeddingError(
                "GCP credential type disagrees with the configured auth mode",
                code="MEDIA_EMBEDDING_CONFIG_INVALID",
            )
        try:
            import google.auth

            credentials, _project = google.auth.load_credentials_from_file(
                self._credentials_path, scopes=[_CLOUD_PLATFORM_SCOPE]
            )
        except MediaEmbeddingError:
            raise
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise MediaEmbeddingError(
                "google-auth SDK is unavailable",
                code="MEDIA_EMBEDDING_UNAVAILABLE",
            ) from exc
        except Exception as exc:
            raise MediaEmbeddingError(
                "GCP credential loading failed",
                code="MEDIA_EMBEDDING_CONFIG_INVALID",
            ) from exc
        return credentials

    def _get_client(self) -> Any:
        """Lazily create the Vertex-backed google-genai client."""
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - deployment packaging
            raise MediaEmbeddingError(
                "google-genai SDK is unavailable",
                code="MEDIA_EMBEDDING_UNAVAILABLE",
            ) from exc
        credentials = self._load_credentials()
        try:
            self._client = genai.Client(
                vertexai=True,
                project=self._project_id,
                location=self._location,
                credentials=credentials,
            )
        except Exception as exc:
            from ai.core.faults import log_fault

            log_fault(logger, "Vertex AI client construction failed", exc, stage="media_embed")
            raise MediaEmbeddingError(
                "Vertex AI client construction failed",
                code="MEDIA_EMBEDDING_UNAVAILABLE",
            ) from exc
        return self._client

    def close(self) -> None:
        """Release the underlying SDK client when it supports closing."""
        import contextlib

        client, self._client = self._client, None
        closer = getattr(client, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                closer()

    def _config_kwargs(self, *, task_type: str | None, media: bool) -> dict[str, Any]:
        """Build EmbedContentConfig kwargs, omitting every unset knob.

        Omission matters: sending an explicit null is not the same as leaving a
        field out, and the provider's default for ``auto_truncate`` (silent
        truncation) is what R4 shipped. Only keys we deliberately set appear.
        """
        kwargs: dict[str, Any] = {"output_dimensionality": self._dimensions}
        if task_type and self._task_conditioning == "task_type":
            kwargs["task_type"] = task_type
        if media:
            # Vertex-only, and only meaningful for video parts. The config
            # validator refuses this knob on a PREDICT-routed pin, where the
            # SDK would silently drop it and the audio would never be sent.
            if self._audio_track_extraction:
                kwargs["audio_track_extraction"] = True
            if self._auto_truncate is not None:
                kwargs["auto_truncate"] = self._auto_truncate
        return kwargs

    def _conditioned(self, text: str, *, task_type: str | None) -> str:
        """Apply literal-prefix conditioning when that is the configured mode.

        Which of ``task_type`` and ``prefix`` the service actually honours is
        settled by the WP-0a probe, not by the SDK: ``EmbedContentConfig`` will
        send ``taskType`` either way. Both modes are implemented so the probe's
        answer is a config change rather than a code change.
        """
        if self._task_conditioning != "prefix" or not task_type:
            return text
        if task_type == "RETRIEVAL_QUERY":
            return f"task: search result | query: {text}"
        return f"title: none | text: {text}"

    def _embed_contents(
        self, contents: Any, *, task_type: str | None = None, media: bool = False
    ) -> list[list[float]]:
        """Embed one request worth of contents and enforce the dimension pin."""
        types = _genai_types()
        try:
            response = self._get_client().models.embed_content(
                model=self._model,
                contents=contents,
                config=types.EmbedContentConfig(
                    **self._config_kwargs(task_type=task_type, media=media)
                ),
            )
        except MediaEmbeddingError:
            raise
        except Exception as exc:
            # Value-free: provider errors can carry tokens/credentials.
            from ai.core.faults import log_fault

            log_fault(logger, "Media embedding request failed", exc, stage="media_embed")
            raise MediaEmbeddingError(
                "Embedding request failed", code="MEDIA_EMBEDDING_FAILED"
            ) from exc
        embeddings = getattr(response, "embeddings", None) or []
        vectors: list[list[float]] = []
        for embedding in embeddings:
            values = getattr(embedding, "values", None)
            if not values:
                raise MediaEmbeddingError(
                    "Embedding response is empty",
                    code="MEDIA_EMBEDDING_MALFORMED",
                )
            if len(values) != self._dimensions:
                raise MediaEmbeddingError(
                    "Embedding width disagrees with the configured pin",
                    code="MEDIA_EMBEDDING_DIMENSION_DRIFT",
                )
            vectors.append([float(value) for value in values])
        return vectors

    def embed_texts(self, texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
        """Embed text (queries/captions) into the cross-modal space.

        One request per text, deliberately. The SDK's ``t_contents`` folds a
        list of strings into a SINGLE content carrying one part per string, and
        this model returns one mean-pooled vector per content -- so a batched
        call would yield one fused vector for N inputs and trip the cardinality
        check below. Nothing called this with more than one text before R5, so
        the defect was latent rather than live.
        """
        vectors: list[list[float]] = []
        for text in texts:
            vectors.extend(
                self._embed_contents(
                    self._conditioned(text, task_type=task_type), task_type=task_type
                )
            )
        if len(vectors) != len(texts):
            raise MediaEmbeddingError(
                "Embedding response cardinality mismatch",
                code="MEDIA_EMBEDDING_MALFORMED",
            )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a retrieval query; legal against media vectors (unified space)."""
        return self.embed_texts([text], task_type="RETRIEVAL_QUERY")[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed corpus-side text with document-side conditioning."""
        return self.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_image(self, data: bytes, *, mime_type: str) -> list[float]:
        """Embed one image (PNG/JPEG evidence photo or keyframe)."""
        types = _genai_types()

        # Media call shape per Gemini Embedding 2 launch docs; live-validated in R3.
        part = types.Part.from_bytes(data=data, mime_type=mime_type)
        vectors = self._embed_contents(part, task_type="RETRIEVAL_DOCUMENT", media=True)
        if len(vectors) != 1:
            raise MediaEmbeddingError(
                "Image embedding returned unexpected cardinality",
                code="MEDIA_EMBEDDING_MALFORMED",
            )
        return vectors[0]

    def embed_video_segment(self, data: bytes, *, mime_type: str) -> list[float]:
        """Embed one video clip; callers must pre-segment to <= 120 s."""
        types = _genai_types()

        part = types.Part.from_bytes(data=data, mime_type=mime_type)
        vectors = self._embed_contents(part, task_type="RETRIEVAL_DOCUMENT", media=True)
        if len(vectors) != 1:
            raise MediaEmbeddingError(
                "Video embedding returned unexpected cardinality",
                code="MEDIA_EMBEDDING_MALFORMED",
            )
        return vectors[0]
