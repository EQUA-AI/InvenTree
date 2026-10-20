"""RV04: explicit machine scope excludes unassigned board tasks consistently.

Owner policy (``rv04-policy-decision.json``): **Exclude unassigned work orders
while an explicit machine scope is active**, applied consistently to the
board-task list, detail and stock readers. Population narrowing happens AFTER
the actor's maintenance authorization and never in place of it:

* a null-machine (unassigned) work order that is LEGITIMATELY actor-authorized
  through its explicit ``WorkOrder.customer`` (company.Company FK) matching a
  real ``MaintenanceScope(customer_id=..., site_key=None)`` must be excluded by
  ``get_board_task`` / ``check_board_task_stock`` whenever an explicit machine
  scope is bound — exactly as the list reader
  (``tasks.ai_read.kanban_cards_page``) already excludes it;
* with NO explicit scope the same authorized unassigned record keeps reading
  normally on list, detail and stock;
* ordinary authorization, foreign/missing/unresolved/no-principal/deleted
  actor denials, distinct card/parent identities, truthful bounded lists and
  compute-only stock are unchanged.

Real seam only: ordinary persisted users, real configured username-keyed grant
resolver (the tools reload the actor by primary key), the real
``ai.core.auth.principal_context`` / ``bind_turn_scope`` production seams, real
Django ORM rows (Client / AssetMachine / company.Company / WorkOrder /
KanbanCard) and the actual async tools ``get_board_task``,
``check_board_task_stock`` and ``list_board_tasks``. Rows live only in the
owned disposable island SQLite provisioned by
``ai.core.tests.focus_test_db.ensure_island_tables`` — no authenticated or
live-database claim. Stock assertions cover identity, scoping and the
compute-only rule only; this tracer makes no nonempty-allocation claim.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal
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
    current_turn_scope,
    turn_scope_context,
)
from ai.core.auth import AIPrincipal, principal_context  # noqa: E402
from ai.core.integrations.kanban_tools import (  # noqa: E402
    _scope_machine_ids,
    check_board_task_stock,
    get_board_task,
    list_board_tasks,
)
from ai.core.tests.focus_test_db import ensure_island_tables  # noqa: E402
from asgiref.sync import async_to_sync, sync_to_async  # noqa: E402
from assets.models import AssetMachine, Client  # noqa: E402
from company.models import Company  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import override_settings  # noqa: E402
from part.models import Part  # noqa: E402
from stock.models import StockItem  # noqa: E402
from tasks.models import KanbanCard, WorkOrder, WorkOrderPart  # noqa: E402
from tasks.scope import MaintenanceScope  # noqa: E402

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
        "hash": "c" * 64,
    }
    token = turn_scope_context.set(None)
    try:
        bind_turn_scope(snapshot, thread_pk=None, turn_pk=78)
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
    """One actor, two tenants, one customer relationship, deliberate PKs.

    Records (all card titles carry the module search token):

    * ``wo_unassigned`` / ``card_unassigned`` — UNASSIGNED parent (machine is
      NULL) with an explicit customer the actor is granted: legitimately
      authorized, and the RV04 policy record.
    * ``wo_machine_a`` / ``card_machine_a`` — assigned to machine_a (client_a),
      the actor's client grant: the positive control.
    * ``wo_machine_b`` / ``card_machine_b`` — machine_b (client_b): foreign.
    * ``wo_customer_y`` / ``card_customer_y`` — unassigned with a DIFFERENT
      customer: foreign, denied by authorization alone.
    * ``wo_bare`` / ``card_bare`` — unassigned with no customer at all: its
      scope is unresolved and it is already denied (it cannot expose the
      defect).

    Deliberate disjoint PK ranges: every card PK differs from every work-order
    PK (especially its own parent's), so nothing can pass on equal-numbered
    identities.
    """
    suffix = uuid.uuid4().hex[:6]
    token = f"rv04scope{suffix}"
    client_a = Client.objects.create(name=f"RV04 Client A {suffix}", code=f"rv04-a-{suffix}")
    client_b = Client.objects.create(name=f"RV04 Client B {suffix}", code=f"rv04-b-{suffix}")
    machine_a = AssetMachine.objects.create(name=f"RV04 pump A {suffix}", client=client_a)
    machine_b = AssetMachine.objects.create(name=f"RV04 press B {suffix}", client=client_b)
    customer_x = Company.objects.create(
        name=f"RV04 Customer X {suffix}",
        description=f"RV04 customer X {suffix}",
        is_customer=True,
    )
    customer_y = Company.objects.create(
        name=f"RV04 Customer Y {suffix}",
        description=f"RV04 customer Y {suffix}",
        is_customer=True,
    )
    base = (
        max(
            WorkOrder.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
            KanbanCard.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
        )
        + 1000
    )
    wo_unassigned = WorkOrder.objects.create(
        pk=base + 1,
        title=f"RV04 unassigned job {suffix}",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=None,
        customer=customer_x,
    )
    wo_machine_a = WorkOrder.objects.create(
        pk=base + 2,
        title=f"RV04 assigned job A {suffix}",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=machine_a,
    )
    wo_machine_b = WorkOrder.objects.create(
        pk=base + 3,
        title=f"RV04 foreign job B {suffix}",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=machine_b,
    )
    wo_customer_y = WorkOrder.objects.create(
        pk=base + 4,
        title=f"RV04 foreign customer job {suffix}",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=None,
        customer=customer_y,
    )
    wo_bare = WorkOrder.objects.create(
        pk=base + 5,
        title=f"RV04 bare job {suffix}",
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
        machine=None,
        customer=None,
    )

    def _card(pk, work_order, label):
        return KanbanCard.objects.create(
            pk=pk,
            work_order=work_order,
            title=f"{token} {label}",
            status="backlog",
        )

    card_unassigned = _card(base + 101, wo_unassigned, "card unassigned")
    card_machine_a = _card(base + 102, wo_machine_a, "card A")
    card_machine_b = _card(base + 103, wo_machine_b, "card B")
    card_customer_y = _card(base + 104, wo_customer_y, "card foreign customer")
    card_bare = _card(base + 105, wo_bare, "card bare")
    actor = get_user_model().objects.create_user(
        username=f"rv04-actor-{suffix}",
        email=f"rv04-{suffix}@example.com",
        password="pw",
    )
    denied = get_user_model().objects.create_user(
        username=f"rv04-denied-{suffix}",
        email=f"rv04-denied-{suffix}@example.com",
        password="pw",
    )
    yield SimpleNamespace(
        token=token,
        suffix=suffix,
        client_a=client_a,
        client_b=client_b,
        customer_x=customer_x,
        customer_y=customer_y,
        machine_a=machine_a,
        machine_b=machine_b,
        wo_unassigned=wo_unassigned,
        wo_machine_a=wo_machine_a,
        wo_machine_b=wo_machine_b,
        wo_customer_y=wo_customer_y,
        wo_bare=wo_bare,
        card_unassigned=card_unassigned,
        card_machine_a=card_machine_a,
        card_machine_b=card_machine_b,
        card_customer_y=card_customer_y,
        card_bare=card_bare,
        actor=actor,
        actor_principal=_principal(actor),
        denied=denied,
        denied_principal=_principal(denied),
    )
    # Final cleanup without changing any pre-existing seed rows.
    KanbanCard.objects.filter(
        pk__in=[
            card_unassigned.pk,
            card_machine_a.pk,
            card_machine_b.pk,
            card_customer_y.pk,
            card_bare.pk,
        ]
    ).delete()
    WorkOrder.objects.filter(
        pk__in=[
            wo_unassigned.pk,
            wo_machine_a.pk,
            wo_machine_b.pk,
            wo_customer_y.pk,
            wo_bare.pk,
        ]
    ).delete()
    AssetMachine.objects.filter(pk__in=[machine_a.pk, machine_b.pk]).delete()
    Client.objects.filter(pk__in=[client_a.pk, client_b.pk]).delete()
    Company.objects.filter(pk__in=[customer_x.pk, customer_y.pk]).delete()
    get_user_model().objects.filter(pk__in=[actor.pk, denied.pk]).delete()


def _grant(board) -> None:
    """Real grants: the actor's customer relationship AND client_a."""
    _GRANTS[board.actor.get_username()] = {
        MaintenanceScope(customer_id=board.customer_x.pk, site_key=None, client_id=None),
        MaintenanceScope(customer_id=None, site_key=None, client_id=board.client_a.pk),
    }


def _call_detail(principal, kanban_card_id):
    with _acting_as(principal):
        return async_to_sync(get_board_task)(kanban_card_id=kanban_card_id)


def _call_stock(principal, kanban_card_id):
    with _acting_as(principal):
        return async_to_sync(check_board_task_stock)(kanban_card_id=kanban_card_id)


def _call_list(principal, **kwargs):
    with _acting_as(principal):
        return async_to_sync(list_board_tasks)(**kwargs)


def _not_found(kanban_card_id):
    return {"error": f"Board task {kanban_card_id} not found."}


# -- RV04 behavioral RED: the policy record leaks through detail/stock -------


def test_explicit_machine_scope_excludes_customer_authorized_unassigned_detail_and_stock(
    read_flags, board
):
    """Under an explicit machine scope the authorized UNASSIGNED record is out.

    The parent is legitimately actor-authorized through its explicit customer
    (a real customer MaintenanceScope, no superuser, no mocked authorization),
    so this is a POPULATION narrowing, never a permission change: the same
    record reads normally without an explicit scope (see the paired control).
    """
    _grant(board)
    with _turn_scope(board.machine_a.pk):
        detail = _call_detail(board.actor_principal, board.card_unassigned.pk)
        stock = _call_stock(board.actor_principal, board.card_unassigned.pk)
        page = _call_list(board.actor_principal, search=board.token)
    # Detail and stock must behave exactly like the list reader already does.
    assert detail == _not_found(board.card_unassigned.pk)
    assert stock == _not_found(board.card_unassigned.pk)
    listed = [row["kanban_card_id"] for row in page["tasks"]]
    assert board.card_unassigned.pk not in listed
    assert listed == [board.card_machine_a.pk]
    assert page["population_count"] == 1
    assert page["returned_count"] == 1


# -- observable positive control: the selected machine keeps reading ---------


def test_explicit_machine_scope_selected_machine_positive_control(read_flags, board):
    """Inside the explicit machine scope the assigned record still reads."""
    _grant(board)
    with _turn_scope(board.machine_a.pk):
        bound = current_turn_scope()
        assert bound is not None
        assert bound.explicit
        assert set(bound.machine_ids) == {board.machine_a.pk}
        # The tools re-read the scope from their own sync worker thread; the
        # probe must observe the same binding the tool seam observes.
        probe = async_to_sync(sync_to_async(_scope_machine_ids, thread_sensitive=True))()
        assert probe == [board.machine_a.pk]
        detail = _call_detail(board.actor_principal, board.card_machine_a.pk)
        stock = _call_stock(board.actor_principal, board.card_machine_a.pk)
        page = _call_list(board.actor_principal, search=board.token)
    assert "error" not in detail
    assert detail["kanban_card_id"] == board.card_machine_a.pk
    assert detail["work_order_id"] == board.wo_machine_a.pk
    assert detail["kanban_card_id"] != detail["work_order_id"]
    assert "id" not in detail
    assert "error" not in stock
    assert stock["kanban_card_id"] == board.card_machine_a.pk
    assert stock["work_order_id"] == board.wo_machine_a.pk
    assert stock["parts"] == []
    assert stock["warnings"] == []
    assert [row["kanban_card_id"] for row in page["tasks"]] == [board.card_machine_a.pk]
    assert page["population_count"] == 1
    assert page["returned_count"] == 1


# -- no explicit scope: the authorized unassigned record keeps reading -------


def test_no_explicit_scope_customer_authorized_unassigned_success(read_flags, board):
    """Without an explicit scope the authorized unassigned record is visible."""
    _grant(board)
    detail = _call_detail(board.actor_principal, board.card_unassigned.pk)
    stock = _call_stock(board.actor_principal, board.card_unassigned.pk)
    page = _call_list(board.actor_principal, search=board.token)
    assert "error" not in detail
    assert detail["kanban_card_id"] == board.card_unassigned.pk
    assert detail["work_order_id"] == board.wo_unassigned.pk
    assert detail["kanban_card_id"] != detail["work_order_id"]
    assert "error" not in stock
    assert stock["kanban_card_id"] == board.card_unassigned.pk
    assert stock["work_order_id"] == board.wo_unassigned.pk
    listed = {row["kanban_card_id"] for row in page["tasks"]}
    assert listed == {
        board.card_unassigned.pk,
        board.card_machine_a.pk,
    }
    assert page["population_count"] == 2
    assert page["returned_count"] == 2


def test_explicit_scope_other_machine_excludes_authorized_assigned_record(read_flags, board):
    """An explicit scope for a different machine narrows the assigned record."""
    _grant(board)
    with _turn_scope(board.machine_b.pk):
        detail = _call_detail(board.actor_principal, board.card_machine_a.pk)
        stock = _call_stock(board.actor_principal, board.card_machine_a.pk)
        page = _call_list(board.actor_principal, search=board.token)
    assert detail == _not_found(board.card_machine_a.pk)
    assert stock == _not_found(board.card_machine_a.pk)
    assert page["tasks"] == []
    assert page["population_count"] == 0
    assert page["returned_count"] == 0


# -- authorization is unchanged and independent of the scope fence ----------


def test_authorization_denials_stay_closed_with_and_without_scope(read_flags, board):
    """Foreign, unresolved, missing, deleted and ungranted actors stay out."""
    _grant(board)
    # Foreign customer: unassigned but NOT this actor's customer relationship.
    assert _call_detail(board.actor_principal, board.card_customer_y.pk) == _not_found(
        board.card_customer_y.pk
    )
    assert _call_stock(board.actor_principal, board.card_customer_y.pk) == _not_found(
        board.card_customer_y.pk
    )
    with _turn_scope(board.machine_a.pk):
        assert _call_detail(board.actor_principal, board.card_customer_y.pk) == _not_found(
            board.card_customer_y.pk
        )
    # Null-machine row WITHOUT a customer: scope unresolved, already denied.
    assert _call_detail(board.actor_principal, board.card_bare.pk) == _not_found(board.card_bare.pk)
    assert _call_stock(board.actor_principal, board.card_bare.pk) == _not_found(board.card_bare.pk)
    # No grant at all: the same record stays closed even without a scope.
    assert _call_detail(board.denied_principal, board.card_unassigned.pk) == _not_found(
        board.card_unassigned.pk
    )
    # Missing (deleted) actor and no principal fail closed.
    deleted = AIPrincipal(
        subject="user:4242424242",
        actor="user:4242424242",
        user_pk="4242424242",
        username="rv04-deleted",
        authentication_method="session",
        scope="chat",
        policy_version="test",
        is_staff=False,
        is_superuser=False,
    )
    assert _call_detail(deleted, board.card_unassigned.pk) == _not_found(board.card_unassigned.pk)
    assert _call_detail(None, board.card_unassigned.pk) == _not_found(board.card_unassigned.pk)
    assert _call_stock(None, board.card_unassigned.pk) == _not_found(board.card_unassigned.pk)
    # Card and parent identities are never substituted for one another.
    assert _call_detail(board.actor_principal, board.wo_unassigned.pk) == _not_found(
        board.wo_unassigned.pk
    )
    assert board.card_unassigned.pk != board.wo_unassigned.pk
    assert board.card_machine_a.pk != board.wo_machine_a.pk


def test_stock_and_reads_persist_no_business_effects(read_flags, board):
    """The stock check stays compute-only and reads mutate nothing."""
    _grant(board)
    before = (
        WorkOrder.objects.count(),
        KanbanCard.objects.count(),
        get_user_model().objects.count(),
    )
    first = _call_stock(board.actor_principal, board.card_unassigned.pk)
    second = _call_stock(board.actor_principal, board.card_machine_a.pk)
    _call_detail(board.actor_principal, board.card_unassigned.pk)
    _call_list(board.actor_principal, search=board.token)
    after = (
        WorkOrder.objects.count(),
        KanbanCard.objects.count(),
        get_user_model().objects.count(),
    )
    assert after == before
    assert first["work_order_id"] == board.wo_unassigned.pk
    assert second["work_order_id"] == board.wo_machine_a.pk


# -- RV04 additions: independently reached detail/stock scoped denials, a
# -- genuinely deleted actor control, and a real NONEMPTY compute-only
# -- allocation proof --------------------------------------------------------


def test_explicit_machine_scope_excludes_unassigned_detail_alone(read_flags, board):
    """Detail alone: no-scope success first, then the scoped denial is reached."""
    _grant(board)
    plain = _call_detail(board.actor_principal, board.card_unassigned.pk)
    assert "error" not in plain
    assert plain["kanban_card_id"] == board.card_unassigned.pk
    assert plain["work_order_id"] == board.wo_unassigned.pk
    with _turn_scope(board.machine_a.pk):
        denied = _call_detail(board.actor_principal, board.card_unassigned.pk)
        bound = _call_detail(board.actor_principal, board.card_machine_a.pk)
        page = _call_list(board.actor_principal, search=board.token)
    assert denied == _not_found(board.card_unassigned.pk)
    assert "error" not in bound
    assert bound["kanban_card_id"] == board.card_machine_a.pk
    assert bound["work_order_id"] == board.wo_machine_a.pk
    assert [row["kanban_card_id"] for row in page["tasks"]] == [board.card_machine_a.pk]
    assert page["population_count"] == 1
    assert page["returned_count"] == 1


def test_explicit_machine_scope_excludes_unassigned_stock_alone(read_flags, board):
    """Stock alone: no-scope success first, then the scoped denial is reached."""
    _grant(board)
    plain = _call_stock(board.actor_principal, board.card_unassigned.pk)
    assert "error" not in plain
    assert plain["kanban_card_id"] == board.card_unassigned.pk
    assert plain["work_order_id"] == board.wo_unassigned.pk
    with _turn_scope(board.machine_a.pk):
        denied = _call_stock(board.actor_principal, board.card_unassigned.pk)
        bound = _call_stock(board.actor_principal, board.card_machine_a.pk)
        page = _call_list(board.actor_principal, search=board.token)
    assert denied == _not_found(board.card_unassigned.pk)
    assert "error" not in bound
    assert bound["kanban_card_id"] == board.card_machine_a.pk
    assert bound["work_order_id"] == board.wo_machine_a.pk
    assert [row["kanban_card_id"] for row in page["tasks"]] == [board.card_machine_a.pk]
    assert page["population_count"] == 1
    assert page["returned_count"] == 1


def test_authorized_then_deleted_actor_reads_then_native_denial(read_flags, board):
    """A real actor reads, is deleted, then its captured principal is denied."""
    _grant(board)
    actor = get_user_model().objects.create_user(
        username=f"rv04-temp-{board.suffix}",
        email=f"rv04-temp-{board.suffix}@example.com",
        password="pw",
    )
    principal = _principal(actor)
    _GRANTS[actor.get_username()] = {
        MaintenanceScope(customer_id=board.customer_x.pk, site_key=None, client_id=None),
        MaintenanceScope(customer_id=None, site_key=None, client_id=board.client_a.pk),
    }
    actor_pk = actor.pk
    try:
        detail = _call_detail(principal, board.card_unassigned.pk)
        stock = _call_stock(principal, board.card_machine_a.pk)
        page = _call_list(principal, search=board.token)
        assert "error" not in detail
        assert detail["kanban_card_id"] == board.card_unassigned.pk
        assert "error" not in stock
        assert stock["kanban_card_id"] == board.card_machine_a.pk
        assert {row["kanban_card_id"] for row in page["tasks"]} == {
            board.card_unassigned.pk,
            board.card_machine_a.pk,
        }
        get_user_model().objects.filter(pk=actor_pk).delete()
        assert get_user_model().objects.filter(pk=actor_pk).first() is None
        assert WorkOrder.objects.filter(pk=board.wo_unassigned.pk).exists()
        assert KanbanCard.objects.filter(pk=board.card_unassigned.pk).exists()
        assert _call_detail(principal, board.card_unassigned.pk) == _not_found(
            board.card_unassigned.pk
        )
        assert _call_stock(principal, board.card_unassigned.pk) == _not_found(
            board.card_unassigned.pk
        )
        gone = _call_list(principal, search=board.token)
        assert gone["tasks"] == []
        assert gone["population_count"] == 0
        assert gone["returned_count"] == 0
    finally:
        get_user_model().objects.filter(pk=actor_pk).delete()
        _GRANTS.pop(actor.get_username(), None)


def _exact(value) -> Decimal:
    """Exact decimal view of a tool-reported numeric value (no tolerance)."""
    return Decimal(str(value))


def _wop_snapshot(wop) -> dict:
    """Exact persisted WorkOrderPart fields, refreshed from the ORM."""
    wop.refresh_from_db()
    return dict(WorkOrderPart.objects.filter(pk=wop.pk).values().get())


@pytest.fixture()
def alloc_rows(board):
    """Real Part/StockItem/WorkOrderPart rows with NONEMPTY seeded allocation.

    Distinguishable prior allocation values make an accidental UPDATE
    detectable; cleanup deletes ONLY the rows this fixture created.
    """
    suffix = board.suffix
    owned = {"parts": [], "stock": [], "wos": [], "cards": [], "wops": []}

    def _cleanup():
        KanbanCard.objects.filter(pk__in=owned["cards"]).delete()
        WorkOrderPart.objects.filter(pk__in=owned["wops"]).delete()
        WorkOrder.objects.filter(pk__in=owned["wos"]).delete()
        StockItem.objects.filter(pk__in=owned["stock"]).delete()
        Part.objects.filter(pk__in=owned["parts"]).delete()

    # Owned-row cleanup is armed BEFORE the first ORM constructor can fail.
    try:
        part_full = Part.objects.create(
            name=f"RV04 alloc full part {suffix}",
            description=f"RV04 allocation component {suffix}",
            component=True,
        )
        owned["parts"].append(part_full.pk)
        part_partial = Part.objects.create(
            name=f"RV04 alloc partial part {suffix}",
            description=f"RV04 partial component {suffix}",
            component=True,
        )
        owned["parts"].append(part_partial.pk)
        part_unrelated = Part.objects.create(
            name=f"RV04 unrelated part {suffix}",
            description=f"RV04 unrelated component {suffix}",
            component=True,
        )
        owned["parts"].append(part_unrelated.pk)
        stock_full = StockItem.objects.create(
            part=part_full,
            quantity=Decimal("10"),
            batch=f"rv04-alloc-{suffix}-full",
        )
        owned["stock"].append(stock_full.pk)
        stock_partial = StockItem.objects.create(
            part=part_partial,
            quantity=Decimal("2"),
            batch=f"rv04-alloc-{suffix}-partial",
        )
        owned["stock"].append(stock_partial.pk)
        stock_unrelated = StockItem.objects.create(
            part=part_unrelated,
            quantity=Decimal("3"),
            batch=f"rv04-alloc-{suffix}-unrelated",
        )
        owned["stock"].append(stock_unrelated.pk)
        wo_alloc = WorkOrder.objects.create(
            title=f"RV04 allocation job {suffix}",
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=board.machine_a,
        )
        owned["wos"].append(wo_alloc.pk)
        wo_other = WorkOrder.objects.create(
            title=f"RV04 other allocation job {suffix}",
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=board.machine_a,
        )
        owned["wos"].append(wo_other.pk)
        # Manual child PKs reserved AFTER WorkOrder.save's auto tracking cards.
        last_wo = WorkOrder.objects.order_by("-pk").values_list("pk", flat=True).first()
        last_card = KanbanCard.objects.order_by("-pk").values_list("pk", flat=True).first()
        base = max(last_wo or 0, last_card or 0) + 1000
        card_alloc = KanbanCard.objects.create(
            pk=base + 1,
            work_order=wo_alloc,
            title=f"rv04alloc{suffix} card allocation",
            status="backlog",
        )
        owned["cards"].append(card_alloc.pk)
        card_other = KanbanCard.objects.create(
            pk=base + 2,
            work_order=wo_other,
            title=f"rv04alloc{suffix} card other",
            status="backlog",
        )
        owned["cards"].append(card_other.pk)
        wop_full = WorkOrderPart.objects.create(
            work_order=wo_alloc,
            part=part_full,
            quantity=Decimal("5"),
            allocated_quantity=Decimal("1.25"),
            allocation_status=WorkOrderPart.ALLOCATION_PARTIAL,
            allocation_note="seeded prior allocation value",
        )
        owned["wops"].append(wop_full.pk)
        wop_partial = WorkOrderPart.objects.create(
            work_order=wo_alloc,
            part=part_partial,
            quantity=Decimal("4"),
            allocated_quantity=Decimal("0.5"),
            allocation_status=WorkOrderPart.ALLOCATION_NONE,
            allocation_note="seeded second prior allocation",
        )
        owned["wops"].append(wop_partial.pk)
        wop_other = WorkOrderPart.objects.create(
            work_order=wo_other,
            part=part_unrelated,
            quantity=Decimal("2"),
            allocated_quantity=Decimal("0.75"),
            allocation_status=WorkOrderPart.ALLOCATION_NONE,
            allocation_note="seeded unrelated prior allocation",
        )
        owned["wops"].append(wop_other.pk)
        yield SimpleNamespace(
            part_full=part_full,
            part_partial=part_partial,
            part_unrelated=part_unrelated,
            stock_full=stock_full,
            stock_partial=stock_partial,
            stock_unrelated=stock_unrelated,
            wo_alloc=wo_alloc,
            wo_other=wo_other,
            card_alloc=card_alloc,
            card_other=card_other,
            wop_full=wop_full,
            wop_partial=wop_partial,
            wop_other=wop_other,
        )
    finally:
        _cleanup()


def test_nonempty_stock_allocation_compute_only_exact_persisted_fields(
    read_flags, board, alloc_rows
):
    """Real NONEMPTY allocation computed by the tool; nothing is persisted."""
    _grant(board)
    before_full = _wop_snapshot(alloc_rows.wop_full)
    before_partial = _wop_snapshot(alloc_rows.wop_partial)
    before_other = _wop_snapshot(alloc_rows.wop_other)
    assert before_full["allocated_quantity"] == Decimal("1.25")
    assert before_full["allocation_status"] == WorkOrderPart.ALLOCATION_PARTIAL
    assert before_full["allocation_note"] == "seeded prior allocation value"
    assert before_partial["allocated_quantity"] == Decimal("0.5")
    assert before_partial["allocation_status"] == WorkOrderPart.ALLOCATION_NONE
    assert before_partial["allocation_note"] == "seeded second prior allocation"
    assert before_other["allocated_quantity"] == Decimal("0.75")
    assert before_other["allocation_status"] == WorkOrderPart.ALLOCATION_NONE
    assert before_other["allocation_note"] == "seeded unrelated prior allocation"
    assert alloc_rows.wop_full.pk in before_full.values()
    stock_pks = [
        alloc_rows.stock_full.pk,
        alloc_rows.stock_partial.pk,
        alloc_rows.stock_unrelated.pk,
    ]
    stock_before = {}
    for pk in stock_pks:
        stock_before[pk] = StockItem.objects.filter(pk=pk).values().get()
    assert (
        StockItem.objects
        .filter(pk=alloc_rows.stock_full.pk)
        .filter(StockItem.IN_STOCK_FILTER)
        .exists()
    )
    assert (
        StockItem.objects
        .filter(pk=alloc_rows.stock_partial.pk)
        .filter(StockItem.IN_STOCK_FILTER)
        .exists()
    )
    # Mirror check_and_allocate's ORM-read stock quantities (DB decimal
    # rendering) so note strings compare exact persisted representations.
    alloc_rows.stock_full.refresh_from_db()
    alloc_rows.stock_partial.refresh_from_db()
    avail_full = alloc_rows.stock_full.unallocated_quantity()
    avail_partial = alloc_rows.stock_partial.unallocated_quantity()
    stock = _call_stock(board.actor_principal, alloc_rows.card_alloc.pk)
    assert "error" not in stock
    assert stock["kanban_card_id"] == alloc_rows.card_alloc.pk
    assert stock["work_order_id"] == alloc_rows.wo_alloc.pk
    by_part = {row["part_id"]: row for row in stock["parts"]}
    assert set(by_part) == {alloc_rows.part_full.pk, alloc_rows.part_partial.pk}
    full = by_part[alloc_rows.part_full.pk]
    assert full["part_name"] == alloc_rows.part_full.name
    assert _exact(full["quantity_needed"]) == Decimal("5")
    assert _exact(full["quantity_available"]) == Decimal("10")
    assert _exact(full["quantity_allocated"]) == Decimal("5")
    assert full["allocation_status"] == WorkOrderPart.ALLOCATION_FULL
    assert full["note"] == (f"Fully allocated: {alloc_rows.wop_full.quantity} available in stock")
    partial = by_part[alloc_rows.part_partial.pk]
    assert partial["part_name"] == alloc_rows.part_partial.name
    assert _exact(partial["quantity_needed"]) == Decimal("4")
    assert _exact(partial["quantity_available"]) == Decimal("2")
    assert _exact(partial["quantity_allocated"]) == Decimal("2")
    assert _exact(full["quantity_allocated"]) != before_full["allocated_quantity"]
    assert _exact(partial["quantity_allocated"]) != before_partial["allocated_quantity"]
    assert partial["allocation_status"] == WorkOrderPart.ALLOCATION_PARTIAL
    assert partial["note"] == (
        f"Partially allocated: {avail_partial} of "
        f"{alloc_rows.wop_partial.quantity} available. "
        f"Short by {alloc_rows.wop_partial.quantity - avail_partial}"
    )
    assert stock["all_allocated"] is False
    assert stock["warnings"] == [
        f"⚠ Part '{alloc_rows.part_partial.name}' "
        f"(ID {alloc_rows.part_partial.pk}): "
        f"only {float(avail_partial)} of "
        f"{float(alloc_rows.wop_partial.quantity)} available"
    ]
    assert avail_full >= alloc_rows.wop_full.quantity
    # Exact persisted fields (allocation fields, note, timestamps) unchanged.
    assert _wop_snapshot(alloc_rows.wop_full) == before_full
    assert _wop_snapshot(alloc_rows.wop_partial) == before_partial
    assert _wop_snapshot(alloc_rows.wop_other) == before_other
    # Stock/unrelated rows unchanged as exact persisted fields.
    stock_after = {}
    for pk in stock_pks:
        stock_after[pk] = StockItem.objects.filter(pk=pk).values().get()
    assert stock_after == stock_before
    assert WorkOrder.objects.filter(pk=board.wo_unassigned.pk).exists()
