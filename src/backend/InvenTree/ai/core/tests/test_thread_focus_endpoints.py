"""``GET/PUT /threads/{id}/focus`` route behavior (owner-bound, optimistic).

Endpoint semantics on top of the repository contract: 404 for a thread
outside the boundary (never leaking focus state), 409 ``focus_version_conflict``
for a stale ``expected_focus_version``, 400 ``focus_update_rejected`` for every
unknown/unauthorized/unavailable candidate (one generic detail — existence is
never disclosed), 400 with a safe message for malformed shapes, and the
canonical projection echoed on success. The endpoint mirrors the scope-rail
route shape exactly (client-minted thread ids materialize before the write).
"""

# ruff: noqa: E402

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core import app as ai_app
from ai.core.focus import resolution as focus_resolution
from ai.core.focus.wire import ThreadFocusPayload
from ai.core.tests.focus_test_db import ensure_island_tables
from fastapi import HTTPException


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()
    yield


@pytest.fixture(autouse=True)
def _deny_by_default(monkeypatch):
    monkeypatch.setattr(focus_resolution, "read_machine", lambda _user, _ref: None)
    monkeypatch.setattr(focus_resolution, "read_work_order", lambda _user, _ref: None)
    monkeypatch.setattr(focus_resolution, "read_kanban_card", lambda _user, _ref: None)


@pytest.fixture
def principal(monkeypatch):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username=f"focus-api-{os.urandom(4).hex()}")
    value = SimpleNamespace(
        subject=f"user:{user.pk}", user_pk=user.pk, scope="site:main", policy_version="v1"
    )
    monkeypatch.setattr(ai_app, "_principal", lambda: value)
    return value


@pytest.fixture
def allow_machines(monkeypatch):
    """Authorize ONLY the fixture's real machines (3 and 4) — nothing else.

    A blanket permissive reader would defeat the point of the rejection
    tests: unknown ids (999) must stay denied.
    """

    def read_machine(user, machine_id):
        if machine_id not in (3, 4):
            return None
        return SimpleNamespace(pk=machine_id, name=f"Machine {machine_id}")

    monkeypatch.setattr(focus_resolution, "read_machine", read_machine)


def _put(thread_id, body):
    import asyncio

    return asyncio.run(ai_app.set_thread_focus(thread_id, ai_app.ThreadFocusUpdateRequest(**body)))


def _get(thread_id):
    import asyncio

    return asyncio.run(ai_app.get_thread_focus(thread_id))


def test_new_thread_reads_no_focus(principal) -> None:
    # A GET on a NONEXISTENT thread is a 404 (pinned below); a real new
    # thread is materialized first, exactly like the client-minted flow.
    from aichat.services.threads import ThreadRepository

    owner = ThreadRepository(principal.user_pk, principal.scope)
    owner.get_or_create("thread_focus_api_1")
    assert _get("thread_focus_api_1") == {
        "focus": None,
        "focus_version": 0,
        "focus_unavailable": False,
    }


def test_unknown_thread_is_404_and_discloses_nothing(principal, monkeypatch) -> None:
    from aichat.services.threads import ThreadRepository

    owner = ThreadRepository(principal.user_pk, principal.scope)
    thread, _ = owner.get_or_create("thread_focus_api_owner")
    from django.contrib.auth import get_user_model

    other = get_user_model().objects.create_user(username=f"other-{os.urandom(4).hex()}")
    monkeypatch.setattr(
        ai_app,
        "_principal",
        lambda: SimpleNamespace(
            subject=f"user:{other.pk}",
            user_pk=other.pk,
            scope="site:main",
            policy_version="v1",
        ),
    )
    with pytest.raises(HTTPException) as exc:
        _get(thread.pk)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Thread not found"


def test_put_focus_echoes_canonical_projection(principal, allow_machines) -> None:
    result = _put(
        "thread_focus_api_2",
        {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 0},
    )
    assert result == {
        "focus": {
            "kind": "machine",
            "id": 3,
            "label": "Machine 3",
            "machine_id": 3,
            "work_order_id": None,
        },
        "focus_version": 1,
        "focus_unavailable": False,
    }
    assert _get("thread_focus_api_2") == result
    # The wire mirror validates the live endpoint payload byte-for-shape.
    assert ThreadFocusPayload.model_validate(result).model_dump(mode="json") == result


def test_put_focus_materializes_a_client_minted_thread_id(principal, allow_machines) -> None:
    """The machine-page launch flow sets focus BEFORE the first send."""
    result = _put(
        "thread_focus_api_minted",
        {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 0},
    )
    assert result["focus_version"] == 1


def test_stale_expected_version_is_409_and_changes_nothing(principal, allow_machines) -> None:
    _put("thread_focus_api_3", {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 0})
    with pytest.raises(HTTPException) as exc:
        _put(
            "thread_focus_api_3",
            {"focus": {"kind": "machine", "id": 4}, "expected_focus_version": 0},
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "focus_version_conflict"
    state = _get("thread_focus_api_3")
    assert state["focus_version"] == 1
    assert state["focus"]["id"] == 3


def test_unknown_or_unauthorized_candidate_is_one_generic_400(principal, allow_machines) -> None:
    for ref in (
        {"kind": "machine", "id": 999},
        {"kind": "work_order", "id": 999},
        {"kind": "kanban_card", "id": 999},
    ):
        with pytest.raises(HTTPException) as exc:
            _put("thread_focus_api_4", {"focus": ref, "expected_focus_version": 0})
        assert exc.value.status_code == 400
        assert exc.value.detail == "focus_update_rejected"
    assert _get("thread_focus_api_4") == {
        "focus": None,
        "focus_version": 0,
        "focus_unavailable": False,
    }


def test_malformed_shape_is_400_with_a_safe_message(principal) -> None:
    with pytest.raises(HTTPException) as exc:
        _put(
            "thread_focus_api_5",
            {"focus": {"kind": "task", "id": 3}, "expected_focus_version": 0},
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "Unknown focus kind"


def test_clear_focus_via_null(principal, allow_machines) -> None:
    _put("thread_focus_api_6", {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 0})
    result = _put("thread_focus_api_6", {"focus": None, "expected_focus_version": 1})
    assert result == {"focus": None, "focus_version": 2, "focus_unavailable": False}


def test_identical_reselection_is_idempotent_on_the_wire(principal, allow_machines) -> None:
    first = _put(
        "thread_focus_api_7", {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 0}
    )
    again = _put(
        "thread_focus_api_7", {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 1}
    )
    assert again == first
    assert again["focus_version"] == 1


# ---- gap 8: explicit-scope conflict is a safe refusal (state unchanged) ----


def test_out_of_analysis_scope_target_is_refused_with_its_code(principal, allow_machines) -> None:
    from ai.core.analysis import scope as scope_contract
    from aichat.models import ChatThread
    from aichat.services.threads import ThreadRepository

    owner = ThreadRepository(principal.user_pk, principal.scope)
    owner.get_or_create("thread_focus_api_scope")
    # An EXPLICIT machine fence (stored exactly as the scope rail persists
    # it; the scope write path itself belongs to the scope lane).
    stored_scope = scope_contract.scope_to_payload(
        scope_contract.AnalysisScope(mode=scope_contract.MODE_EXPLICIT, machine_ids=(1,))
    )
    ChatThread.objects.filter(pk="thread_focus_api_scope").update(
        analysis_scope=stored_scope, analysis_scope_version=1
    )
    with pytest.raises(HTTPException) as exc:
        _put(
            "thread_focus_api_scope",
            {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 0},
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "focus_out_of_analysis_scope"
    # State unchanged and the scope version is never advanced.
    assert _get("thread_focus_api_scope") == {
        "focus": None,
        "focus_version": 0,
        "focus_unavailable": False,
    }
    assert owner.get_scope("thread_focus_api_scope")["version"] == 1


# ---- gap 4: un-set-aside-able decision refuses the PUT fail-closed -------


def test_set_aside_failure_refuses_the_put_with_its_code(
    principal, allow_machines, monkeypatch
) -> None:
    class _Coordinator:
        def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
            raise RuntimeError("store contention")

    monkeypatch.setattr("ai.core.decisions.pipeline.enabled", lambda: True)
    monkeypatch.setattr("ai.core.decisions.coordinator.get_coordinator", lambda: _Coordinator())
    with pytest.raises(HTTPException) as exc:
        _put(
            "thread_focus_api_setaside",
            {"focus": {"kind": "machine", "id": 3}, "expected_focus_version": 0},
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "focus_decision_not_set_aside"
    assert _get("thread_focus_api_setaside") == {
        "focus": None,
        "focus_version": 0,
        "focus_unavailable": False,
    }


# ---- gap 7: GET /focus/candidates (bounded, authorized, has_more) --------


def _candidates(kind, query, limit=8):
    import asyncio

    return asyncio.run(ai_app.list_focus_candidates(kind=kind, query=query, limit=limit))


def test_focus_candidates_endpoint_returns_bounded_authorized_page(
    principal, allow_machines, monkeypatch
) -> None:
    monkeypatch.setattr(
        focus_resolution, "search_machines", lambda _user, _query, _limit: [3, 9, 4]
    )
    page = _candidates("machine", "Machine")
    assert [candidate["id"] for candidate in page["candidates"]] == [3, 4]
    assert page["returned_count"] == 2
    assert page["has_more"] is False
    # Canonical labels come from the READER, not from search rows.
    assert page["candidates"][0]["label"] == "Machine 3"


def test_focus_candidates_endpoint_reports_has_more(principal, monkeypatch) -> None:
    monkeypatch.setattr(
        focus_resolution,
        "read_machine",
        lambda _user, ref: SimpleNamespace(pk=ref, name=f"Machine {ref}"),
    )
    monkeypatch.setattr(
        focus_resolution, "search_machines", lambda _user, _query, _limit: [3, 4, 5]
    )
    page = _candidates("machine", "Machine", limit=2)
    assert page["returned_count"] == 2
    assert page["has_more"] is True


def test_focus_candidates_endpoint_rejects_unknown_kind(principal) -> None:
    with pytest.raises(HTTPException) as exc:
        _candidates("task", "pump")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Unknown focus kind"
