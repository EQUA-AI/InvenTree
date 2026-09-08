"""The ONE place an Azure OpenAI client is constructed (M1 PR A, plan §9.3).

Every rail used to build its own ``AzureOpenAIChatClient`` from the same
three settings. Centralising it gives the keyless seam (Q23: managed
identity instead of ``api_key``) a single edit point, and lets the function
invocation limits ride the spec instead of post-construction pokes.

M2 PR 7 (plan §8.7 managed-identity block; GR-23) extends the same seam to
the raw SDK client: :func:`build_openai_client` returns an
``openai.AzureOpenAI`` that authenticates with ``DefaultAzureCredential``
when ``AIMMS_OPENAI_KEYLESS`` is on and with the API key otherwise. The
compaction summarizer and ``compaction_model_probe`` are its first adopters;
the agent-framework client keeps the key until its own keyless step.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

#: The token audience for Azure OpenAI data-plane calls under Entra auth.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"

#: Process-cached bearer-token provider over ONE ``DefaultAzureCredential``
#: (plan §8.7 MI block: "process-cached credential", the pattern lifted from
#: ``controlled_document_indexing`` / ``embeddings_cohere``). The credential's
#: chain probe and managed-identity round-trip run once per worker process;
#: the token cache lives inside the credential, so sharing it is what makes
#: the cache useful across compaction attempts and tasks.
_TOKEN_PROVIDER: Callable[[], str] | None = None
_TOKEN_PROVIDER_LOCK = threading.Lock()


def _keyless_token_provider() -> Callable[[], str]:
    """Return the process-wide token provider, building it on first use.

    ``azure.identity`` is imported here, not at module import, so key-mode
    processes never load it and tests can stand in a fake module.
    """
    global _TOKEN_PROVIDER
    if _TOKEN_PROVIDER is None:
        with _TOKEN_PROVIDER_LOCK:
            if _TOKEN_PROVIDER is None:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider

                _TOKEN_PROVIDER = get_bearer_token_provider(
                    DefaultAzureCredential(), COGNITIVE_SERVICES_SCOPE
                )
    return _TOKEN_PROVIDER


def reset_token_provider_cache() -> None:
    """Drop the cached credential so the next keyless client builds a fresh one.

    Test hook: suites that stand in a fake ``azure.identity`` must call this
    in teardown or the fake provider would leak into later keyless tests.
    """
    global _TOKEN_PROVIDER
    with _TOKEN_PROVIDER_LOCK:
        _TOKEN_PROVIDER = None


def build_chat_client(
    deployment: str,
    *,
    max_iterations: int | None = None,
    include_detailed_errors: bool | None = None,
) -> Any:
    """Return a chat client for ``deployment`` with the invocation limits applied.

    ``max_iterations`` bounds the tool loop; ``include_detailed_errors``
    False keeps provider error text out of model-visible tool results.
    Either left ``None`` keeps the SDK default.
    """
    settings = get_settings()
    client = AzureOpenAIChatClient(
        deployment_name=deployment,
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
    )
    config = getattr(client, "function_invocation_config", None)
    if config is not None:
        if max_iterations is not None:
            config.max_iterations = max_iterations
        if include_detailed_errors is not None:
            config.include_detailed_errors = include_detailed_errors
    return client


def client_auth_mode(settings: Any | None = None) -> str:
    """``"keyless"`` when the raw client uses managed identity, else ``"key"``.

    Value-free by construction (an enum word, never the key) so probes and
    reports can print it.
    """
    settings = settings if settings is not None else get_settings()
    return "keyless" if bool(getattr(settings, "aimms_openai_keyless", False)) else "key"


def build_openai_client(*, settings: Any | None = None) -> Any:
    """Return a raw ``openai.AzureOpenAI`` client for the configured endpoint.

    Keyless mode (``AIMMS_OPENAI_KEYLESS=1``) passes the process-cached
    ``azure_ad_token_provider`` (one ``DefaultAzureCredential`` over
    :data:`COGNITIVE_SERVICES_SCOPE`, shared by every client this process
    builds) and NO ``api_key``; key mode passes ``azure_openai_api_key``.
    The OpenAI import is deferred to the call so test doubles patched at
    ``openai.AzureOpenAI`` keep working; key-mode callers never touch
    ``azure.identity``.
    """
    from openai import AzureOpenAI

    settings = settings if settings is not None else get_settings()
    kwargs: dict[str, Any] = {
        "azure_endpoint": settings.azure_openai_endpoint,
        "api_version": settings.azure_openai_api_version,
    }
    if client_auth_mode(settings) == "keyless":
        kwargs["azure_ad_token_provider"] = _keyless_token_provider()
    else:
        kwargs["api_key"] = settings.azure_openai_api_key
    return AzureOpenAI(**kwargs)


__all__ = [
    "COGNITIVE_SERVICES_SCOPE",
    "build_chat_client",
    "build_openai_client",
    "client_auth_mode",
    "reset_token_provider_cache",
]
