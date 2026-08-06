"""Fail-closed model and embedding pin verification (S17 A4/A10).

Two planes, one authority:

* **Embedding plane (A4).** The controlled-document index stores vectors of a
  fixed dimension produced by one embedding model. Nothing structural stopped
  the configured deployment from drifting away from the index — the mismatch
  surfaced per query, at search time, as an opaque retrieval failure. The boot
  probe embeds one known string and refuses to start a process whose live
  embedding output cannot be stored in the index it is configured to search.
* **Chat plane (A10).** Deployment names are aliases; the model behind one can
  be changed in the portal without any code signal. The resolved model identity
  reported by the provider is recorded here and stamped onto every terminal
  turn, and an optional boot probe asserts it against an explicit pin.

The probes are guarded by their own settings so a deployment can always boot
past a broken probe with one env flip — the kill switch is the rollback.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ai.core.config import Settings

#: Fixed probe input: never user content, stable across boots so the embedding
#: call is cacheable provider-side.
_PROBE_TEXT = "AIMMS embedding dimension boot probe"


class ModelPinError(Exception):
    """A model or embedding pin violation that must abort startup."""

    code = "MODEL_PIN_VIOLATION"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


_LOCK = threading.Lock()
_RESOLVED: dict[str, str] = {}


def record_resolved_model(deployment: str, model: str) -> None:
    """Record the provider-reported model identity behind a deployment alias.

    First resolution logs at INFO; a mid-process change logs at WARNING because
    it means the deployment was remapped underneath a running service.
    """
    if not deployment or not model:
        return
    with _LOCK:
        previous = _RESOLVED.get(deployment)
        if previous == model:
            return
        _RESOLVED[deployment] = model
    if previous is None:
        logger.info("model.resolved deployment=%s model=%s", deployment, model)
    else:
        logger.warning(
            "model.resolved CHANGED deployment=%s model=%s previous=%s",
            deployment,
            model,
            previous,
        )


def resolved_model_versions() -> dict[str, str]:
    """Return a copy of every deployment→model resolution seen this process."""
    with _LOCK:
        return dict(_RESOLVED)


def _reset_resolved_models() -> None:
    """Test hook: clear the process-level resolution registry."""
    with _LOCK:
        _RESOLVED.clear()


def _default_index_dimensions_reader(settings: Settings) -> int | None:
    """Read ``text_vector`` dimensions from the live controlled-document index.

    Returns ``None`` when the schema cannot be read (the runtime identity may
    hold data-plane query rights only) — the caller degrades to a loud skip,
    because an unreadable schema is a permissions posture, not evidence of
    drift.
    """
    try:
        from ai.core.integrations.controlled_document_indexing import (
            AzureSearchProjection,
        )

        return AzureSearchProjection.from_settings().vector_dimensions()
    except Exception as exc:
        logger.warning(
            "model-pin index schema unreadable (%s); index dimension check skipped",
            type(exc).__name__,
        )
        return None


def _probe_chat_deployment(settings: Settings, deployment: str, expected: str) -> None:
    """Resolve one chat deployment with a minimal call and assert its pin."""
    from openai import AzureOpenAI

    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )
    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": "ping"}],
        max_completion_tokens=1,
    )
    resolved = str(getattr(response, "model", "") or "")
    record_resolved_model(deployment, resolved)
    if expected and resolved and resolved != expected:
        raise ModelPinError(
            f"Deployment {deployment!r} resolves to {resolved!r}, pinned to {expected!r}",
            code="CHAT_MODEL_PIN_MISMATCH",
        )


def run_boot_probes(
    *,
    settings: Settings | None = None,
    embedding_client_factory: Callable[[], Any] | None = None,
    index_dimensions_reader: Callable[[Settings], int | None] | None = None,
    chat_prober: Callable[[Settings, str, str], None] | None = None,
) -> dict[str, str]:
    """Run the S17 startup probes and return a bounded outcome report.

    Every skip is loud (WARNING log + report entry) so a dark plane is always
    distinguishable from a verified one. A genuine pin violation raises
    :class:`ModelPinError`, which the caller must let abort startup.
    """
    if settings is None:
        from ai.core.config import get_settings

        settings = get_settings()
    report: dict[str, str] = {"embedding": "skipped", "index": "skipped", "chat": "skipped"}

    if not settings.embedding_boot_probe_enabled:
        report["embedding"] = "disabled"
        logger.warning("model-pin boot probe disabled by configuration")
    elif not settings.azure_openai_endpoint or not settings.azure_openai_embedding_deployment:
        logger.warning("model-pin boot probe skipped: Azure OpenAI embedding plane unconfigured")
    elif not settings.azure_search_controlled_documents_index:
        # Known dev posture: the retrieval plane is dark, so a hard probe here
        # would refuse a boot that serves no manuals traffic at all.
        logger.warning("model-pin boot probe skipped: controlled-document index unconfigured")
    else:
        if embedding_client_factory is None:
            from ai.core.integrations.controlled_document_indexing import (
                AzureOpenAIEmbeddingClient,
            )

            embedding_client_factory = AzureOpenAIEmbeddingClient.from_settings
        try:
            vectors = embedding_client_factory().embed_batch([_PROBE_TEXT])
        except ModelPinError:
            raise
        except Exception as exc:
            raise ModelPinError(
                "Embedding boot probe could not reach the configured deployment",
                code="EMBEDDING_PROBE_UNREACHABLE",
            ) from exc
        expected_dims = settings.controlled_document_embedding_dimensions
        if len(vectors) != 1 or len(vectors[0]) != expected_dims:
            observed = len(vectors[0]) if vectors else 0
            raise ModelPinError(
                f"Embedding deployment {settings.azure_openai_embedding_deployment!r} "
                f"produced {observed}-dimension vectors; the controlled index is "
                f"configured for {expected_dims}",
                code="EMBEDDING_DIMENSION_DRIFT",
            )
        expected_model = settings.azure_openai_expected_embedding_model
        resolved = resolved_model_versions().get(settings.azure_openai_embedding_deployment, "")
        if expected_model and resolved and resolved != expected_model:
            raise ModelPinError(
                f"Embedding deployment resolves to {resolved!r}, pinned to {expected_model!r}",
                code="EMBEDDING_MODEL_PIN_MISMATCH",
            )
        report["embedding"] = "verified"

        reader = index_dimensions_reader or _default_index_dimensions_reader
        live_dims = reader(settings)
        if live_dims is None:
            report["index"] = "unreadable"
        elif live_dims != expected_dims:
            raise ModelPinError(
                f"Live index stores {live_dims}-dimension vectors; configuration "
                f"expects {expected_dims}",
                code="INDEX_DIMENSION_DRIFT",
            )
        else:
            report["index"] = "verified"

    if not settings.model_version_boot_probe_enabled:
        report["chat"] = "disabled"
    elif not settings.azure_openai_endpoint:
        logger.warning("chat model-pin probe skipped: Azure OpenAI plane unconfigured")
    else:
        prober = chat_prober or _probe_chat_deployment
        for deployment, expected in (
            (settings.azure_openai_deployment, settings.azure_openai_expected_model),
            (settings.azure_openai_fast_deployment, settings.azure_openai_expected_fast_model),
        ):
            if not deployment:
                continue
            try:
                prober(settings, deployment, expected)
            except ModelPinError:
                raise
            except Exception as exc:
                raise ModelPinError(
                    f"Chat model probe could not reach deployment {deployment!r}",
                    code="CHAT_PROBE_UNREACHABLE",
                ) from exc
        report["chat"] = "verified"

    logger.info(
        "model-pin boot probe embedding=%s index=%s chat=%s",
        report["embedding"],
        report["index"],
        report["chat"],
    )
    return report


__all__ = [
    "ModelPinError",
    "record_resolved_model",
    "resolved_model_versions",
    "run_boot_probes",
]
