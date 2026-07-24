"""Authentication, CSRF, origin, and redaction tests for the AI ASGI boundary."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.auth import (  # noqa: E402
    AI_PRINCIPAL_SCOPE_KEY,
    AIBoundaryAuthMiddleware,
    AIBoundaryPolicy,
    get_current_principal,
    get_identity_anomaly_counts,
    require_ai_principal,
    reset_identity_anomaly_counts,
    sign_interactive_subject,
)
from ai.core.tests.settings import TEST_USERS, TestUser, TestUserModel  # noqa: E402
from django.contrib.auth import (  # noqa: E402
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
)
from django.contrib.sessions.backends.signed_cookies import SessionStore  # noqa: E402
from django.http import HttpRequest  # noqa: E402
from django.middleware.csrf import get_token  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402

ORIGIN = "https://app.example.test"


def _policy() -> AIBoundaryPolicy:
    return AIBoundaryPolicy(
        single_site_policy_key="pilot-site",
        policy_version="policy-v1",
        signed_subject_max_age_seconds=60,
        signed_subject_salt="tests.ai.interactive.v1",
        signed_subject_audience="tests-ai",
        allowed_origins=(ORIGIN,),
    )


def _session_cookie(user) -> str:
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "ai.core.tests.settings.InMemoryAuthenticationBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    return f"sessionid={session.session_key}"


def _csrf_pair() -> tuple[str, str]:
    request = HttpRequest()
    header_token = get_token(request)
    return request.META["CSRF_COOKIE"], header_token


def _scope(
    *,
    path: str = "/chat",
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_type: str = "http",
) -> dict[str, object]:
    scope: dict[str, object] = {
        "type": scope_type,
        "path": path,
        "root_path": "/api/ai",
        "scheme": "https" if scope_type == "http" else "wss",
        "server": ("testserver", 443),
        "headers": headers or [],
        "query_string": b"",
    }
    if scope_type == "http":
        scope["method"] = method
    return scope


async def _run(
    scope: dict[str, object], policy: AIBoundaryPolicy
) -> tuple[list[dict], tuple[Any, Any] | None]:
    messages: list[dict] = []
    observed: tuple[Any, Any] | None = None

    async def app(inner_scope, _receive, send):
        nonlocal observed
        observed = (
            inner_scope.get(AI_PRINCIPAL_SCOPE_KEY),
            get_current_principal(),
        )
        if inner_scope["type"] == "websocket":
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})
        else:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    async def receive():  # noqa: RUF029
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # noqa: RUF029
        messages.append(message)

    middleware = AIBoundaryAuthMiddleware(app, policy=policy)
    await middleware(scope, receive, send)
    return messages, observed


def _execute(
    scope: dict[str, object], policy: AIBoundaryPolicy
) -> tuple[list[dict], tuple[Any, Any] | None]:
    with patch("ai.core.auth.get_user_model", return_value=TestUserModel):
        return asyncio.run(_run(scope, policy))


def _status(messages: list[dict]) -> int:
    return next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )


class AIBoundaryAuthTests(SimpleTestCase):
    """Verify all accepted identities are current Django users."""

    def setUp(self) -> None:
        reset_identity_anomaly_counts()
        self.policy = _policy()
        TEST_USERS.clear()
        self.user = TestUser("7", "boundary-user", "old-password")
        self.other = TestUser("8", "other-user", "other-password")
        TEST_USERS[self.user.pk] = self.user
        TEST_USERS[self.other.pk] = self.other

    def test_valid_django_session_uses_public_session_primitives(self) -> None:
        messages, observed = _execute(
            _scope(headers=[(b"cookie", _session_cookie(self.user).encode())]),
            self.policy,
        )

        self.assertEqual(_status(messages), 200)
        assert observed is not None
        principal, contextual = observed
        self.assertEqual(principal, contextual)
        self.assertEqual(principal.user_pk, str(self.user.pk))
        self.assertEqual(principal.authentication_method, "django_session")

    def test_signed_subject_valid_and_unsafe_request_needs_no_csrf_cookie(self) -> None:
        token = sign_interactive_subject(self.user, policy=self.policy)
        messages, observed = _execute(
            _scope(
                method="POST",
                headers=[
                    (b"authorization", f"Bearer {token}".encode()),
                    (b"origin", ORIGIN.encode()),
                ],
            ),
            self.policy,
        )

        self.assertEqual(_status(messages), 200)
        assert observed is not None
        self.assertEqual(observed[0].authentication_method, "signed_subject")

    def test_signed_subject_expired_is_rejected(self) -> None:
        token = sign_interactive_subject(self.user, policy=self.policy)
        messages, _ = _execute(
            _scope(headers=[(b"authorization", f"Bearer {token}".encode())]),
            replace(self.policy, signed_subject_max_age_seconds=-1),
        )
        self.assertEqual(_status(messages), 401)
        self.assertEqual(get_identity_anomaly_counts(), {"expired_signed_subject": 1})

    def test_signed_subject_forgery_is_rejected(self) -> None:
        token = f"{sign_interactive_subject(self.user, policy=self.policy)}forged"
        messages, _ = _execute(
            _scope(headers=[(b"authorization", f"Bearer {token}".encode())]),
            self.policy,
        )
        self.assertEqual(_status(messages), 401)
        self.assertEqual(get_identity_anomaly_counts(), {"invalid_signed_subject": 1})

    def test_signed_subject_inactive_user_is_rejected(self) -> None:
        token = sign_interactive_subject(self.user, policy=self.policy)
        self.user.is_active = False
        try:
            messages, _ = _execute(
                _scope(headers=[(b"authorization", f"Bearer {token}".encode())]),
                self.policy,
            )
        finally:
            self.user.is_active = True
        self.assertEqual(_status(messages), 401)
        self.assertEqual(get_identity_anomaly_counts(), {"inactive_subject": 1})

    def test_signed_subject_session_hash_mismatch_is_rejected(self) -> None:
        token = sign_interactive_subject(self.user, policy=self.policy)
        self.user.set_password("new-password")
        messages, _ = _execute(
            _scope(headers=[(b"authorization", f"Bearer {token}".encode())]),
            self.policy,
        )
        self.assertEqual(_status(messages), 401)
        self.assertEqual(get_identity_anomaly_counts(), {"signed_subject_hash_mismatch": 1})

    def test_signed_subject_deleted_user_is_rejected(self) -> None:
        disposable = TestUser("9", "delete-me")
        TEST_USERS[disposable.pk] = disposable
        token = sign_interactive_subject(disposable, policy=self.policy)
        del TEST_USERS[disposable.pk]
        messages, _ = _execute(
            _scope(headers=[(b"authorization", f"Bearer {token}".encode())]),
            self.policy,
        )
        self.assertEqual(_status(messages), 401)
        self.assertEqual(get_identity_anomaly_counts(), {"deleted_subject": 1})

    def test_conflicting_session_and_token_subjects_are_rejected(self) -> None:
        token = sign_interactive_subject(self.other, policy=self.policy)
        messages, _ = _execute(
            _scope(
                headers=[
                    (b"cookie", _session_cookie(self.user).encode()),
                    (b"authorization", f"Bearer {token}".encode()),
                ]
            ),
            self.policy,
        )
        self.assertEqual(_status(messages), 401)
        self.assertEqual(get_identity_anomaly_counts(), {"session_token_conflict": 1})

    def test_every_real_route_including_health_and_docs_requires_auth(self) -> None:
        for path in ("/chat", "/health", "/docs", "/openapi.json"):
            with self.subTest(path=path):
                messages, _ = _execute(_scope(path=path), self.policy)
                self.assertEqual(_status(messages), 401)

    def test_options_may_pass_for_cors_without_a_principal(self) -> None:
        messages, _ = _execute(
            _scope(method="OPTIONS", headers=[(b"origin", ORIGIN.encode())]),
            self.policy,
        )
        self.assertEqual(_status(messages), 200)

    def test_unsafe_session_request_requires_exact_origin_and_django_csrf(self) -> None:
        csrf_cookie, csrf_header = _csrf_pair()
        session_cookie = _session_cookie(self.user)
        valid_headers = [
            (b"cookie", f"{session_cookie}; csrftoken={csrf_cookie}".encode()),
            (b"origin", ORIGIN.encode()),
            (b"x-csrftoken", csrf_header.encode()),
            (b"host", b"testserver"),
        ]
        messages, _ = _execute(_scope(method="POST", headers=valid_headers), self.policy)
        self.assertEqual(_status(messages), 200)

        no_csrf = [header for header in valid_headers if header[0] != b"x-csrftoken"]
        messages, _ = _execute(_scope(method="POST", headers=no_csrf), self.policy)
        self.assertEqual(_status(messages), 403)

        wrong_origin = [
            (name, b"https://app.example.test.evil" if name == b"origin" else value)
            for name, value in valid_headers
        ]
        messages, _ = _execute(_scope(method="POST", headers=wrong_origin), self.policy)
        self.assertEqual(_status(messages), 403)

    def test_websocket_auth_and_origin_failures_use_private_close_codes(self) -> None:
        messages, _ = _execute(_scope(scope_type="websocket"), self.policy)
        self.assertEqual(messages, [{"type": "websocket.close", "code": 4401}])

        token = sign_interactive_subject(self.user, policy=self.policy)
        messages, _ = _execute(
            _scope(
                scope_type="websocket",
                headers=[
                    (b"authorization", f"Bearer {token}".encode()),
                    (b"origin", b"https://evil.example"),
                ],
            ),
            self.policy,
        )
        self.assertEqual(messages, [{"type": "websocket.close", "code": 4403}])

    def test_legacy_identity_claims_are_ignored_and_counted(self) -> None:
        token = sign_interactive_subject(self.user, policy=self.policy)
        scope = _scope(
            headers=[
                (b"authorization", f"Bearer {token}".encode()),
                (b"x-user-id", str(self.other.pk).encode()),
            ]
        )
        scope["query_string"] = f"user_id={self.other.pk}".encode()
        messages, observed = _execute(scope, self.policy)
        self.assertEqual(_status(messages), 200)
        assert observed is not None
        self.assertEqual(observed[0].user_pk, str(self.user.pk))
        self.assertEqual(
            get_identity_anomaly_counts(),
            {"legacy_header_user_id": 1, "legacy_query_user_id": 1},
        )

    def test_logs_and_anomaly_counters_never_contain_claim_values(self) -> None:
        raw_token = f"{sign_interactive_subject(self.user, policy=self.policy)}bad"
        with self.assertLogs("ai.core.auth", level="WARNING") as captured:
            messages, _ = _execute(
                _scope(headers=[(b"authorization", f"Bearer {raw_token}".encode())]),
                self.policy,
            )
        self.assertEqual(_status(messages), 401)
        rendered = "\n".join(captured.output)
        self.assertNotIn(raw_token, rendered)
        self.assertNotIn(str(self.user.pk), rendered)
        self.assertEqual(set(get_identity_anomaly_counts()), {"invalid_signed_subject"})

    def test_missing_principal_dependency_fails_closed(self) -> None:
        try:
            from fastapi import HTTPException
        except ImportError:
            self.skipTest("FastAPI dependency set is tested in the AI environment")

        request = SimpleNamespace(scope={})
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(require_ai_principal(request))
        self.assertEqual(raised.exception.status_code, 401)

    def test_standalone_fastapi_dependency_returns_401_not_422(self) -> None:
        try:
            from fastapi import Depends, FastAPI
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            self.skipTest("FastAPI dependency set is tested in the AI environment")

        app = FastAPI()

        @app.get("/protected")
        async def protected(_principal=Depends(require_ai_principal)):
            return {"ok": True}

        async def request_protected():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                return await client.get("/protected")

        response = asyncio.run(request_protected())
        self.assertEqual(response.status_code, 401)
