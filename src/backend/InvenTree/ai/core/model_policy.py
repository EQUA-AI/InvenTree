"""Deterministic model tiering behind one policy table (S37).

Every non-Luna deployment choice in the AI plane goes through
``select_deployment``. The function computes both the legacy inline choice
(byte-for-byte what each call site did before S37) and the policy-table
choice, then applies the rollout ladder:

- ``FEATURE_MODEL_TIERING_SHADOW`` (default on): log any divergence, return
  the legacy choice. The initial policy table IS the identity of legacy
  behavior, so a divergence log line can only mean someone edited the table.
- ``FEATURE_MODEL_TIERING_ENFORCE`` (default off): return the policy choice.
  Flipping it with an identity table is a proven no-op.

The one real policy edit this phase ships dark: text lookup-shaped wf8 turns
on the fast deployment, behind ``FEATURE_WF8_TEXT_FAST_TIER`` — gated on the
S39 golden set passing against the fast deployment.

Luna's reasoning deployment is pinned by ``azure_luna_deployment`` and is
deliberately outside this table.
"""

from __future__ import annotations

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class ModelPurpose(StrEnum):
    """Every distinct reason the AI plane picks a chat deployment."""

    WF8_PRIMARY = "wf8_primary"
    FALLBACK_CLASSIFIER = "fallback_classifier"
    GROUNDING_AUDIT = "grounding_audit"
    CLOSEOUT_BINDING = "closeout_binding"
    SUMMARIZATION = "summarization"
    EXTRACTION = "extraction"
    MEDIA_CAPTION = "media_caption"


#: Purposes that never resolve to the fast tier (D-10 / CR-2). The workers
#: that run ``_summarize`` carry no tiering flag, so the rule must hold on
#: BOTH ladder branches or it would not bind where the call actually runs.
_OVERRIDE_PURPOSES = frozenset({ModelPurpose.SUMMARIZATION, ModelPurpose.EXTRACTION})


def _closeout_override() -> str:
    """The Django-side closeout model override, when configured."""
    try:
        from django.conf import settings as django_settings

        return str(getattr(django_settings, "AIMMS_CLOSEOUT_EXTRACTION_MODEL", "") or "").strip()
    except Exception:
        return ""


def _summarization_override(settings) -> str:
    """The env-backed SUMMARIZATION/EXTRACTION deployment, when configured."""
    return str(getattr(settings, "azure_openai_summarization_deployment", "") or "").strip()


def _summarization_choice(settings) -> str:
    """Override or the standard tier — never fast (D-10)."""
    return _summarization_override(settings) or getattr(settings, "azure_openai_deployment", "")


def _legacy_choice(settings, purpose: ModelPurpose, modality: str) -> str:
    """Exactly what each call site chose before S37."""
    # getattr defaults keep partial test fakes (SimpleNamespace settings)
    # working exactly as they did against the old inline expressions.
    fast = getattr(settings, "azure_openai_fast_deployment", "")
    standard = getattr(settings, "azure_openai_deployment", "")
    if purpose is ModelPurpose.WF8_PRIMARY:
        return fast if modality == "voice" else standard
    if purpose is ModelPurpose.FALLBACK_CLASSIFIER:
        return fast or standard
    if purpose is ModelPurpose.GROUNDING_AUDIT:
        return fast or standard
    if purpose is ModelPurpose.CLOSEOUT_BINDING:
        return _closeout_override() or fast
    if purpose in _OVERRIDE_PURPOSES:
        # D-10: override or standard, never fast. Legacy == policy by
        # construction; the S38 "fast or standard" line routed compaction to
        # the 50K-TPM tier on the workers, where no tiering flag is set (CR-2).
        return _summarization_choice(settings)
    if purpose is ModelPurpose.MEDIA_CAPTION:
        # New in R3; vision needs the full tier, so legacy == policy.
        return standard
    raise ValueError(f"unknown model purpose: {purpose}")  # pragma: no cover


def _policy_choice(settings, purpose: ModelPurpose, modality: str) -> str:
    """The policy table. Initially the identity of legacy behavior, except
    the flag-gated wf8 text fast tier."""
    fast = getattr(settings, "azure_openai_fast_deployment", "")
    standard = getattr(settings, "azure_openai_deployment", "")
    if purpose is ModelPurpose.WF8_PRIMARY:
        if modality == "voice":
            return fast
        # The phase's one real tiering change, dark until the golden set
        # passes on the fast deployment.
        if getattr(settings, "feature_wf8_text_fast_tier", False):
            return fast or standard
        return standard
    if purpose is ModelPurpose.FALLBACK_CLASSIFIER:
        return fast or standard
    if purpose is ModelPurpose.GROUNDING_AUDIT:
        return fast or standard
    if purpose is ModelPurpose.CLOSEOUT_BINDING:
        return _closeout_override() or fast
    if purpose in _OVERRIDE_PURPOSES:
        return _summarization_choice(settings)
    if purpose is ModelPurpose.MEDIA_CAPTION:
        return standard
    raise ValueError(f"unknown model purpose: {purpose}")  # pragma: no cover


def select_deployment(purpose: ModelPurpose, *, modality: str = "text") -> str:
    """The deployment name for one purpose, through the rollout ladder."""
    from ai.core.config import get_settings

    settings = get_settings()
    legacy = _legacy_choice(settings, purpose, modality)
    shadow = bool(getattr(settings, "feature_model_tiering_shadow", False))
    enforce = bool(getattr(settings, "feature_model_tiering_enforce", False))
    if not shadow and not enforce:
        return legacy
    policy = _policy_choice(settings, purpose, modality)
    if shadow and policy != legacy:
        logger.warning(
            "model_tiering.divergence purpose=%s modality=%s legacy=%s policy=%s enforce=%s",
            purpose.value,
            modality,
            legacy,
            policy,
            enforce,
        )
    return policy if enforce else legacy


def call_options(purpose: ModelPurpose) -> dict[str, str]:
    """Extra provider kwargs for one purpose.

    ``reasoning_effort`` is sent only when the SUMMARIZATION/EXTRACTION
    override is configured: the override is a reasoning deployment by
    decision (D-10), while the gpt-4.x standard tiers reject the parameter.
    """
    from ai.core.config import get_settings

    settings = get_settings()
    if purpose in _OVERRIDE_PURPOSES and _summarization_override(settings):
        effort = str(getattr(settings, "azure_summarization_reasoning_effort", "low") or "low")
        return {"reasoning_effort": effort}
    return {}


__all__ = ["ModelPurpose", "call_options", "select_deployment"]
