"""Bounded, keyless provider seams for the dedicated memory worker.

These helpers do not authorize work or schedule calls. Callers must check consent,
restore holds, queue placement and budget before calling, then recheck before
persisting a result. No provider response or exception text is logged here.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from ai.core.integrations.azure_openai_client import (
    build_openai_client,
    cognitive_token_provider,
)
from ai.core.redaction import redact_text

DIMENSIONS = 1536
MAX_DOCUMENTS = 5
MAX_CHARS = 10_000
MAX_RESPONSE_BYTES = 65_536
TIMEOUT_SECONDS = 15.0
SHIELD_API_VERSION = "2024-09-01"


@dataclass(frozen=True)
class ShieldResult:
    """Only annotations, with unavailable distinct from clear."""

    states: tuple[str, ...]
    attempted: bool = False
    error_code: str = ""


@dataclass(frozen=True)
class EmbeddingResult:
    """A validated vector and usage; an unavailable vector is never persisted."""

    vector: tuple[float, ...] | None = None
    profile: str = ""
    input_tokens: int = 0
    attempted: bool = False
    error_code: str = ""


def _texts(documents: list[str]) -> list[str]:
    if (
        not isinstance(documents, list)
        or not 1 <= len(documents) <= MAX_DOCUMENTS
        or any(not isinstance(doc, str) or not doc.strip() for doc in documents)
        or sum(len(doc) for doc in documents) > MAX_CHARS
    ):
        raise ValueError("memory_input_bounds")
    return [redact_text(doc)[0] for doc in documents]


def _shield_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not host.endswith(".cognitiveservices.azure.com")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("memory_shield_endpoint")
    return (
        f"{endpoint.rstrip('/')}/contentsafety/text:shieldPrompt?api-version={SHIELD_API_VERSION}"
    )


def shield_documents(documents: list[str], *, settings) -> ShieldResult:
    """Annotate at most five documents, without treating an outage as clear.

    Annotation never grants tool authority. A flagged response is retained for
    policy/UI handling; unavailable blocks confirmation until a later retry.
    """
    count = len(documents) if isinstance(documents, list) else 0
    unavailable = ("unavailable",) * min(count, MAX_DOCUMENTS)
    attempted = False
    try:
        texts = _texts(documents)
        url = _shield_url(settings.memory_shield_endpoint)
        token = cognitive_token_provider()()
        with httpx.Client(
            timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            attempted = True
            with client.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"documents": texts},
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError("memory_shield_response_size")
                data = json.loads(body)
        analyses = data.get("documentsAnalysis")
        if not isinstance(analyses, list) or len(analyses) != len(texts):
            raise ValueError("memory_shield_shape")
        flags = [row.get("attackDetected") for row in analyses if isinstance(row, dict)]
        if len(flags) != len(texts) or any(type(flag) is not bool for flag in flags):
            raise ValueError("memory_shield_shape")
        return ShieldResult(tuple("flagged" if flag else "clear" for flag in flags), attempted=True)
    except Exception:
        return ShieldResult(unavailable, attempted, "shield_unavailable")


def embed_memory(text: str, *, settings) -> EmbeddingResult:
    """Request exactly 1536 floats from the dedicated memory deployment.

    SDK retries are disabled: durable worker retry policy owns the call budget.
    Request timeouts bound transport waits, not credential acquisition or the
    complete task. The memory worker's task timeout supplies the outer bound.
    """
    attempted = False
    input_tokens = 0
    try:
        value = _texts([text])[0]
        deployment = settings.memory_embedding_deployment
        if (
            settings.memory_embedding_dims != DIMENSIONS
            or not isinstance(deployment, str)
            or not deployment
            or len(deployment) > 110
        ):
            raise ValueError("memory_embedding_config")
        client = build_openai_client(settings=settings, require_keyless=True)
        with client.with_options(
            timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=5.0), max_retries=0
        ) as bounded:
            attempted = True
            response = bounded.embeddings.create(
                model=deployment, input=[value], dimensions=DIMENSIONS, encoding_format="float"
            )
        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "prompt_tokens", 0)
        if type(tokens) is int and tokens >= 0:
            input_tokens = tokens
        rows = response.data
        if len(rows) != 1 or rows[0].index != 0:
            raise ValueError("memory_embedding_shape")
        vector = rows[0].embedding
        if (
            len(vector) != DIMENSIONS
            or any(
                type(v) not in (int, float) or (not math.isfinite(v) or abs(v) > 3.4e38)
                for v in vector
            )
            or not any(v != 0 for v in vector)
        ):
            raise ValueError("memory_embedding_shape")
        return EmbeddingResult(
            tuple(float(v) for v in vector),
            f"{deployment}:{DIMENSIONS}",
            input_tokens,
            True,
        )
    except Exception:
        return EmbeddingResult(
            input_tokens=input_tokens, attempted=attempted, error_code="embedding_unavailable"
        )
