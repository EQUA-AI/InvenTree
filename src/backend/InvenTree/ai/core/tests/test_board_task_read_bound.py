"""RV05: the board-task read page honors the existing 25-result ceiling.

Finding RV05: ``tasks.ai_read.kanban_cards_page`` materializes
``order_by('pk')[:max(int(limit), 0)]`` with NO upper bound, so a caller
passing ``limit > MAX_SEARCH_RESULTS`` gets a bulk dump past the module's
own declared bound ("a prompt gets a readable page, never a dump",
``MAX_SEARCH_RESULTS = 25``). This module promotes one real excessive-limit
regression through the real reader AND the actual async ``list_board_tasks``
seam, paired with an ordinary-limit control and unchanged
ordinary-principal/denial controls.

Real seam only: real Django ORM rows (Client / AssetMachine / WorkOrder /
KanbanCard), real ``work_order_scope_filter`` authorization served through
the PRODUCTION grant seam ``AIMMS_MAINTENANCE_SCOPE_RESOLVER`` (a configured
resolver, not a mocked authorization predicate), the real reader
``tasks.ai_read.kanban_cards_page`` and the real async tool
``ai.core.integrations.kanban_tools.list_board_tasks`` under the real
``ai.core.auth.principal_context``. Disclosed settings seams:
``AIMMS_MAINTENANCE_AI_READ_ENABLED`` and the resolver path above. Rows live
only in the owned disposable island SQLite provisioned by
``ai.core.tests.focus_test_db.ensure_island_tables`` — no authenticated or
live-database claim.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

import django

django.setup()

from ai.core.analysis.scope import (  # noqa: E402
    MODE_EXPLICIT,
    SCHEMA_VERSION,
    SOURCE_CLASSES,
)
from ai.core.analysis.scope_context import (  # noqa: E402
    bind_turn_scope,
    turn_scope_context,
)
from ai.core.auth import AIPrincipal, principal_context  # noqa: E402
from ai.core.integrations.kanban_tools import list_board_tasks  # noqa: E402
from ai.core.tests.focus_test_db import ensure_island_tables  # noqa: E402
from asgiref.sync import async_to_sync  # noqa: E402
from assets.models import AssetMachine, Client  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import override_settings  # noqa: E402
from tasks import ai_read  # noqa: E402
from tasks.models import KanbanCard, WorkOrder  # noqa: E402
from tasks.scope import MaintenanceScope, work_order_scope_filter  # noqa: E402

READ_FLAGS = {"AIMMS_MAINTENANCE_AI_READ_ENABLED": True}

# Grants are served the way production serves them: through the configured
# resolver, keyed by username (the tools reload the actor by primary key).
_GRANTS: dict[str, set[MaintenanceScope]] = {}


def _grant_resolver(actor):
    """Resolver seam: return whatever the test granted this username."""
    return _GRANTS.get(actor.get_username(), set())


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f"user:{user.pk}",
        actor=f"user:{user.pk}",
        user_pk=str(user.pk),
        username=user.get_username(),
        authentication_method="session",
        scope="chat",
        policy_version="test",
        is_staff=bool(user.is_staff),
        is_superuser=bool(user.is_superuser),
    )


@contextmanager
def _acting_as(principal: AIPrincipal | None):
    """Bind (or clear) the boundary principal for the duration of a call."""
    token = principal_context.set(principal)
    try:
        yield
    finally:
        principal_context.reset(token)


@contextmanager
def _turn_scope(*machine_ids):
    """Bind a real explicit analysis scope (production ``bind_turn_scope``)."""
    snapshot = {
        "scope": {
            "schema_version": SCHEMA_VERSION,
            "mode": MODE_EXPLICIT,
            "machine_ids": list(machine_ids),
            "date_window": {"from": None, "to": None},
            "source_classes": list(SOURCE_CLASSES),
            "display_label": "test analysis scope",
        },
        "version": 1,
        "hash": "b" * 64,
    }
    token = turn_scope_context.set(None)
    try:
        bind_turn_scope(snapshot, thread_pk=None, turn_pk=77)
        yield
    finally:
        turn_scope_context.reset(token)


@pytest.fixture(scope="module", autouse=True)
def _database():
    ensure_island_tables()
    yield


@pytest.fixture()
def read_flags():
    """Disclosed production settings seams for every test in this module."""
    with override_settings(
        **READ_FLAGS,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER=f"{__name__}._grant_resolver",
    ):
        _GRANTS.clear()
        yield
        _GRANTS.clear()


@pytest.fixture(scope="module")
def board():
    """Two tenants; 30 authorized matching cards + foreign look-alikes.

    Deliberate disjoint PK ranges: every explicit card PK differs from every
    work-order PK (especially its own parent's), so nothing can pass on
    equal-numbered identities. The search token matches CARD titles only.
    """
    suffix = uuid.uuid4().hex[:6]
    token = f"rv05bound{suffix}"
    client_a = Client.objects.create(name=f"RV05 Client A {suffix}", code=f"rv05-a-{suffix}")
    client_b = Client.objects.create(name=f"RV05 Client B {suffix}", code=f"rv05-b-{suffix}")
    machine_a = AssetMachine.objects.create(name=f"RV05 pump A {suffix}", client=client_a)
    machine_b = AssetMachine.objects.create(name=f"RV05 press B {suffix}", client=client_b)
    base = (
        max(
            WorkOrder.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
            KanbanCard.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
        )
        + 1000
    )
    wo_a = WorkOrder.objects.create(
        pk=base + 1,
        title=f"RV05 authorized job {suffix}",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=machine_a,
    )
    wo_b = WorkOrder.objects.create(
        pk=base + 2,
        title=f"RV05 foreign job {suffix}",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=machine_b,
    )
    # WorkOrder.save() creates tracking cards. Reserve explicit IDs after
    # those inserts: SQLite's allocator can outlive previously deleted rows.
    last_card_id = KanbanCard.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
    card_base = max(base + 100, last_card_id + 1)
    cards = [
        KanbanCard.objects.create(
            pk=card_base + index,
            work_order=wo_a,
            title=f"{token} probe {index:02d}",
            status="backlog",
        )
        for index in range(30)
    ]
    foreign_cards = [
        KanbanCard.objects.create(
            pk=card_base + 400 + index,
            work_order=wo_b,
            title=f"{token} foreign probe {index:02d}",
            status="backlog",
        )
        for index in range(3)
    ]
    actor = get_user_model().objects.create_user(
        username=f"rv05-actor-{suffix}",
        email=f"rv05-{suffix}@example.com",
        password="pw",
    )
    denied = get_user_model().objects.create_user(
        username=f"rv05-denied-{suffix}",
        email=f"rv05-denied-{suffix}@example.com",
        password="pw",
    )
    return SimpleNamespace(
        token=token,
        suffix=suffix,
        client_a=client_a,
        machine_a=machine_a,
        machine_b=machine_b,
        wo_a=wo_a,
        wo_b=wo_b,
        cards=cards,
        foreign_cards=foreign_cards,
        actor=actor,
        actor_principal=_principal(actor),
        denied=denied,
        denied_principal=_principal(denied),
    )


def _grant_client_a(board) -> None:
    _GRANTS[board.actor.get_username()] = {
        MaintenanceScope(customer_id=None, site_key=None, client_id=board.client_a.pk)
    }


def _call_tool(principal, **kwargs):
    with _acting_as(principal):
        return async_to_sync(list_board_tasks)(**kwargs)


# -- RV05 behavioral RED/GREEN: the 25-result ceiling is real ---------------


def test_reader_caps_excessive_limit_at_max_search_results(read_flags, board):
    """limit=100 must never materialize more than MAX_SEARCH_RESULTS rows."""
    _grant_client_a(board)
    page = ai_read.kanban_cards_page(board.actor, query=board.token, limit=100)
    # Truthful population: every authorized matching card is counted.
    assert page["population_count"] == 30
    assert len(page["rows"]) == ai_read.MAX_SEARCH_RESULTS
    assert page["returned_count"] == ai_read.MAX_SEARCH_RESULTS
    assert page["display_truncated"] is True
    # Canonical identities preserved on every returned row.
    ids = [row["kanban_card_id"] for row in page["rows"]]
    assert ids == sorted(ids)
    assert ids == [card.pk for card in board.cards[:25]]
    for row in page["rows"]:
        assert row["work_order_id"] == board.wo_a.pk
        assert row["kanban_card_id"] != row["work_order_id"]
        assert "id" not in row


def test_tool_seam_caps_excessive_limit_and_reports_truthful_coverage(read_flags, board):
    """The async tool seam returns the bounded rows and honest counts."""
    _grant_client_a(board)
    result = _call_tool(board.actor_principal, search=board.token, limit=100)
    assert result["population_count"] == 30
    assert result["returned_count"] == ai_read.MAX_SEARCH_RESULTS
    assert len(result["tasks"]) == ai_read.MAX_SEARCH_RESULTS
    assert result["display_truncated"] is True
    coverage = result["retrieval"]["coverage"]
    assert coverage["population_count"] == 30
    assert coverage["returned_count"] == ai_read.MAX_SEARCH_RESULTS
    assert coverage["complete_population"] is False
    assert coverage["display_truncated"] is True
    ids = [row["kanban_card_id"] for row in result["tasks"]]
    assert ids == [card.pk for card in board.cards[:25]]
    for row in result["tasks"]:
        assert row["work_order_id"] == board.wo_a.pk
        assert row["kanban_card_id"] != row["work_order_id"]


# -- paired controls: everything else stays exactly as before ---------------


def test_ordinary_limit_control_stays_exact(read_flags, board):
    """An ordinary limit below the ceiling is still honored exactly."""
    _grant_client_a(board)
    page = ai_read.kanban_cards_page(board.actor, query=board.token, limit=7)
    assert page["population_count"] == 30
    assert len(page["rows"]) == 7
    assert page["returned_count"] == 7
    assert page["display_truncated"] is True
    assert [row["kanban_card_id"] for row in page["rows"]] == [card.pk for card in board.cards[:7]]


def test_denied_and_missing_principal_controls_stay_closed(read_flags, board):
    """No grant, no principal and a deleted principal all fail closed."""
    page = ai_read.kanban_cards_page(board.denied, query=board.token, limit=100)
    assert page == {"rows": [], "population_count": 0, "returned_count": 0}
    denied = _call_tool(board.denied_principal, search=board.token, limit=100)
    assert denied["tasks"] == []
    assert denied["population_count"] == 0
    assert denied["returned_count"] == 0
    missing = _call_tool(
        AIPrincipal(
            subject="user:4242424242",
            actor="user:4242424242",
            user_pk="4242424242",
            username="rv05-deleted",
            authentication_method="session",
            scope="chat",
            policy_version="test",
            is_staff=False,
            is_superuser=False,
        ),
        search=board.token,
        limit=100,
    )
    assert missing["tasks"] == []
    assert missing["population_count"] == 0
    none = _call_tool(None, search=board.token, limit=100)
    assert none["tasks"] == []
    assert none["population_count"] == 0
    # Foreign look-alike rows never ride an authorized read.
    _grant_client_a(board)
    page = ai_read.kanban_cards_page(board.actor, query=board.token)
    assert page["population_count"] == 30
    assert all(row["work_order_id"] == board.wo_a.pk for row in page["rows"])
    assert not any(
        card.pk in {row["kanban_card_id"] for row in page["rows"]} for card in board.foreign_cards
    )


# -- expanded RV05 controls -------------------------------------------------


def test_zero_negative_and_default_limits_keep_semantics(read_flags, board):
    """Zero/negative stay empty pages; the default page is exactly 25."""
    _grant_client_a(board)
    for bad_limit in (0, -5):
        page = ai_read.kanban_cards_page(board.actor, query=board.token, limit=bad_limit)
        assert page["rows"] == []
        assert page["returned_count"] == 0
        assert page["population_count"] == 30
        assert page["display_truncated"] is True
    defaulted = ai_read.kanban_cards_page(board.actor, query=board.token)
    assert len(defaulted["rows"]) == 25
    assert defaulted["returned_count"] == 25
    assert defaulted["population_count"] == 30
    assert defaulted["display_truncated"] is True
    empty = _call_tool(board.actor_principal, search=board.token, limit=0)
    assert empty["tasks"] == []
    assert empty["returned_count"] == 0
    assert empty["population_count"] == 30
    assert empty["retrieval"]["coverage"]["returned_count"] == 0
    assert empty["retrieval"]["coverage"]["population_count"] == 30


def test_population_count_equals_real_authorized_count(read_flags, board):
    """population_count is the real authorized COUNT, not the page size."""
    _grant_client_a(board)
    real_count = KanbanCard.objects.filter(
        work_order__in=WorkOrder.objects.filter(work_order_scope_filter(board.actor)),
        is_active=True,
        title__icontains=board.token,
    ).count()
    assert real_count == 30
    page = ai_read.kanban_cards_page(board.actor, query=board.token, limit=100)
    assert page["population_count"] == real_count


def test_explicit_scope_narrowing_still_applies_under_the_bound(read_flags, board):
    """The analysis fence narrows authorized reads; the bound never expands."""
    _grant_client_a(board)
    with _turn_scope(board.machine_a.pk):
        page = ai_read.kanban_cards_page(board.actor, query=board.token, limit=100)
    assert page["population_count"] == 30
    assert len(page["rows"]) == 25
    with _turn_scope(board.machine_b.pk):
        narrowed = _call_tool(board.actor_principal, search=board.token, limit=100)
    assert narrowed["population_count"] == 0
    assert narrowed["tasks"] == []
    assert narrowed["returned_count"] == 0


def test_reads_persist_no_business_effects(read_flags, board):
    """A bounded read changes no row counts anywhere (read-only seam)."""
    _grant_client_a(board)
    before = (
        WorkOrder.objects.count(),
        KanbanCard.objects.count(),
        get_user_model().objects.count(),
    )
    ai_read.kanban_cards_page(board.actor, query=board.token, limit=100)
    _call_tool(board.actor_principal, search=board.token, limit=100)
    after = (
        WorkOrder.objects.count(),
        KanbanCard.objects.count(),
        get_user_model().objects.count(),
    )
    assert after == before
