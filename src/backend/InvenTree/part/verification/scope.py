"""Fail-closed scope resolution for part verification.

Mirrors the maintenance scope pattern in ``tasks/scope.py``: actor scope comes
from an explicit deployment resolver (``AIMMS_RPF_SCOPE_RESOLVER``) or an
explicit actor attribute; nothing is ever inferred from free-text location, and
an unresolved or contradictory scope always fails before any object lookup.

A scope with ``customer_id=None`` means the explicit shared/global catalog
scope. It is never a wildcard: an actor holding only the global scope cannot
act on a customer-owned context, and vice versa.
"""

from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string

from part.verification.schema import HashDomains, hash_canonical


class VerificationScopeError(Exception):
    """Raised when verification scope cannot be resolved unambiguously."""


@dataclass(frozen=True)
class VerificationScope:
    """One resolved verification scope (customer and optional site)."""

    customer_id: int | None
    site_key: str | None = None


def scope_fingerprint(scope: VerificationScope) -> str:
    """Return the canonical fingerprint for a resolved scope."""
    return hash_canonical(
        HashDomains.SCOPE,
        {'customer_id': scope.customer_id, 'site_key': scope.site_key},
    )


def _coerce_scope(value) -> VerificationScope:
    """Coerce a resolver-returned value into a VerificationScope."""
    if isinstance(value, VerificationScope):
        return value
    if isinstance(value, dict):
        return VerificationScope(
            customer_id=value.get('customer_id'), site_key=value.get('site_key')
        )
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return VerificationScope(customer_id=value[0], site_key=value[1])
    # Accept maintenance scopes (same shape) without importing the tasks app
    customer_id = getattr(value, 'customer_id', None)
    site_key = getattr(value, 'site_key', None)
    if customer_id is not None or site_key is not None:
        return VerificationScope(customer_id=customer_id, site_key=site_key)
    raise VerificationScopeError(
        'Invalid verification scope returned by actor resolver'
    )


def scope_for_actor(actor) -> set[VerificationScope]:
    """Resolve the set of verification scopes granted to an actor.

    Fails closed: an unauthenticated actor, a missing resolver result, or an
    empty scope set raises ``VerificationScopeError``.
    """
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise VerificationScopeError('Verification actor is not authenticated')

    resolver = getattr(settings, 'AIMMS_RPF_SCOPE_RESOLVER', None) or getattr(
        settings, 'AIMMS_MAINTENANCE_SCOPE_RESOLVER', None
    )
    if isinstance(resolver, str):
        resolver = import_string(resolver)

    if resolver is not None:
        raw = resolver(actor)
    else:
        # Explicit actor attributes remain the test/service-account seam;
        # the maintenance scope attribute is accepted so one deployment
        # configuration governs both domains.
        raw = getattr(actor, 'verification_scopes', None) or getattr(
            actor, 'maintenance_scopes', None
        )

    if not raw:
        raise VerificationScopeError('Verification actor scope is unresolved')

    return {_coerce_scope(item) for item in raw}


def scope_for_context(
    *, machine=None, work_order=None, bom_item=None, requested_part=None
) -> VerificationScope:
    """Resolve the target scope for a verification context.

    Customer-owned context (a machine or work order) yields that customer's
    scope; disagreement between owners fails closed. Pure catalog context
    (BOM line, requested part, manual) yields the explicit global scope.

    The free-text ``AssetMachine.location`` is never promoted into a site
    boundary (spec section 17.3).
    """
    customer_ids = set()

    if machine is not None:
        customer_ids.add(machine.customer_id)

    if work_order is not None:
        wo_customer = getattr(work_order, 'customer_id', None)
        wo_machine = getattr(work_order, 'machine', None)
        if wo_customer is not None:
            customer_ids.add(wo_customer)
        if wo_machine is not None:
            customer_ids.add(wo_machine.customer_id)

    customer_ids.discard(None)

    if len(customer_ids) > 1:
        raise VerificationScopeError(
            'Verification context owners disagree about customer scope'
        )

    customer_id = next(iter(customer_ids)) if customer_ids else None
    return VerificationScope(customer_id=customer_id, site_key=None)


def require_scope(actor, target: VerificationScope) -> VerificationScope:
    """Require exact membership of the target scope in the actor's scopes."""
    if target not in scope_for_actor(actor):
        raise VerificationScopeError(
            'Actor and verification target scopes do not match'
        )
    return target
