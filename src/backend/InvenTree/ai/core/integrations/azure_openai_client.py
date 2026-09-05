"""The ONE place an Azure OpenAI chat client is constructed (M1 PR A, plan §9.3).

Every rail used to build its own ``AzureOpenAIChatClient`` from the same
three settings. Centralising it gives the keyless seam (Q23: managed
identity instead of ``api_key``) a single edit point, and lets the function
invocation limits ride the spec instead of post-construction pokes.
"""

from __future__ import annotations

from typing import Any

from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings


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


__all__ = ["build_chat_client"]
