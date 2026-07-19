"""Scope codec and per-source scope adapters for the Risk Radar.

Everything is scope-filtered before aggregation. One explicit authorized
scope is evaluated per scan/request; each source adapter must prove that a
row belongs to that scope before any rule queryset, persisted finding,
cache key, count, export, notification, or AI-summary input sees it.

An absent, ambiguous, or unsupported adapter aborts the rule rather than
returning an empty queryset (fail closed, never fail silent-zero).
"""

from __future__ import annotations

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from tasks.scope import MaintenanceScope, ScopeError, scope_for_actor

SCOPE_UNRESOLVED = 'SCOPE_UNRESOLVED'

_SCOPE_KEY_RE = re.compile(
    r'^c(?P<customer>[1-9]\d{0,9})(?:~(?P<site>[A-Za-z0-9][A-Za-z0-9_.-]{0,63}))?$'
)

_SITE_KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


class RiskScopeError(Exception):
    """Raised when a scope cannot be resolved, decoded, or authorized."""

    code = SCOPE_UNRESOLVED


def encode_scope(scope: MaintenanceScope) -> str:
    """Encode a structured scope into the canonical persisted scope key.

    ``RiskScopeCodec`` is the single reversible encoding between structured
    ``MaintenanceScope(customer_id, site_key)`` and persisted/API
    ``scope_key`` values; arbitrary caller strings are never trusted.
    """
    customer_id = scope.customer_id
    if not isinstance(customer_id, int) or customer_id <= 0:
        raise RiskScopeError('Scope customer id is unresolved')
    if scope.site_key is None:
        return f'c{customer_id}'
    if not _SITE_KEY_RE.match(str(scope.site_key)):
        raise RiskScopeError('Scope site key is not encodable')
    return f'c{customer_id}~{scope.site_key}'


def decode_scope_key(scope_key: str) -> MaintenanceScope:
    """Decode a canonical scope key back into a structured scope."""
    match = _SCOPE_KEY_RE.match(str(scope_key or ''))
    if not match:
        raise RiskScopeError('Scope key is not decodable')
    return MaintenanceScope(
        customer_id=int(match.group('customer')), site_key=match.group('site')
    )


def authorized_scopes(actor) -> set[MaintenanceScope]:
    """Return the actor's authorized scopes, translating scope errors."""
    try:
        return scope_for_actor(actor)
    except ScopeError as exc:
        raise RiskScopeError(str(exc)) from exc


def authorized_scope_keys(actor) -> list[str]:
    """Return the actor's authorized scope keys, sorted for determinism."""
    return sorted(encode_scope(scope) for scope in authorized_scopes(actor))


def require_scope(actor, scope_key: str) -> MaintenanceScope:
    """Decode one scope key and prove it is authorized for the actor."""
    scope = decode_scope_key(scope_key)
    if scope not in authorized_scopes(actor):
        raise RiskScopeError('Requested scope is not authorized for this actor')
    return scope


def risk_service_user():
    """Resolve the configured least-privilege scanner principal.

    Scans fail closed while ``AIMMS_RISK_SERVICE_USER_ID`` is unset or does
    not name an active user.
    """
    raw = getattr(settings, 'AIMMS_RISK_SERVICE_USER_ID', None)
    if raw in (None, ''):
        raise RiskScopeError('AIMMS_RISK_SERVICE_USER_ID is not configured')
    try:
        user_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise RiskScopeError('AIMMS_RISK_SERVICE_USER_ID is invalid') from exc
    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise RiskScopeError('Risk scanner principal does not exist or is inactive')
    return user


def _require_site_unscoped(scope: MaintenanceScope, source_kind: str) -> int:
    """Reject site-scoped requests no source can prove today.

    No authoritative source model carries a site key yet; evaluating a
    site-scoped request against customer-wide rows would over-disclose, so
    the adapter aborts instead.
    """
    if scope.site_key is not None:
        raise RiskScopeError(
            f'Source {source_kind!r} cannot prove site-level scope membership'
        )
    if scope.customer_id is None:
        raise RiskScopeError('Scope customer id is unresolved')
    return scope.customer_id


class RiskSourceScopeAdapter:
    """Base class mapping one source's ownership fields onto scopes."""

    source_kind = ''

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return only rows proven to belong to the one requested scope."""
        raise NotImplementedError


class WorkOrderScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``tasks.KanbanCard`` (the AIMMS work order).

    Mirrors the live fail-closed seam: a row is in scope when its explicit
    customer and its machine's customer agree on (or one of them names) the
    requested customer. Conflicting rows are excluded, exactly as the live
    work-order API excludes them.
    """

    source_kind = 'work_order'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return work orders provably owned by the scope customer."""
        from tasks.models import KanbanCard

        customer_id = _require_site_unscoped(scope, self.source_kind)
        return KanbanCard.objects.filter(
            Q(customer_id=customer_id, machine__isnull=True)
            | Q(customer_id=customer_id, machine__customer__isnull=True)
            | Q(customer_id=customer_id, machine__customer_id=customer_id)
            | Q(customer__isnull=True, machine__customer_id=customer_id)
        )


class RepairPacketScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``repair.RepairPacket``.

    A packet's provable owners are its machine's customer and its linked
    work order's provable customer. Packets claiming any other customer on
    one of those paths are excluded (never leaked, never counted).
    """

    source_kind = 'repair_packet'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return packets provably owned by the scope customer."""
        from repair.models import RepairPacket

        customer_id = _require_site_unscoped(scope, self.source_kind)
        claims_scope = (
            Q(machine__customer_id=customer_id)
            | Q(work_order__customer_id=customer_id)
            | Q(work_order__machine__customer_id=customer_id)
        )
        claims_other = (
            (Q(machine__customer__isnull=False) & ~Q(machine__customer_id=customer_id))
            | (
                Q(work_order__customer__isnull=False)
                & ~Q(work_order__customer_id=customer_id)
            )
            | (
                Q(work_order__machine__customer__isnull=False)
                & ~Q(work_order__machine__customer_id=customer_id)
            )
        )
        return RepairPacket.objects.filter(claims_scope).exclude(claims_other)


def _conflicting_shortages(customer_id: int) -> QuerySet:
    """Shortage rows whose work order provably belongs to another customer.

    Used as an anchored NOT-EXISTS exclusion: each row in this queryset is
    one shortage that claims a foreign customer, so excluding parents via
    ``rel__in`` evaluates the conflict per related row instead of across
    the whole join (the classic ``exclude(Q & ~Q)`` multi-valued pitfall).
    """
    from tasks.jobkit_models import JobKitShortage

    return JobKitShortage.objects.filter(
        (
            Q(line__kit__work_order__customer__isnull=False)
            & ~Q(line__kit__work_order__customer_id=customer_id)
        )
        | (
            Q(line__kit__work_order__machine__customer__isnull=False)
            & ~Q(line__kit__work_order__machine__customer_id=customer_id)
        )
    )


class ApprovalScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``approvals.Approval``.

    Approvals carry no ownership fields of their own; membership is proven
    only through links to already-scoped aggregates (repair packets via
    ``RepairPacketApprovalLink``, job-kit shortages via their work order).
    Approvals without any provable link are excluded from every scope —
    fail closed means they are never shown, not shown everywhere. Approvals
    additionally linked to another customer's aggregate are excluded as
    conflicting.
    """

    source_kind = 'approval'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return approvals provably linked to the scope customer."""
        from approvals.models import Approval
        from repair.models import RepairPacketApprovalLink

        customer_id = _require_site_unscoped(scope, self.source_kind)
        packets = RepairPacketScopeAdapter().queryset_for_scope(
            actor=actor, scope=scope
        )
        work_orders = WorkOrderScopeAdapter().queryset_for_scope(
            actor=actor, scope=scope
        )
        claims_scope = Q(repair_packet_links__packet__in=packets) | Q(
            jobkitshortage__line__kit__work_order__in=work_orders
        )
        # Conflict exclusion must be anchored per related row: a plain
        # .exclude(Q(...) & ~Q(...)) over these multi-valued joins is not
        # anchored to one link, so an approval with one in-scope link would
        # escape exclusion even while also linked to another customer.
        conflicting_links = RepairPacketApprovalLink.objects.filter(
            (
                Q(packet__machine__customer__isnull=False)
                & ~Q(packet__machine__customer_id=customer_id)
            )
            | (
                Q(packet__work_order__customer__isnull=False)
                & ~Q(packet__work_order__customer_id=customer_id)
            )
            | (
                Q(packet__work_order__machine__customer__isnull=False)
                & ~Q(packet__work_order__machine__customer_id=customer_id)
            )
        )
        conflicting_shortages = _conflicting_shortages(customer_id)
        return (
            Approval.objects
            .filter(claims_scope)
            .exclude(repair_packet_links__in=conflicting_links)
            .exclude(jobkitshortage__in=conflicting_shortages)
            .distinct()
        )


class PurchaseOrderLineScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``order.PurchaseOrderLineItem``.

    Purchase orders have no maintenance-customer ownership; a line is in
    scope only when a job-kit shortage of an in-scope work order links to
    it. Unlinked procurement is not provable and therefore not radar input.
    """

    source_kind = 'purchase_order_line'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return PO lines provably feeding the scope's job kits."""
        from order.models import PurchaseOrderLineItem

        customer_id = _require_site_unscoped(scope, self.source_kind)
        work_orders = WorkOrderScopeAdapter().queryset_for_scope(
            actor=actor, scope=scope
        )
        # Anchored per shortage row (see ApprovalScopeAdapter): a line
        # feeding shortages of two customers is conflicting and excluded
        # from both scopes rather than leaked into both.
        return (
            PurchaseOrderLineItem.objects
            .filter(jobkitshortage__line__kit__work_order__in=work_orders)
            .exclude(jobkitshortage__in=_conflicting_shortages(customer_id))
            .distinct()
        )


class JobKitShortageScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``tasks.JobKitShortage`` via its work order."""

    source_kind = 'job_kit_shortage'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return shortages whose kit's work order is in scope."""
        from tasks.jobkit_models import JobKitShortage

        _require_site_unscoped(scope, self.source_kind)
        work_orders = WorkOrderScopeAdapter().queryset_for_scope(
            actor=actor, scope=scope
        )
        return JobKitShortage.objects.filter(line__kit__work_order__in=work_orders)


class PartStockScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``part.Part`` for stock-threshold rules.

    Parts are deployment-global catalog rows; scope membership here means
    *relevance* (the part is installed on one of the scope customer's
    active machines), not exclusive ownership. Stock counts remain native
    InvenTree data governed by part permissions.
    """

    source_kind = 'part_stock'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return active parts installed on the scope customer's machines."""
        from part.models import Part

        customer_id = _require_site_unscoped(scope, self.source_kind)
        return Part.objects.filter(
            active=True,
            machine_installations__machine__customer_id=customer_id,
            machine_installations__machine__active=True,
        ).distinct()


class AssetMachineScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``assets.AssetMachine``."""

    source_kind = 'asset_machine'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return active machines owned by the scope customer."""
        from assets.models import AssetMachine

        customer_id = _require_site_unscoped(scope, self.source_kind)
        return AssetMachine.objects.filter(customer_id=customer_id, active=True)


SOURCE_ADAPTERS: dict[str, RiskSourceScopeAdapter] = {
    adapter.source_kind: adapter
    for adapter in (
        WorkOrderScopeAdapter(),
        RepairPacketScopeAdapter(),
        ApprovalScopeAdapter(),
        PurchaseOrderLineScopeAdapter(),
        JobKitShortageScopeAdapter(),
        PartStockScopeAdapter(),
        AssetMachineScopeAdapter(),
    )
}


def get_source_adapter(source_kind: str) -> RiskSourceScopeAdapter:
    """Return the registered adapter for a source, or abort fail-closed."""
    adapter = SOURCE_ADAPTERS.get(source_kind)
    if adapter is None:
        raise RiskScopeError(
            f'No scope adapter is registered for source {source_kind!r}'
        )
    return adapter
