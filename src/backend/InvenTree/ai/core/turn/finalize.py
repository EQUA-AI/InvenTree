"""Terminal persistence helpers for the normalized turn pipeline (S47).

Commit 1 carries only the metadata builder (moved verbatim); the terminal
stage functions land in commit 3.
"""

from __future__ import annotations

from typing import Any

from ai.core.usage import drain_turn_usage


def _terminal_output_metadata(base: dict[str, Any]) -> dict[str, Any]:
    """Attach the resolved model-version stamp to a terminal turn (S17 A10).

    Deployment names are aliases; the stamp records which concrete model
    identities the provider reported during this process, so a post-hoc audit
    of any persisted turn can name the models that served it. S24 adds the
    turn's provider usage through the same funnel, behind its kill switch.
    """
    from ai.core.config import get_settings
    from ai.core.integrations.model_pins import resolved_model_versions

    metadata = dict(base)
    versions = resolved_model_versions()
    if versions:
        metadata["model_versions"] = versions
    try:
        if get_settings().feature_turn_usage_persistence:
            usage = drain_turn_usage()
            if usage:
                metadata["usage"] = usage
    except Exception:  # pragma: no cover - telemetry must never fail a turn
        pass
    return metadata
