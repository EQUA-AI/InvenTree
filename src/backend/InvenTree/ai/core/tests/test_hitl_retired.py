"""A4: the retired HITL endpoints answer 410 Gone, never a 200 that reads as success."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core import app as ai_app
from ai.core.auth import principal_context
from ai.core.tests.test_realtime_session_api import _principal, _user
from django.core.management import call_command
from fastapi import HTTPException


@pytest.fixture(scope="module", autouse=True)
def _database():
    call_command("migrate", verbosity=0, interactive=False)
    yield


def _call(principal, coroutine_factory):
    token = principal_context.set(principal)
    try:
        return asyncio.run(coroutine_factory())
    finally:
        principal_context.reset(token)


def test_respond_is_gone_for_authenticated_callers():
    principal = _principal(_user())
    request = ai_app.HITLRespondRequest(request_id="req-1", approved=True, reason="ok")
    with pytest.raises(HTTPException) as excinfo:
        _call(principal, lambda: ai_app.respond_to_hitl(request))
    assert excinfo.value.status_code == 410
    assert excinfo.value.detail["code"] == "HITL_RETIRED"
    assert "/api/aichat/proposals/" in excinfo.value.detail["message"]
    assert "success" not in excinfo.value.detail


def test_pending_is_gone_for_authenticated_callers():
    principal = _principal(_user())
    with pytest.raises(HTTPException) as excinfo:
        _call(principal, lambda: ai_app.get_pending_hitl(thread_id="t-1"))
    assert excinfo.value.status_code == 410
    assert excinfo.value.detail == ai_app.HITL_RETIRED_DETAIL


def test_unauthenticated_callers_do_not_even_get_the_notice():
    request = ai_app.HITLRespondRequest(request_id="req-1", approved=False)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(ai_app.respond_to_hitl(request))
    assert excinfo.value.status_code in (401, 403)


def test_retirement_detail_never_claims_success():
    assert ai_app.HITL_RETIRED_DETAIL["code"] == "HITL_RETIRED"
    assert "performs no action" in ai_app.HITL_RETIRED_DETAIL["message"]
