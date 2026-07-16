"""Fail-closed maintenance customer and site scope resolution."""

from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string


class ScopeError(Exception):
    """Raised when maintenance scope cannot be resolved unambiguously."""


@dataclass(frozen=True)
class MaintenanceScope:
    """Customer and deployment-defined site boundary for maintenance data."""

    customer_id: int | None
    site_key: str | None


def _coerce_scope(value) -> MaintenanceScope:
    """Convert a resolver value into a ``MaintenanceScope``."""
    if isinstance(value, MaintenanceScope):
        return value
    if isinstance(value, dict):
        return MaintenanceScope(
            customer_id=value.get('customer_id'), site_key=value.get('site_key')
        )
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return MaintenanceScope(customer_id=value[0], site_key=value[1])
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
    if not scopes or any(scope.customer_id is None for scope in scopes):
        raise ScopeError('Maintenance actor scope is unresolved')
    return scopes


def scope_for_work_order(work_order) -> MaintenanceScope:
    """Resolve and reconcile explicit and asset-derived work-order scope."""
    explicit_customer_id = getattr(work_order, 'customer_id', None)
    machine = getattr(work_order, 'machine', None)
    machine_customer_id = getattr(machine, 'customer_id', None) if machine else None

    if (
        explicit_customer_id is not None
        and machine_customer_id is not None
        and explicit_customer_id != machine_customer_id
    ):
        raise ScopeError('Work-order customer does not match asset customer')

    customer_id = explicit_customer_id or machine_customer_id
    if customer_id is None:
        raise ScopeError('Work-order customer scope is unresolved')

    # AssetMachine currently has no authoritative site key. Its free-text
    # ``location`` must not be promoted into a security boundary.
    return MaintenanceScope(customer_id=customer_id, site_key=None)


def require_work_order_scope(actor, work_order) -> MaintenanceScope:
    """Require the work order's exact scope to be authorized for the actor."""
    scope = scope_for_work_order(work_order)
    if scope not in scope_for_actor(actor):
        raise ScopeError('Actor and work-order maintenance scopes do not match')
    return scope
