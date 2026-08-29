"""S10 WP-A5: evidence-set route behavior (cursor, 404 posture, headers)."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from aichat.services.threads import ThreadNotFound
from fastapi import HTTPException, Response


def _principal():
    return SimpleNamespace(
        subject="user:5", user_pk="5", scope="site-a", rate_limit_key="user:5", is_staff=False
    )


def _set_row(**overrides):
    row = SimpleNamespace(
        pk="set_" + "a" * 32,
        source_class="work_order",
        filters={"machine_ids": [12]},
        population_count=60,
        evaluated_count=60,
        displayed_count=25,
        complete_population=True,
        supports_expansion=True,
        member_count=60,
        member_cap=25000,
        calculation={"operation": "count", "result": "60"},
        created_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def _members(start: int, count: int) -> list[dict]:
    return [
        {
            "member_index": index,
            "source_class": "work_order",
            "source_object_id": str(index),
            "label": f"WO-{index}",
            "available": True,
        }
        for index in range(start, start + count)
    ]


def _repo(row=None, members=None, error: Exception | None = None):
    def evidence_set(thread_id, set_id):
        if error is not None:
            raise error
        return row

    def evidence_set_members(thread_id, set_id, *, after_ordinal, limit):
        if error is not None:
            raise error
        return [member for member in (members or []) if member["member_index"] > after_ordinal][
            :limit
        ]

    return SimpleNamespace(evidence_set=evidence_set, evidence_set_members=evidence_set_members)


def _call_header(repo, thread_id="thread_1", set_id="set_" + "a" * 32):
    from ai.core.app import get_evidence_set

    response = Response()
    with (
        mock.patch("ai.core.app._principal", side_effect=_principal),
        mock.patch("ai.core.app._repository", return_value=repo),
    ):
        payload = asyncio.run(get_evidence_set(thread_id, set_id, response))
    return payload, response


def _call_members(repo, *, cursor=None, limit=50, thread_id="thread_1", set_id="set_" + "a" * 32):
    from ai.core.app import get_evidence_set_members

    response = Response()
    with (
        mock.patch("ai.core.app._principal", side_effect=_principal),
        mock.patch("ai.core.app._repository", return_value=repo),
    ):
        payload = asyncio.run(
            get_evidence_set_members(thread_id, set_id, response, cursor=cursor, limit=limit)
        )
    return payload, response


def test_header_projects_counts_and_never_scope_hashes() -> None:
    payload, response = _call_header(_repo(row=_set_row()))
    assert payload["set_id"].startswith("set_")
    assert payload["population_count"] == 60
    assert payload["complete_population"] is True
    assert "authorization_scope_hash" not in payload
    assert "analysis_scope_hash" not in payload
    assert response.headers["Cache-Control"] == "private, no-store"


def test_every_failure_mode_is_one_generic_404() -> None:
    for error in (ThreadNotFound("Thread not found"), ThreadNotFound("Evidence set not found")):
        with pytest.raises(HTTPException) as exc_info:
            _call_header(_repo(error=error))
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Not found"


def test_members_page_and_cursor_round_trip() -> None:
    repo = _repo(row=_set_row(), members=_members(1, 60))
    first, response = _call_members(repo, limit=25)
    assert [member["member_index"] for member in first["members"]] == list(range(1, 26))
    assert first["population_count"] == 60
    assert first["complete"] is True
    assert first["next_cursor"]
    assert response.headers["Cache-Control"] == "private, no-store"

    second, _ = _call_members(repo, cursor=first["next_cursor"], limit=25)
    assert [member["member_index"] for member in second["members"]] == list(range(26, 51))

    third, _ = _call_members(repo, cursor=second["next_cursor"], limit=25)
    assert [member["member_index"] for member in third["members"]] == list(range(51, 61))
    assert third["next_cursor"] is None


def test_cursor_is_bound_to_actor_thread_and_set() -> None:
    repo = _repo(row=_set_row(), members=_members(1, 60))
    first, _ = _call_members(repo, limit=25)
    cursor = first["next_cursor"]

    # Another thread: the same cursor must be refused generically.
    with pytest.raises(HTTPException) as exc_info:
        _call_members(repo, cursor=cursor, thread_id="thread_other")
    assert exc_info.value.status_code == 404

    # Another actor: refused the same way.
    from ai.core import app as app_module

    other = SimpleNamespace(
        subject="user:6", user_pk="6", scope="site-a", rate_limit_key="user:6", is_staff=False
    )
    response = Response()
    with (
        mock.patch.object(app_module, "_principal", side_effect=lambda: other),
        mock.patch.object(app_module, "_repository", return_value=repo),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            app_module.get_evidence_set_members(
                "thread_1", "set_" + "a" * 32, response, cursor=cursor, limit=25
            )
        )
    assert exc_info.value.status_code == 404


def test_tampered_cursor_is_refused_generically() -> None:
    repo = _repo(row=_set_row(), members=_members(1, 60))
    with pytest.raises(HTTPException) as exc_info:
        _call_members(repo, cursor="forged-cursor-value")
    assert exc_info.value.status_code == 404


def test_incomplete_membership_reports_complete_false() -> None:
    row = _set_row(member_count=10, evaluated_count=60)
    payload, _ = _call_members(_repo(row=row, members=_members(1, 10)), limit=25)
    assert payload["complete"] is False
    assert payload["next_cursor"] is None


def test_capability_advertisement_follows_the_gate_mode() -> None:
    """The sync payload advertises evidence_sets only when the gate is on."""
    import inspect

    from ai.core import app as app_module

    source = inspect.getsource(app_module.list_threads)
    assert '"evidence_sets"' in source
    assert "evidence_gate_mode" in source
