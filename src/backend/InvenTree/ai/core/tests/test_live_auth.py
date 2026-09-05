"""D1: the runner's non-staff signed-subject auth mints per request."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from ai.core.evals import live_auth


class _Signer:
    def __init__(self):
        self.calls = 0

    def __call__(self, user):
        self.calls += 1
        return f"subject-{user.pk}-{self.calls}"


def _auth(token="drf-token", staff=False):
    signer = _Signer()
    user = SimpleNamespace(pk=7, is_staff=staff, is_superuser=False)
    auth = live_auth.SignedSubjectAuth(
        "yesworkorders", django_token=token, signer=signer, user_loader=lambda _name: user
    )
    return auth, signer


def _send(auth: httpx.Auth, path: str) -> httpx.Request:
    request = httpx.Request("GET", f"http://battery.test{path}")
    flow = auth.auth_flow(request)
    return next(flow)


def test_ai_paths_get_a_fresh_signed_subject_every_request():
    auth, signer = _auth()
    first = _send(auth, "/api/ai/chat")
    second = _send(auth, "/api/ai/threads/x")
    assert first.headers["Authorization"] == "Bearer subject-7-1"
    assert second.headers["Authorization"] == "Bearer subject-7-2"
    assert signer.calls == auth.minted == 2


def test_other_paths_carry_the_django_token_and_mint_nothing():
    auth, signer = _auth()
    request = _send(auth, "/api/assets/machine/")
    assert request.headers["Authorization"] == "Token drf-token"
    assert signer.calls == 0
    # No token configured -> no header at all (never an empty scheme).
    auth, _ = _auth(token="")
    assert "Authorization" not in _send(auth, "/api/aichat/proposals/").headers


def test_the_user_is_loaded_once_and_lazily():
    loads = []

    def loader(name):
        loads.append(name)
        return SimpleNamespace(pk=3, is_staff=False, is_superuser=False)

    auth = live_auth.SignedSubjectAuth("tech", signer=lambda _user: "s", user_loader=loader)
    assert loads == []
    _send(auth, "/api/ai/chat")
    _send(auth, "/api/ai/chat")
    assert loads == ["tech"]


def test_staff_subjects_are_refused(monkeypatch):
    """A staff principal would widen every projection and inflate the baseline."""
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
    import django

    django.setup()
    from django.contrib.auth import get_user_model

    class _Manager:
        def filter(self, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(pk=1, is_staff=True, is_superuser=False)

    monkeypatch.setattr(get_user_model(), "objects", _Manager())
    with pytest.raises(RuntimeError, match="non-staff"):
        live_auth._django_user("admin")


def test_unknown_users_are_refused(monkeypatch):
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
    import django

    django.setup()
    from django.contrib.auth import get_user_model

    class _Manager:
        def filter(self, **kwargs):
            return self

        def first(self):
            return None

    monkeypatch.setattr(get_user_model(), "objects", _Manager())
    with pytest.raises(RuntimeError, match="does not exist"):
        live_auth._django_user("ghost")


def test_auth_from_env_is_none_without_the_signed_subject_user(monkeypatch):
    monkeypatch.delenv("AIMMS_BATTERY_SIGNED_SUBJECT_USER", raising=False)
    assert live_auth.auth_from_env() is None
    monkeypatch.setenv("AIMMS_BATTERY_SIGNED_SUBJECT_USER", "yesworkorders")
    monkeypatch.setenv("AIMMS_BATTERY_DJANGO_TOKEN", "t")
    auth = live_auth.auth_from_env()
    assert isinstance(auth, live_auth.SignedSubjectAuth)
    assert auth.username == "yesworkorders"
    assert auth.django_token == "t"
