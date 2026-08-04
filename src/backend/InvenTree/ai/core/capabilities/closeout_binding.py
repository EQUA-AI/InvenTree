"""Deployment binding for the closeout extractor seam (execution-plan S19).

``tasks.services.closeout_extraction.resolve_extractor`` imports the dotted
path in ``AIMMS_CLOSEOUT_EXTRACTOR`` and calls it as ``(narrative, shape)``.
This module is that path: it binds the tool-free extraction capability to one
deployment-pinned chat completion and nothing else. Configure with::

    AIMMS_CLOSEOUT_EXTRACTION_ENABLED=true
    AIMMS_CLOSEOUT_EXTRACTOR=ai.core.capabilities.closeout_binding.extract

Fail-closed by construction: a missing endpoint or credential raises, which
the Django wrapper converts to ``EXTRACTION_UNAVAILABLE`` and a reverted
capture — never a fabricated document.
"""

from __future__ import annotations

from typing import Any

from ai.core.capabilities.closeout_extraction import extract_closeout

_MAX_REPLY_TOKENS = 2000


def _deployment_name() -> str:
    """The pinned model: the Django-plane override, else the fast deployment.

    ``AIMMS_CLOSEOUT_EXTRACTION_MODEL`` is also stamped into each proposal's
    ``model_provenance`` by the Django side, so overriding it changes both the
    call and the recorded provenance together.
    """
    try:
        from django.conf import settings as django_settings

        override = str(
            getattr(django_settings, "AIMMS_CLOSEOUT_EXTRACTION_MODEL", "") or ""
        ).strip()
        if override:
            return override
    except Exception:  # pragma: no cover - Django not configured (island tests)
        pass
    from ai.core.config import get_settings

    return get_settings().azure_openai_fast_deployment


def _complete(messages: list[dict]) -> str:
    """One deterministic, JSON-mode chat completion against the pinned model."""
    from ai.core.config import get_settings
    from openai import AzureOpenAI

    settings = get_settings()
    if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
        raise RuntimeError("closeout extraction has no configured Azure OpenAI plane")
    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )
    response = client.chat.completions.create(
        model=_deployment_name(),
        messages=messages,
        temperature=0,
        max_tokens=_MAX_REPLY_TOKENS,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def extract(narrative: str, shape: dict[str, Any]) -> dict:
    """The ``AIMMS_CLOSEOUT_EXTRACTOR`` entry point."""
    return extract_closeout(narrative, shape, complete=_complete)
