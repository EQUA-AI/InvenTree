"""Strict-mode provider schema export shared by tool and response builders.

A raw ``model_json_schema()`` is rejected at dispatch when a request declares
``strict: True``: the provider requires every property to appear in
``required`` and ``additionalProperties: false`` at every object level
(verified live against gpt-5.6-luna on 2026-08-03 — the first real reasoning
dispatch 400ed with ``invalid_json_schema``). The OpenAI SDK ships the exact
transformer its own ``parse()`` path uses; every strict schema we send must
come through it.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=64)
def _strict_schema_cached(model: type) -> dict[str, Any]:
    try:
        from openai.lib._pydantic import to_strict_json_schema
    except ImportError:  # pragma: no cover - SDK absent in minimal test envs
        schema = model.model_json_schema()
        schema["additionalProperties"] = False
        return schema
    return to_strict_json_schema(model)


def strict_provider_schema(model: type) -> dict[str, Any]:
    """Return a strict-mode JSON schema for one pydantic model.

    Each call returns a fresh copy so a caller mutating its request payload
    cannot poison the cache for every later turn.
    """
    return copy.deepcopy(_strict_schema_cached(model))
