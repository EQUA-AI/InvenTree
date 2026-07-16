"""Exact Azure Voice Live endpoint construction.

The wire contract is pinned to the API versions verified against Microsoft
Learn on 2026-07-14 (see ``LocalDocs/VoiceInterfaceImplementation.md`` §5.2):

- GA control WebSocket:   ``wss://<resource>.services.ai.azure.com/voice-live/realtime?api-version=2026-04-10&model=<model>``
- Preview WebRTC calls:   ``wss://<resource>.services.ai.azure.com/voice-live/realtime/calls?api-version=2026-01-01-preview&model=<model>``

Builders are pure functions with fail-closed input validation so a malformed
or hostile configuration value can never produce a URL that targets a
non-Azure host or smuggles extra query/path segments.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode, urlsplit

GA_CONTROL_API_VERSION = "2026-04-10"
WEBRTC_CALLS_API_VERSION = "2026-01-01-preview"

#: Entra token scope for Voice Live / Foundry access. The legacy
#: ``https://cognitiveservices.azure.com/.default`` scope also works but the
#: current documented scope is the single value used everywhere in AIMMS.
TOKEN_SCOPE = "https://ai.azure.com/.default"

_ALLOWED_HOST_SUFFIXES = (
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
)


class VoiceLiveEndpointError(ValueError):
    """Raised when endpoint inputs would produce an unsafe or invalid URL."""


def _validated_host(resource_host: str) -> str:
    host = (resource_host or "").strip().lower()
    if "://" in host:
        parsed = urlsplit(host)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise VoiceLiveEndpointError("resource host must not carry a path, query, or fragment")
        host = parsed.netloc
    if not host or "@" in host or ":" in host:
        raise VoiceLiveEndpointError(
            "resource host must be a bare hostname without credentials or port"
        )
    if any(ch in host for ch in "/?#\\ \t"):
        raise VoiceLiveEndpointError("resource host contains illegal characters")
    if not host.endswith(_ALLOWED_HOST_SUFFIXES):
        raise VoiceLiveEndpointError("resource host is not an Azure AI services hostname")
    return host


def _validated_model(model: str) -> str:
    value = (model or "").strip()
    if not value:
        raise VoiceLiveEndpointError("model is required")
    return value


def _url(host: str, path: str, api_version: str, model: str) -> str:
    query = urlencode(
        {"api-version": api_version, "model": model},
        quote_via=quote,
        safe="",
    )
    return f"wss://{host}{path}?{query}"


def build_control_url(
    resource_host: str,
    model: str,
    *,
    api_version: str = GA_CONTROL_API_VERSION,
) -> str:
    """Return the GA Voice Live control WebSocket URL."""
    return _url(
        _validated_host(resource_host),
        "/voice-live/realtime",
        api_version,
        _validated_model(model),
    )


def build_calls_url(
    resource_host: str,
    model: str,
    *,
    api_version: str = WEBRTC_CALLS_API_VERSION,
) -> str:
    """Return the public-preview WebRTC calls WebSocket URL.

    Microsoft documents this endpoint as public preview and not recommended
    for production workloads; callers must keep it behind its own flag.
    """
    return _url(
        _validated_host(resource_host),
        "/voice-live/realtime/calls",
        api_version,
        _validated_model(model),
    )
