"""Fail-closed maintenance scope resolution.

Machine and procedure boundaries use two distinct identities:

* **client** - the tenant of this software. Machines are internal plant
  assets and carry only this identity;
* **customer** - a sales relationship. It is a claim about a work order or a
  procedure, never about a machine.

Customer-attributed Work and Work with neither customer nor machine are shared
with every active Work viewer (O24). They grant no machine authority.

A machine without a client is unreachable, by design: an empty identity must
never read as "everyone's".

``site_key`` remains a deployment-defined subdivision. Free-text machine location
is never promoted into a boundary.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.utils.module_loading import import_string


class ScopeError(Exception):
    """Raised when maintenance scope cannot be resolved unambiguously."""


@dataclass(frozen=True)
class MaintenanceScope:
    """One authorized boundary: a customer or a client, optionally per site."""

    customer_id: int | None
    site_key: str | None
    client_id: int | None = None

    @property
    def is_resolved(self) -> bool:
        """Whether this scope names an identity at all.

        A scope with neither a customer nor a client authorizes nothing. Saying
        so explicitly is what keeps an empty resolver result from reading as
        "everything".
        """
        return self.customer_id is not None or self.client_id is not None


def _coerce_scope(value) -> MaintenanceScope:
    """Convert a resolver value into a ``MaintenanceScope``."""
    if isinstance(value, MaintenanceScope):
        return value
    if isinstance(value, dict):
        return MaintenanceScope(
            customer_id=value.get('customer_id'),
            site_key=value.get('site_key'),
            client_id=value.get('client_id'),
        )
    # Positional form stays customer-first for the deployments already using it.
    if isinstance(value, (tuple, list)) and len(value) in (2, 3):
        return MaintenanceScope(*value)
    raise ScopeError('Invalid maintenance scope returned by actor resolver')


def scope_for_actor(actor) -> set[MaintenanceScope]:
    """Return explicitly authorized scopes for an actor.

    Deployments can configure ``AIMMS_MAINTENANCE_SCOPE_RESOLVER`` as a callable
    or dotted path. The ``maintenance_scopes`` actor attribute is also supported
    for service accounts and tests. No implicit global scope is inferred.
    """
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise ScopeError('Maintenance actor is not authenticated')

    resolver = getattr(settings, 'AIMMS_MAINTENANCE_SCOPE_RESOLVER', None)
    if isinstance(resolver, str):
        resolver = import_string(resolver)

    values = (
        resolver(actor)
        if callable(resolver)
        else getattr(actor, 'maintenance_scopes', None)
    )
    if not values:
        raise ScopeError('Maintenance actor scope is unresolved')

    scopes = {_coerce_scope(value) for value in values}
    if any(
        scope.customer_id is not None and scope.client_id is not None
        for scope in scopes
    ):
        # A mixed value matches neither canonical record scope. Do not turn it
        # into customer authority in Work lists or client authority in search.
        raise ScopeError(
            'Maintenance actor scope has ambiguous customer/client identity'
        )
    if not scopes or any(not scope.is_resolved for scope in scopes):
        raise ScopeError('Maintenance actor scope is unresolved')
    return scopes


def scoped_procedures(actor):
    """The direct procedure reader's exact, currently resolved customer scope.

    Shared Work and machine-client grants confer no catalogue authority.
    Site-only and unresolved grants cannot authorize a whole procedure.
    """
    from .models import Procedure

    try:
        scopes = scope_for_actor(actor)
        customer_ids = {
            scope.customer_id
            for scope in scopes
            if scope.site_key is None and scope.customer_id is not None
        }
    except ScopeError:
        return Procedure.objects.none()
    client_ids = {
        scope.client_id
        for scope in scopes
        if scope.site_key is None and scope.client_id is not None
    }
    return Procedure.objects.filter(
        Q(customer_id__in=customer_ids, client__isnull=True)
        | Q(client_id__in=client_ids, customer__isnull=True)
    )


def scope_for_procedure(procedure) -> MaintenanceScope:
    """Require explicit customer OR client ownership, never machine-name inference."""
    if bool(procedure.customer_id) == bool(procedure.client_id):
        raise ScopeError('Procedure requires one explicit customer or client scope')
    return MaintenanceScope(
        customer_id=procedure.customer_id, client_id=procedure.client_id, site_key=None
    )


def scope_for_work_order(work_order) -> MaintenanceScope:
    """Return the Work's provenance identity, not machine access authority.

    Customer Work is shared under O24 regardless of this customer value.
    Machineless, customerless Work has an empty provenance identity, also
    shared. A machine-only job still requires its machine's resolved client.
    The empty identity never becomes a valid actor or machine scope.
    """
    if work_order is None:
        raise ScopeError('Work-order scope is unresolved: no work order supplied')
    explicit_customer_id = getattr(work_order, 'customer_id', None)
    machine = getattr(work_order, 'machine', None)
    machine_client_id = getattr(machine, 'client_id', None) if machine else None

    if explicit_customer_id is not None:
        # AssetMachine has no authoritative site key. Its free-text
        # ``location`` must not be promoted into a security boundary.
        return MaintenanceScope(customer_id=explicit_customer_id, site_key=None)
    if machine_client_id is not None:
        return MaintenanceScope(
            customer_id=None, site_key=None, client_id=machine_client_id
        )
    if machine is None:
        return MaintenanceScope(customer_id=None, site_key=None)
    raise ScopeError('Work-order scope is unresolved: its machine has no client.')


def _work_actor_scopes(actor) -> set[MaintenanceScope]:
    """Require active authentication and Work view; resolve optional client grants.

    Shared Work needs no client grant. An unresolved or ambiguous resolver
    grants no machine-only Work, while the actor's Work role still authorizes
    shared Work. A custom resolver cannot bypass the active/view checks.
    """
    from users.permissions import check_user_role

    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise ScopeError('Maintenance actor is not authenticated')
    if not getattr(actor, 'is_active', False) or not check_user_role(
        actor, 'work_order', 'view'
    ):
        raise ScopeError('Maintenance actor requires active Work view authority')
    try:
        return scope_for_actor(actor)
    except ScopeError:
        return set()


def require_work_order_scope(actor, work_order) -> MaintenanceScope:
    """Authorize shared Work or the exact client of machine-only Work."""
    scopes = _work_actor_scopes(actor)
    scope = scope_for_work_order(work_order)
    if scope.customer_id is None and scope.is_resolved and scope not in scopes:
        raise ScopeError('Actor and work-order maintenance scopes do not match')
    return scope


def scope_for_machine(machine) -> MaintenanceScope:
    """Resolve one asset's own boundary, independent of any work order.

    A machine belongs to its client, full stop. A machine without one remains
    unresolved and therefore unreachable.

    This exists because a machine is addressable on its own -- the AI read
    rails answer questions about an asset with no work order in sight -- and
    deriving asset authority from a work order would make an asset reachable
    only once somebody happened to raise a job against it.
    """
    if machine is None:
        raise ScopeError('Machine scope is unresolved: no machine supplied')

    client_id = getattr(machine, 'client_id', None)
    if client_id is not None:
        # AssetMachine has no authoritative site key. Its free-text ``location``
        # must not be promoted into a security boundary.
        return MaintenanceScope(customer_id=None, site_key=None, client_id=client_id)
    raise ScopeError('Machine scope is unresolved: it has no client.')


def require_machine_scope(actor, machine) -> MaintenanceScope:
    """Require the machine's exact scope to be authorized for the actor."""
    scope = scope_for_machine(machine)
    if scope not in scope_for_actor(actor):
        raise ScopeError('Actor and machine maintenance scopes do not match')
    return scope


def work_machine_authorized(actor, work_order) -> bool:
    """Whether the actor holds machine scope for this Work's own machine.

    O24 shares customer and unattributed Work, but a shared Work row is never
    machine authority. Machine-scoped records reached *through* a Work (its
    repair packet, source alert, maintenance record, telemetry, stock
    identities) are projected only when this returns ``True``. Machineless
    Work has no machine records, so it returns ``False``.
    """
    machine = getattr(work_order, 'machine', None) if work_order else None
    if machine is None:
        return False
    try:
        require_machine_scope(actor, machine)
    except ScopeError:
        return False
    return True


def enforce_machine_scope(actor, machine) -> None:
    """Explicit machine scope check; fails closed on every unresolved boundary.

    This is the per-record gate behind the machine-scoped health reads (see
    ``machine_health.api._machine``). It shares :func:`require_machine_scope`'s
    fail-closed semantics exactly:

    * a machine without a client is **denied** — an empty identity must never
      read as "everyone's" (see the module docstring);
    * an actor whose scope cannot be resolved is **denied** — role grants
      alone (``work_order.view`` etc.) are not client-scope authority;
    * a resolved actor scope that does not contain the machine's client is
      **denied**.

    Compatibility impact (deliberate, replaces the former "safe legacy"
    pass-through): deployments reading machine health with role grants only —
    no ``AIMMS_MAINTENANCE_SCOPE_RESOLVER`` configured and no per-actor
    ``maintenance_scopes`` — now receive 404 for those reads instead of
    unrestricted access. Configure a resolver (``single_site_scope_resolver``
    or ``granted_client_scope_resolver``) or explicit actor scopes, and give
    machines a client, to restore access under an authorized boundary.
    """
    require_machine_scope(actor, machine)


def scope_for_maintenance_record(record) -> MaintenanceScope:
    """Resolve one maintenance record's boundary: its machine's client.

    A record is plant history about an asset — its ``machine`` FK is non-null
    by schema — so it rides :func:`scope_for_machine` unchanged. A linked work
    order never widens or narrows the record itself: the order's customer is a
    sales claim about that job, and a denial there withholds enrichment only,
    which the enrichment site enforces.
    """
    if record is None:
        raise ScopeError('Maintenance-record scope is unresolved: no record supplied')
    return scope_for_machine(getattr(record, 'machine', None))


def require_maintenance_record_scope(actor, record) -> MaintenanceScope:
    """Require the record's exact scope to be authorized for the actor."""
    scope = scope_for_maintenance_record(record)
    if scope not in scope_for_actor(actor):
        raise ScopeError('Actor and maintenance-record scopes do not match')
    return scope


def machine_scope_filter(actor) -> Q:
    """Return a queryset predicate selecting exactly the actor's machines.

    This is the set form of :func:`require_machine_scope` and must never be
    wider than it, because listing surfaces disclose a machine's existence
    before anything re-authorizes it row by row. Two rules keep the two in
    agreement:

    * The base predicate is ``pk__in=[]`` -- selecting nothing -- so an actor
      whose scopes contribute no clause matches no rows. Starting from an empty
      ``Q()`` would instead read as "everything", which is the classic
      fail-open shape this module exists to avoid.
    * Scopes carrying a ``site_key`` are skipped. ``scope_for_machine`` always
      reports ``site_key=None``, and ``MaintenanceScope`` equality includes the
      site key, so a site-qualified grant authorizes no machine under
      ``require_machine_scope``. Letting it match here would surface machines
      that every per-record check then denies.

    Raises:
        ScopeError: When the actor's own scope cannot be resolved.
    """
    predicate = Q(pk__in=[])
    for scope in scope_for_actor(actor):
        if scope.site_key is not None:
            continue
        if scope.client_id is not None:
            predicate |= Q(client_id=scope.client_id)
    return predicate


def maintenance_record_scope_filter(actor) -> Q:
    """Return a queryset predicate selecting exactly the actor's records.

    The set form of :func:`require_maintenance_record_scope`, and never wider
    than it: a record belongs to its machine's client, full stop. The same
    fail-closed rules as :func:`machine_scope_filter` apply — ``pk__in=[]``
    base, site-qualified grants skipped — and customer-only scopes contribute
    nothing, because a customer is a claim about a work order, never about a
    machine or its history.

    Raises:
        ScopeError: When the actor's own scope cannot be resolved.
    """
    predicate = Q(pk__in=[])
    for scope in scope_for_actor(actor):
        if scope.site_key is not None:
            continue
        if scope.client_id is not None:
            predicate |= Q(machine__client_id=scope.client_id)
    return predicate


def work_order_scope_filter(actor) -> Q:
    """Select shared Work plus machine-only Work in the actor's client scope.

    Exact set form of ``require_work_order_scope``: customer Work (on any
    machine) and Work with neither customer nor machine are shared. A
    customerless job on an unresolved/foreign machine stays withheld.
    Site-qualified grants do not authorize machine-only Work.

    Raises:
        ScopeError: For unauthenticated, inactive or roleless actors.
    """
    scopes = _work_actor_scopes(actor)
    predicate = Q(customer__isnull=False) | Q(
        customer__isnull=True, machine__isnull=True
    )
    for scope in scopes:
        if scope.site_key is None and scope.client_id is not None:
            predicate |= Q(customer__isnull=True, machine__client_id=scope.client_id)
    return predicate


def scoped_work_orders(actor):
    """Return only shared or client-scoped Work authorized for this actor."""
    from tasks.models import WorkOrder

    try:
        return WorkOrder.objects.filter(work_order_scope_filter(actor))
    except ScopeError:
        return WorkOrder.objects.none()


def scoped_machines(actor):
    """Return only machines authorized by the canonical client boundary."""
    from assets.models import AssetMachine

    try:
        return AssetMachine.objects.filter(machine_scope_filter(actor))
    except ScopeError:
        return AssetMachine.objects.none()


def client_codes_for_actor(actor) -> frozenset[str]:
    """Return the client codes named by the actor's resolved scopes.

    The code-valued form of :func:`machine_scope_filter`, for boundaries that
    are stamped by client *code* rather than joined by client id -- the
    attachment-RAG search indexes carry ``client_codes`` so retrieval filters
    must be authored in that vocabulary. The same fail-closed rules apply:

    * Scopes carrying a ``site_key`` are skipped, exactly as in
      :func:`machine_scope_filter` -- a site-qualified grant authorizes no
      machine row, so it must not widen a search filter either.
    * Customer-only scopes contribute nothing: codes ride client grants.
    * An empty result raises rather than returning an empty set, because an
      empty filter clause must never be silently omitted downstream.

    Raises:
        ScopeError: When the actor's scope cannot be resolved, or when the
            resolved scopes name no active client.
    """
    client_ids = {
        scope.client_id
        for scope in scope_for_actor(actor)
        if scope.site_key is None and scope.client_id is not None
    }
    if not client_ids:
        raise ScopeError('Maintenance actor scope names no client')

    from assets.models import Client

    codes = frozenset(
        Client.objects.filter(pk__in=client_ids, active=True).values_list(
            'code', flat=True
        )
    )
    if not codes:
        raise ScopeError('Maintenance actor scope names no client')
    return codes


def single_site_scope_resolver(actor) -> set[MaintenanceScope]:
    """Grant the deployment's one internal tenant to authorized operators.

    For a single-tenant deployment: every active user holding the work-order
    view role is the tenant's staff, and their boundary is the tenant itself.
    Configured via ``AIMMS_MAINTENANCE_SCOPE_RESOLVER =
    'tasks.scope.single_site_scope_resolver'``; the tenant is named by
    ``AIMMS_SINGLE_SITE_CLIENT_CODE`` (default ``internal``).

    Fail-closed by construction: an empty return means ``scope_for_actor``
    raises, so a missing client row, an inactive user, or a user without the
    role authorizes nothing rather than everything.
    """
    if actor is None or not getattr(actor, 'is_active', False):
        return set()

    from assets.models import Client
    from users.permissions import check_user_role

    if not check_user_role(actor, 'work_order', 'view'):
        return set()

    code = getattr(settings, 'AIMMS_SINGLE_SITE_CLIENT_CODE', 'internal')
    client = Client.objects.filter(code=code, active=True).first()
    if client is None:
        return set()
    return {MaintenanceScope(customer_id=None, site_key=None, client_id=client.pk)}


def granted_client_scope_resolver(actor) -> set[MaintenanceScope]:
    """Explicit ``ClientScopeGrant`` rows win; no rows falls back single-site.

    The S6 control (spec: "the authorization relation … is the control, not
    names"): a user WITH grant rows is scoped to exactly those active
    clients — the dedicated solar-evaluation user holds grants for both
    ``internal`` and ``eval-fixtures``, so the golden set stays whole while
    every ungranted user simply never has ``eval-fixtures`` in a filter. A
    user with NO rows resolves through ``single_site_scope_resolver``
    unchanged, which keeps every existing operator (and the
    part-verification reuse of this setting) behavior-identical.

    Same role gate on both branches; same fail-closed construction.
    """
    if actor is None or not getattr(actor, 'is_active', False):
        return set()

    from assets.models import ClientScopeGrant
    from users.permissions import check_user_role

    if not check_user_role(actor, 'work_order', 'view'):
        return set()

    granted = set(
        ClientScopeGrant.objects.filter(user=actor, client__active=True).values_list(
            'client_id', flat=True
        )
    )
    if not granted:
        return single_site_scope_resolver(actor)
    return {
        MaintenanceScope(customer_id=None, site_key=None, client_id=client_id)
        for client_id in granted
    }
