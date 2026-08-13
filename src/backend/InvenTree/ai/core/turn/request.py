"""Request-shaping helpers for the normalized turn pipeline (S47).

Moved verbatim from ``ai.core.turn_service``; the facade re-exports every
name so existing imports keep working.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any


def _machine_name_matches(name: str, lowered_content: str) -> bool:
    """Token-based utterance match for a machine display name.

    "influent pump station" must match "Influent Pump Station No. 1": a name
    matches when ALL of its substantive alphabetic tokens (len >= 3) appear
    in the lowered utterance; names with no such tokens never match. Shared
    by the clarify-first routing signal and the cross-machine grounding
    fence seed (P8-W0a) so both agree on what "the turn is about machine X"
    means.
    """
    tokens = [token for token in re.findall(r"[a-z]+", name.lower()) if len(token) >= 3]
    return bool(tokens) and all(token in lowered_content for token in tokens)


def _reject_durable_audio(value: Any, *, path: str = "metadata") -> None:
    """Reject raw/audio-shaped values before any durable turn write."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("raw audio must not enter normalized turn persistence")
    if isinstance(value, dict):
        forbidden = {
            "audio",
            "audio_bytes",
            "audio_data",
            "audio_payload",
            "pcm",
            "waveform",
        }
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise ValueError("raw audio metadata is not permitted")
            _reject_durable_audio(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_durable_audio(item, path=f"{path}[{index}]")


def _json_value(value: Any, *, reject_audio: bool = False) -> dict[str, Any]:
    """Convert a trusted context object to a JSON-compatible dictionary."""

    if hasattr(value, "to_dict"):
        result = value.to_dict()
    elif hasattr(value, "model_dump"):
        result = value.model_dump(mode="json")
    elif is_dataclass(value):
        result = asdict(value)
    elif isinstance(value, dict):
        result = value
    else:  # pragma: no cover - defensive misuse guard
        raise TypeError("trusted context must be serializable")

    # A round-trip both validates portability and strips exotic mapping types.
    if reject_audio:
        _reject_durable_audio(result)
    try:
        normalized = json.loads(json.dumps(result, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError("turn metadata must contain JSON values") from exc
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise TypeError("trusted context must serialize to an object")
    return normalized


def turn_request_fingerprint(
    *,
    content: str,
    modality: str,
    trusted_context: dict[str, Any],
    modality_metadata: dict[str, Any],
) -> str:
    """Return the stable fingerprint bound to an idempotency key."""

    payload = json.dumps(
        {
            "content": content,
            "modality": modality,
            "trusted_context": trusted_context,
            "modality_metadata": modality_metadata,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
