"""Server-derived context for normalized unscoped AI turns.

This module intentionally does not implement Feature #14. Any future
record-scoped context must pass that feature's Django-signed ``ChatContext``
gate, scoped-conversation namespace checks, record resolution, and per-call
authorization. Browser content handled here can never stand in for that gate.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ai.core.auth import AIPrincipal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

UNSCOPED_THREAD_NAMESPACE = "unscoped"
UNSCOPED_READ_CAPABILITY = "chat.unscoped.read"


class TrustedContextConfigurationError(ValueError):
    """Raised when a server-owned trusted-context setting is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class TrustedTurnContext:
    """Immutable server authority plus separately labelled untrusted content.

    ``untrusted_content`` is a canonical JSON string retained only as user
    input. It must never contribute actor/scope ids, namespace, capabilities,
    route/tool selection, or record selection. Record-scoped turns are blocked
    until external Feature #14's signed scoped-context gate is available.
    """

    actor: str
    server_policy_key: str
    server_policy_hash: str
    thread_namespace: str
    server_route_hints: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    correlation_id: str
    policy_version: str
    untrusted_content: str


def _canonical_untrusted_content(content: Mapping[str, Any] | None) -> str:
    """Serialize browser context without consulting any of its claims."""
    if content is None:
        return "{}"
    try:
        serialized = json.dumps(
            content,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Browser context must contain JSON values") from exc
    if len(serialized.encode("utf-8")) > 16 * 1024:
        raise ValueError("Browser context exceeds 16 KiB")
    return serialized


def _server_route_hints(hints: Sequence[str] | None) -> tuple[str, ...]:
    """Validate route-only hints supplied by trusted server routing code."""
    normalized: list[str] = []
    for hint in hints or ():
        if not isinstance(hint, str) or not hint.startswith("/"):
            raise TrustedContextConfigurationError("Server route hints must be paths")
        parsed = urlsplit(hint)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise TrustedContextConfigurationError("Server route hints must not be URLs")
        if hint not in normalized:
            normalized.append(hint)
    return tuple(normalized)


def _safe_capabilities(capabilities: Sequence[str] | None) -> tuple[str, ...]:
    """Constrain unscoped chat to explicit, read-only server capabilities."""
    selected = tuple(capabilities or (UNSCOPED_READ_CAPABILITY,))
    if not selected:
        return (UNSCOPED_READ_CAPABILITY,)
    for capability in selected:
        if not isinstance(capability, str) or not capability:
            raise TrustedContextConfigurationError("Capabilities must be non-empty strings")
        lowered = capability.lower()
        if any(
            marker in lowered
            for marker in ("write", "create", "update", "delete", "execute", "approve")
        ):
            raise TrustedContextConfigurationError(
                "Unscoped chat capabilities must remain read-only"
            )
    return tuple(dict.fromkeys(selected))


def build_trusted_turn_context(
    principal: AIPrincipal,
    *,
    single_site_policy_key: str | None = None,
    policy_version: str | None = None,
    server_route_hints: Sequence[str] | None = None,
    server_allowed_capabilities: Sequence[str] | None = None,
    correlation_id: str | None = None,
    browser_context: Mapping[str, Any] | None = None,
) -> TrustedTurnContext:
    """Build the unscoped trusted envelope solely from server-owned inputs.

    Browser-provided ``browser_context`` is serialized only into
    ``untrusted_content`` after every authority-bearing field has been derived.
    """
    if not isinstance(principal, AIPrincipal):
        raise TypeError("An authenticated AIPrincipal is required")

    if single_site_policy_key is None or policy_version is None:
        from ai.core.config import get_settings

        configured = get_settings()
        if single_site_policy_key is None:
            single_site_policy_key = configured.single_site_policy_key
        if policy_version is None:
            policy_version = configured.policy_version

    policy_key = single_site_policy_key.strip()
    version = policy_version.strip()
    if not policy_key:
        raise TrustedContextConfigurationError("AIMMS_SINGLE_SITE_POLICY_KEY must be configured")
    if not version:
        raise TrustedContextConfigurationError("AIMMS_POLICY_VERSION must be configured")
    if principal.scope != policy_key or principal.policy_version != version:
        raise TrustedContextConfigurationError("Principal policy no longer matches the server")

    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    else:
        try:
            correlation_id = str(uuid.UUID(correlation_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("correlation_id must be a UUID") from exc

    return TrustedTurnContext(
        actor=principal.actor,
        server_policy_key=policy_key,
        server_policy_hash=hashlib.sha256(policy_key.encode("utf-8")).hexdigest(),
        thread_namespace=UNSCOPED_THREAD_NAMESPACE,
        server_route_hints=_server_route_hints(server_route_hints),
        allowed_capabilities=_safe_capabilities(server_allowed_capabilities),
        correlation_id=correlation_id,
        policy_version=version,
        untrusted_content=_canonical_untrusted_content(browser_context),
    )


__all__ = [
    "UNSCOPED_READ_CAPABILITY",
    "UNSCOPED_THREAD_NAMESPACE",
    "TrustedContextConfigurationError",
    "TrustedTurnContext",
    "build_trusted_turn_context",
]
