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
    """Return the pinned deployment via the S37 policy table.

    The table preserves this site's precedence exactly: the Django
    ``AIMMS_CLOSEOUT_EXTRACTION_MODEL`` override, else the fast deployment.
    """
    from ai.core.model_policy import ModelPurpose, select_deployment

    return select_deployment(ModelPurpose.CLOSEOUT_BINDING)


def _complete(
    messages: list[dict],
    *,
    deployment_name: str | None = None,
    provenance: dict[str, str] | None = None,
) -> str:
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
    deployment = deployment_name or _deployment_name()
    response = client.chat.completions.create(
        model=deployment,
        messages=messages,
        temperature=0,
        max_tokens=_MAX_REPLY_TOKENS,
        response_format={"type": "json_object"},
    )
    if provenance is not None:
        provenance["deployment"] = deployment
        provenance["model"] = str(getattr(response, "model", "") or deployment)
        run_id = str(getattr(response, "id", "") or "").strip()
        if run_id:
            provenance["run_id"] = run_id
    # S37: deliberately NOT recorded in the turn usage ledger — this runs in
    # the closeout wizard REST path where no ledger is bound, so a
    # record_usage here would be a silent no-op pretending to count. Listed
    # as known-uncounted in ai/core/usage.py.
    return response.choices[0].message.content or ""


class ExtractionDocument(dict):
    """Schema document carrying trusted out-of-band inference provenance."""

    def __init__(self, document: dict, *, model_provenance: dict[str, str]) -> None:
        super().__init__(document)
        self.model_provenance = dict(model_provenance)


def extract(narrative: str, shape: dict[str, Any]) -> dict:
    """The ``AIMMS_CLOSEOUT_EXTRACTOR`` entry point."""
    deployment = _deployment_name()
    provenance = {"deployment": deployment, "model": deployment}

    def complete(messages: list[dict]) -> str:
        return _complete(
            messages,
            deployment_name=deployment,
            provenance=provenance,
        )

    document = extract_closeout(narrative, shape, complete=complete)
    return ExtractionDocument(document, model_provenance=provenance)
