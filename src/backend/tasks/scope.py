"""Fail-closed maintenance scope resolution.

A maintenance record is reachable through exactly one of two identities:

* **client** - the tenant of this software. Machines are internal plant
  assets and carry only this identity;
* **customer** - a sales relationship. It is a claim about a work order or a
  procedure, never about a machine.

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
    if not scopes or any(not scope.is_resolved for scope in scopes):
        raise ScopeError('Maintenance actor scope is unresolved')
    return scopes


def scope_for_work_order(work_order) -> MaintenanceScope:
    """Resolve a work order's boundary from its own customer or its asset.

    An explicit work-order customer wins: it is a sales claim about this job.
    Otherwise the asset's client owns it. A work order with neither remains
    unresolved and therefore unreachable.
    """
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
    raise ScopeError(
        'Work-order scope is unresolved: its asset has neither a customer nor a client.'
    )


def require_work_order_scope(actor, work_order) -> MaintenanceScope:
    """Require the work order's exact scope to be authorized for the actor."""
    scope = scope_for_work_order(work_order)
    if scope not in scope_for_actor(actor):
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


def work_order_scope_filter(actor) -> Q:
    """Return a queryset predicate selecting exactly the actor's work orders.

    The set form of :func:`require_work_order_scope`, and never wider than it:
    an explicit work-order customer is the order's whole boundary, otherwise
    the asset's client owns it. The same fail-closed rules as
    :func:`machine_scope_filter` apply -- ``pk__in=[]`` base so an actor whose
    scopes contribute nothing matches no rows, and site-qualified grants are
    skipped because a resolved work-order scope never carries a site key.

    Raises:
        ScopeError: When the actor's own scope cannot be resolved.
    """
    predicate = Q(pk__in=[])
    for scope in scope_for_actor(actor):
        if scope.site_key is not None:
            continue
        if scope.customer_id is not None:
            predicate |= Q(customer_id=scope.customer_id)
        elif scope.client_id is not None:
            predicate |= Q(customer__isnull=True, machine__client_id=scope.client_id)
    return predicate


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
