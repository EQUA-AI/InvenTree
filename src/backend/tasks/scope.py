"""Fail-closed maintenance scope resolution.

A maintenance record is reachable through exactly one of two identities:

* **customer** - a sales relationship, for machines installed at somebody we
  manufacture for or sell to;
* **client** - the tenant of this software, for internal plant assets nobody
  bought.

Internal assets used to have neither, which left them with no resolvable scope
at all: chat and the canonical API refused to touch them rather than guess. The
client identity is what closes that gap without weakening the boundary - a
machine with neither identity is still unreachable, by design.

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
    """Resolve and reconcile explicit and asset-derived work-order scope.

    A customer relationship wins when one exists, because that is the stronger
    claim: the machine is installed at somebody else's site. Otherwise the
    asset's client owns it. A work order whose asset has neither remains
    unresolved and therefore unreachable.
    """
    explicit_customer_id = getattr(work_order, 'customer_id', None)
    machine = getattr(work_order, 'machine', None)
    machine_customer_id = getattr(machine, 'customer_id', None) if machine else None
    machine_client_id = getattr(machine, 'client_id', None) if machine else None

    if (
        explicit_customer_id is not None
        and machine_customer_id is not None
        and explicit_customer_id != machine_customer_id
    ):
        raise ScopeError('Work-order customer does not match asset customer')

    customer_id = explicit_customer_id or machine_customer_id
    if customer_id is None and machine_client_id is not None:
        return MaintenanceScope(
            customer_id=None, site_key=None, client_id=machine_client_id
        )
    if customer_id is None:
        raise ScopeError(
            'Work-order scope is unresolved: its asset has neither a customer '
            'nor a client.'
        )

    # AssetMachine currently has no authoritative site key. Its free-text
    # ``location`` must not be promoted into a security boundary.
    return MaintenanceScope(customer_id=customer_id, site_key=None)


def require_work_order_scope(actor, work_order) -> MaintenanceScope:
    """Require the work order's exact scope to be authorized for the actor."""
    scope = scope_for_work_order(work_order)
    if scope not in scope_for_actor(actor):
        raise ScopeError('Actor and work-order maintenance scopes do not match')
    return scope


def scope_for_machine(machine) -> MaintenanceScope:
    """Resolve one asset's own boundary, independent of any work order.

    Same two identities and the same precedence as ``scope_for_work_order``: a
    customer relationship is the stronger claim because the machine sits at
    somebody else's site, and the client owns it otherwise. A machine with
    neither remains unresolved and therefore unreachable.

    This exists because a machine is now addressable on its own -- the AI read
    rails answer questions about an asset with no work order in sight -- and
    deriving asset authority from a work order would make an asset reachable
    only once somebody happened to raise a job against it.
    """
    if machine is None:
        raise ScopeError('Machine scope is unresolved: no machine supplied')

    customer_id = getattr(machine, 'customer_id', None)
    client_id = getattr(machine, 'client_id', None)

    if customer_id is not None:
        # AssetMachine has no authoritative site key. Its free-text ``location``
        # must not be promoted into a security boundary.
        return MaintenanceScope(customer_id=customer_id, site_key=None)
    if client_id is not None:
        return MaintenanceScope(customer_id=None, site_key=None, client_id=client_id)
    raise ScopeError(
        'Machine scope is unresolved: it has neither a customer nor a client.'
    )


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
        if scope.customer_id is not None:
            predicate |= Q(customer_id=scope.customer_id)
        elif scope.client_id is not None:
            # Customer wins in ``scope_for_machine``, so a client grant must not
            # reach a machine that also names a customer.
            predicate |= Q(customer_id__isnull=True, client_id=scope.client_id)
    return predicate
