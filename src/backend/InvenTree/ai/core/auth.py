"""Authenticated ASGI boundary for all interactive AIMMS routes.

The middleware in this module is intentionally a pure ASGI wrapper. It belongs
outside the mounted FastAPI application so health, documentation, and any future
routes receive the same authenticated principal.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from importlib import import_module
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from django.contrib.auth import aget_user, get_user, get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.crypto import constant_time_compare

try:
    from starlette.requests import Request
except ImportError:  # pragma: no cover - only the split Django-only environment

    class Request:  # type: ignore[no-redef]
        """Fallback annotation when the separately deployed AI deps are absent."""

        scope: dict[str, Any]


if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

AI_PRINCIPAL_SCOPE_KEY: Final = "aimms.ai.principal"
SIGNED_SUBJECT_VERSION: Final = 1
SIGNED_SUBJECT_PURPOSE: Final = "aimms.interactive"
_SIGNED_CLAIMS: Final = frozenset({
    "v",
    "aud",
    "purpose",
    "sub",
    "scope",
    "policy",
    "session_auth_hash",
})
_UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_KNOWN_ANOMALIES: Final = frozenset({
    "anonymous_session",
    "csrf_rejected",
    "deleted_subject",
    "expired_signed_subject",
    "inactive_subject",
    "invalid_authorization",
    "invalid_cookie",
    "invalid_session",
    "invalid_signed_subject",
    "legacy_body_user_id",
    "legacy_header_user_id",
    "legacy_query_user_id",
    "missing_credentials",
    "origin_rejected",
    "session_token_conflict",
    "signed_subject_claims",
    "signed_subject_hash_mismatch",
})
_identity_anomalies: Counter[str] = Counter()


@dataclass(frozen=True, slots=True)
class AIPrincipal:
    """Immutable, scalar-only identity derived at the authenticated boundary."""

    subject: str
    actor: str
    user_pk: str
    username: str
    authentication_method: str
    scope: str
    policy_version: str
    is_staff: bool
    is_superuser: bool

    @property
    def actor_id(self) -> str:
        """Return the stable actor identifier used by application services."""
        return self.actor

    @property
    def auth_source(self) -> str:
        """Return the boundary authentication mechanism."""
        return self.authentication_method

    @property
    def rate_limit_key(self) -> str:
        """Return the only permitted per-principal rate-limit key."""
        return self.subject


@dataclass(frozen=True, slots=True)
class AIBoundaryPolicy:
    """Server-owned policy used to authenticate interactive AI subjects."""

    single_site_policy_key: str
    policy_version: str
    signed_subject_max_age_seconds: int
    signed_subject_salt: str
    signed_subject_audience: str
    allowed_origins: tuple[str, ...]

    @classmethod
    def from_settings(cls) -> AIBoundaryPolicy:
        """Load the boundary policy lazily to keep this module app-independent."""
        from ai.core.config import get_settings

        configured = get_settings()
        return cls(
            single_site_policy_key=configured.single_site_policy_key,
            policy_version=configured.policy_version,
            signed_subject_max_age_seconds=configured.signed_subject_max_age_seconds,
            signed_subject_salt=configured.signed_subject_salt,
            signed_subject_audience=configured.signed_subject_audience,
            allowed_origins=tuple(configured.allowed_origins),
        )


principal_context: ContextVar[AIPrincipal | None] = ContextVar("aimms_ai_principal", default=None)


def get_current_principal() -> AIPrincipal | None:
    """Return the principal for the current ASGI task, if the boundary set one."""
    return principal_context.get()


def record_identity_anomaly(kind: str) -> None:
    """Count and log an identity-claim kind without recording its value."""
    safe_kind = kind if kind in _KNOWN_ANOMALIES else "unknown"
    _identity_anomalies[safe_kind] += 1
    logger.warning("AI identity anomaly detected (kind=%s)", safe_kind)


def get_identity_anomaly_counts() -> dict[str, int]:
    """Return a value-free snapshot of boundary anomaly counters."""
    return dict(_identity_anomalies)


def reset_identity_anomaly_counts() -> None:
    """Reset anomaly counters (intended for isolated tests)."""
    _identity_anomalies.clear()


async def require_ai_principal(request: Request) -> AIPrincipal:  # noqa: RUF029
    """FastAPI dependency that fails closed when the ASGI wrapper is absent."""
    principal = request.scope.get(AI_PRINCIPAL_SCOPE_KEY)
    if not isinstance(principal, AIPrincipal):
        # Import lazily: the Django test environment intentionally does not
        # install the independently deployed AI dependency set.
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="AI authentication required")
    return principal


def sign_interactive_subject(
    user: Any,
    *,
    policy: AIBoundaryPolicy | None = None,
) -> str:
    """Mint the only accepted short-lived interactive Authorization subject."""
    active_policy = policy or AIBoundaryPolicy.from_settings()
    if not active_policy.single_site_policy_key:
        raise ValueError("AIMMS_SINGLE_SITE_POLICY_KEY must be configured")
    if not getattr(user, "is_active", False):
        raise ValueError("Cannot sign an inactive user")

    claims = {
        "v": SIGNED_SUBJECT_VERSION,
        "aud": active_policy.signed_subject_audience,
        "purpose": SIGNED_SUBJECT_PURPOSE,
        "sub": str(user.pk),
        "scope": active_policy.single_site_policy_key,
        "policy": active_policy.policy_version,
        "session_auth_hash": user.get_session_auth_hash(),
    }
    return signing.dumps(claims, salt=active_policy.signed_subject_salt, compress=False)


class _AuthenticationFailure(Exception):
    """Internal, value-free authentication failure."""

    def __init__(self, anomaly: str):
        super().__init__(anomaly)
        self.anomaly = anomaly


def _headers(scope: Mapping[str, Any]) -> dict[bytes, list[bytes]]:
    """Collect ASGI headers without silently accepting duplicate auth headers."""
    result: dict[bytes, list[bytes]] = {}
    for raw_name, raw_value in scope.get("headers", []):
        result.setdefault(raw_name.lower(), []).append(raw_value)
    return result


def _one_header(headers: Mapping[bytes, list[bytes]], name: bytes) -> str | None:
    values = headers.get(name, [])
    if not values:
        return None
    if len(values) != 1:
        raise _AuthenticationFailure("invalid_authorization")
    return values[0].decode("latin-1")


def _session_key(headers: Mapping[bytes, list[bytes]]) -> str | None:
    raw_cookie = _one_header(headers, b"cookie")
    if not raw_cookie:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except CookieError as exc:
        raise _AuthenticationFailure("invalid_cookie") from exc
    morsel = cookie.get(django_settings.SESSION_COOKIE_NAME)
    return morsel.value if morsel and morsel.value else None


async def _session_user(headers: Mapping[bytes, list[bytes]]) -> Any | None:
    session_key = _session_key(headers)
    if session_key is None:
        return None

    engine = import_module(django_settings.SESSION_ENGINE)
    session = engine.SessionStore(session_key=session_key)
    request = HttpRequest()
    request.session = session
    try:
        user = await aget_user(request)
    except AttributeError as exc:
        if "aget_user" not in str(exc):
            raise
        user = await sync_to_async(get_user, thread_sensitive=True)(request)
    if not getattr(user, "is_authenticated", False):
        raise _AuthenticationFailure("invalid_session")
    if not getattr(user, "is_active", False):
        raise _AuthenticationFailure("inactive_subject")
    return user


def _bearer_token(headers: Mapping[bytes, list[bytes]]) -> str | None:
    authorization = _one_header(headers, b"authorization")
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token or " " in token:
        raise _AuthenticationFailure("invalid_authorization")
    return token


async def _signed_user(token: str, policy: AIBoundaryPolicy) -> Any:
    try:
        claims = signing.loads(
            token,
            salt=policy.signed_subject_salt,
            max_age=policy.signed_subject_max_age_seconds,
        )
    except signing.SignatureExpired as exc:
        raise _AuthenticationFailure("expired_signed_subject") from exc
    except signing.BadSignature as exc:
        raise _AuthenticationFailure("invalid_signed_subject") from exc

    if not isinstance(claims, dict) or set(claims) != _SIGNED_CLAIMS:
        raise _AuthenticationFailure("signed_subject_claims")
    expected = {
        "v": SIGNED_SUBJECT_VERSION,
        "aud": policy.signed_subject_audience,
        "purpose": SIGNED_SUBJECT_PURPOSE,
        "scope": policy.single_site_policy_key,
        "policy": policy.policy_version,
    }
    if any(claims.get(key) != value for key, value in expected.items()):
        raise _AuthenticationFailure("signed_subject_claims")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise _AuthenticationFailure("signed_subject_claims")
    if not isinstance(claims.get("session_auth_hash"), str):
        raise _AuthenticationFailure("signed_subject_claims")

    user_model = get_user_model()
    try:
        user = await user_model.objects.aget(pk=claims["sub"])
    except (user_model.DoesNotExist, ValueError, ValidationError) as exc:
        raise _AuthenticationFailure("deleted_subject") from exc
    if not getattr(user, "is_active", False):
        raise _AuthenticationFailure("inactive_subject")
    # Django's own ``aget_user`` calls this CPU-only method directly too. It
    # performs no database I/O and must not occupy the thread-sensitive ORM
    # executor across independently managed ASGI event loops.
    auth_hash = user.get_session_auth_hash()
    if not constant_time_compare(claims["session_auth_hash"], auth_hash):
        raise _AuthenticationFailure("signed_subject_hash_mismatch")
    return user


def _principal(user: Any, method: str, policy: AIBoundaryPolicy) -> AIPrincipal:
    subject = f"user:{user.pk}"
    return AIPrincipal(
        subject=subject,
        actor=subject,
        user_pk=str(user.pk),
        username=str(user.get_username()),
        authentication_method=method,
        scope=policy.single_site_policy_key,
        policy_version=policy.policy_version,
        is_staff=bool(user.is_staff),
        is_superuser=bool(user.is_superuser),
    )


async def _authenticate(
    headers: Mapping[bytes, list[bytes]], policy: AIBoundaryPolicy
) -> AIPrincipal:
    session_user = await _session_user(headers)
    bearer = _bearer_token(headers)
    signed_user = await _signed_user(bearer, policy) if bearer is not None else None

    if session_user is not None and signed_user is not None:
        if str(session_user.pk) != str(signed_user.pk):
            raise _AuthenticationFailure("session_token_conflict")
        return _principal(signed_user, "signed_subject", policy)
    if signed_user is not None:
        return _principal(signed_user, "signed_subject", policy)
    if session_user is not None:
        return _principal(session_user, "django_session", policy)
    raise _AuthenticationFailure("missing_credentials")


def _has_legacy_identity_claim(
    scope: Mapping[str, Any], headers: Mapping[bytes, list[bytes]]
) -> None:
    if headers.get(b"x-user-id"):
        record_identity_anomaly("legacy_header_user_id")
    query = scope.get("query_string", b"")
    if query:
        try:
            if "user_id" in parse_qs(query.decode("utf-8"), keep_blank_values=True):
                record_identity_anomaly("legacy_query_user_id")
        except UnicodeDecodeError:
            # An invalid query is application input, never an identity source.
            pass


def _origin_allowed(headers: Mapping[bytes, list[bytes]], policy: AIBoundaryPolicy) -> bool:
    try:
        origin = _one_header(headers, b"origin")
    except _AuthenticationFailure:
        return False
    return origin is not None and origin in policy.allowed_origins


def _csrf_request(scope: Mapping[str, Any], headers: Mapping[bytes, list[bytes]]) -> HttpRequest:
    request = HttpRequest()
    request.method = str(scope.get("method", "GET")).upper()
    request.path = str(scope.get("path", "/"))
    request.path_info = request.path

    server = scope.get("server") or ("localhost", 80)
    scheme = str(scope.get("scheme", "http"))
    request.META = {
        "REQUEST_METHOD": request.method,
        "PATH_INFO": request.path,
        "SERVER_NAME": str(server[0]),
        "SERVER_PORT": str(server[1]),
        "wsgi.url_scheme": scheme,
    }
    for name, values in headers.items():
        value = ",".join(item.decode("latin-1") for item in values)
        decoded_name = name.decode("latin-1").upper().replace("-", "_")
        meta_name = (
            decoded_name
            if decoded_name in {"CONTENT_LENGTH", "CONTENT_TYPE"}
            else f"HTTP_{decoded_name}"
        )
        request.META[meta_name] = value

    cookie = SimpleCookie()
    raw_cookie = request.META.get("HTTP_COOKIE", "")
    try:
        cookie.load(raw_cookie)
    except CookieError:
        cookie.clear()
    request.COOKIES = {key: morsel.value for key, morsel in cookie.items()}
    return request


def _csrf_valid(scope: Mapping[str, Any], headers: Mapping[bytes, list[bytes]]) -> bool:
    request = _csrf_request(scope, headers)
    middleware = CsrfViewMiddleware(lambda _: HttpResponse())
    response = middleware.process_view(request, lambda _: HttpResponse(), (), {})
    return response is None


async def _http_error(send: Any, status: int, error: str, message: str) -> None:
    body = json.dumps({"error": error, "message": message}, separators=(",", ":")).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class AIBoundaryAuthMiddleware:
    """Authenticate, origin-check, and CSRF-protect the mounted AI app."""

    def __init__(self, app: Any, *, policy: AIBoundaryPolicy | None = None):
        self.app = app
        self.policy = policy or AIBoundaryPolicy.from_settings()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        _has_legacy_identity_claim(scope, headers)

        # CORS middleware answers preflights; no application route is executed.
        if scope_type == "http" and str(scope.get("method", "GET")).upper() == "OPTIONS":
            if headers.get(b"origin") and not _origin_allowed(headers, self.policy):
                record_identity_anomaly("origin_rejected")
                await _http_error(send, 403, "origin_rejected", "Origin is not allowed")
                return
            await self.app(scope, receive, send)
            return

        try:
            principal = await _authenticate(headers, self.policy)
        except _AuthenticationFailure as exc:
            record_identity_anomaly(exc.anomaly)
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 4401})
            else:
                await _http_error(
                    send, 401, "authentication_required", "AI authentication is required"
                )
            return

        if scope_type == "websocket":
            if not _origin_allowed(headers, self.policy):
                record_identity_anomaly("origin_rejected")
                await send({"type": "websocket.close", "code": 4403})
                return
        else:
            method = str(scope.get("method", "GET")).upper()
            if method in _UNSAFE_METHODS:
                if not _origin_allowed(headers, self.policy):
                    record_identity_anomaly("origin_rejected")
                    await _http_error(send, 403, "origin_rejected", "Origin is not allowed")
                    return
                if principal.authentication_method == "django_session" and not _csrf_valid(
                    scope, headers
                ):
                    record_identity_anomaly("csrf_rejected")
                    await _http_error(send, 403, "csrf_rejected", "CSRF validation failed")
                    return

        scope[AI_PRINCIPAL_SCOPE_KEY] = principal
        token = principal_context.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            principal_context.reset(token)


__all__ = [
    "AI_PRINCIPAL_SCOPE_KEY",
    "AIBoundaryAuthMiddleware",
    "AIBoundaryPolicy",
    "AIPrincipal",
    "get_current_principal",
    "get_identity_anomaly_counts",
    "principal_context",
    "record_identity_anomaly",
    "require_ai_principal",
    "reset_identity_anomaly_counts",
    "sign_interactive_subject",
]
