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
    r'^(?:c(?P<customer>[1-9]\d{0,9})|k(?P<client>[1-9]\d{0,9}))'
    r'(?:~(?P<site>[A-Za-z0-9][A-Za-z0-9_.-]{0,63}))?$'
)

_SITE_KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


class RiskScopeError(Exception):
    """Raised when a scope cannot be resolved, decoded, or authorized."""

    code = SCOPE_UNRESOLVED


def encode_scope(scope: MaintenanceScope) -> str:
    """Encode a structured scope into the canonical persisted scope key.

    ``RiskScopeCodec`` is the single reversible encoding between structured
    ``MaintenanceScope`` values and persisted/API ``scope_key`` strings;
    arbitrary caller strings are never trusted. Customer scopes encode as
    ``c<id>``, client scopes as ``k<id>``; historical c-keys stay decodable
    so persisted findings never need a data migration.
    """
    customer_id = scope.customer_id
    client_id = scope.client_id
    if isinstance(customer_id, int) and customer_id > 0:
        key = f'c{customer_id}'
    elif isinstance(client_id, int) and client_id > 0:
        key = f'k{client_id}'
    else:
        raise RiskScopeError('Scope identity is unresolved')
    if scope.site_key is None:
        return key
    if not _SITE_KEY_RE.match(str(scope.site_key)):
        raise RiskScopeError('Scope site key is not encodable')
    return f'{key}~{scope.site_key}'


def decode_scope_key(scope_key: str) -> MaintenanceScope:
    """Decode a canonical scope key back into a structured scope."""
    match = _SCOPE_KEY_RE.match(str(scope_key or ''))
    if not match:
        raise RiskScopeError('Scope key is not decodable')
    customer = match.group('customer')
    client = match.group('client')
    return MaintenanceScope(
        customer_id=int(customer) if customer else None,
        site_key=match.group('site'),
        client_id=int(client) if client else None,
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


def _require_site_unscoped(
    scope: MaintenanceScope, source_kind: str
) -> MaintenanceScope:
    """Reject site-scoped requests no source can prove today.

    No authoritative source model carries a site key yet; evaluating a
    site-scoped request against scope-wide rows would over-disclose, so
    the adapter aborts instead.
    """
    if scope.site_key is not None:
        raise RiskScopeError(
            f'Source {source_kind!r} cannot prove site-level scope membership'
        )
    if scope.customer_id is None and scope.client_id is None:
        raise RiskScopeError('Scope identity is unresolved')
    return scope


def _wo_claims_any(prefix: str) -> Q:
    """Work orders that provably belong to *some* scope, per the WO rule.

    A work order is owned by its explicit customer when it names one, else by
    its machine's client. Everything else is unowned and never radar input.
    """
    return Q(**{f'{prefix}customer__isnull': False}) | Q(**{
        f'{prefix}machine__client__isnull': False
    })


def _wo_claims_scope(prefix: str, scope: MaintenanceScope) -> Q:
    """Work orders provably owned by the one requested scope."""
    if scope.customer_id is not None:
        return Q(**{f'{prefix}customer_id': scope.customer_id})
    return Q(**{
        f'{prefix}customer__isnull': True,
        f'{prefix}machine__client_id': scope.client_id,
    })


def _wo_claims_foreign(prefix: str, scope: MaintenanceScope) -> Q:
    """Work orders provably owned by a *different* scope (anchored per row)."""
    return _wo_claims_any(prefix) & ~_wo_claims_scope(prefix, scope)


class RiskSourceScopeAdapter:
    """Base class mapping one source's ownership fields onto scopes."""

    source_kind = ''

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return only rows proven to belong to the one requested scope."""
        raise NotImplementedError


class WorkOrderScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``tasks.WorkOrder`` (the AIMMS work order).

    Mirrors the live fail-closed seam: an explicit work-order customer is the
    order's whole boundary, otherwise the asset's client owns it -- exactly
    the rule the live work-order API applies.
    """

    source_kind = 'work_order'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return work orders provably owned by the requested scope."""
        from tasks.models import WorkOrder

        scope = _require_site_unscoped(scope, self.source_kind)
        return WorkOrder.objects.filter(_wo_claims_scope('', scope))


class RepairPacketScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``repair.RepairPacket``.

    A packet's provable owner follows the work-order rule: an explicit
    customer on its linked work order wins, else the client of its machine
    (or its work order's machine). Packets claiming any other scope on one
    of those paths are excluded (never leaked, never counted).
    """

    source_kind = 'repair_packet'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return packets provably owned by the requested scope."""
        from repair.models import RepairPacket

        scope = _require_site_unscoped(scope, self.source_kind)
        if scope.customer_id is not None:
            claims_scope = Q(work_order__customer_id=scope.customer_id)
        else:
            claims_scope = Q(work_order__customer__isnull=True) & (
                Q(machine__client_id=scope.client_id)
                | Q(work_order__machine__client_id=scope.client_id)
            )
        claims_any = (
            Q(work_order__customer__isnull=False)
            | Q(machine__client__isnull=False)
            | Q(work_order__machine__client__isnull=False)
        )
        return RepairPacket.objects.filter(claims_scope).exclude(
            claims_any & ~claims_scope
        )


def _conflicting_shortages(scope: MaintenanceScope) -> QuerySet:
    """Shortage rows whose work order provably belongs to another scope.

    Used as an anchored NOT-EXISTS exclusion: each row in this queryset is
    one shortage that claims a foreign scope, so excluding parents via
    ``rel__in`` evaluates the conflict per related row instead of across
    the whole join (the classic ``exclude(Q & ~Q)`` multi-valued pitfall).
    """
    from tasks.jobkit_models import JobKitShortage

    return JobKitShortage.objects.filter(
        _wo_claims_foreign('line__kit__work_order__', scope)
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

        scope = _require_site_unscoped(scope, self.source_kind)
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
        # escape exclusion even while also linked to another scope.
        if scope.customer_id is not None:
            link_claims_scope = Q(packet__work_order__customer_id=scope.customer_id)
        else:
            link_claims_scope = Q(packet__work_order__customer__isnull=True) & (
                Q(packet__machine__client_id=scope.client_id)
                | Q(packet__work_order__machine__client_id=scope.client_id)
            )
        link_claims_any = (
            Q(packet__work_order__customer__isnull=False)
            | Q(packet__machine__client__isnull=False)
            | Q(packet__work_order__machine__client__isnull=False)
        )
        conflicting_links = RepairPacketApprovalLink.objects.filter(
            link_claims_any & ~link_claims_scope
        )
        conflicting_shortages = _conflicting_shortages(scope)
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

        scope = _require_site_unscoped(scope, self.source_kind)
        work_orders = WorkOrderScopeAdapter().queryset_for_scope(
            actor=actor, scope=scope
        )
        # Anchored per shortage row (see ApprovalScopeAdapter): a line
        # feeding shortages of two scopes is conflicting and excluded
        # from both scopes rather than leaked into both.
        return (
            PurchaseOrderLineItem.objects
            .filter(jobkitshortage__line__kit__work_order__in=work_orders)
            .exclude(jobkitshortage__in=_conflicting_shortages(scope))
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
    *relevance* (the part is installed on one of the scope client's active
    machines), not exclusive ownership. Machines belong to clients only, so
    a customer scope truthfully sees no machine-installed parts. Stock
    counts remain native InvenTree data governed by part permissions.
    """

    source_kind = 'part_stock'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return active parts installed on the scope client's machines."""
        from part.models import Part

        scope = _require_site_unscoped(scope, self.source_kind)
        if scope.client_id is None:
            return Part.objects.none()
        return Part.objects.filter(
            active=True,
            machine_installations__machine__client_id=scope.client_id,
            machine_installations__machine__active=True,
        ).distinct()


class AssetMachineScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``assets.AssetMachine``.

    Machines carry only a client identity; a customer scope owns none, and
    that empty result is truthful rather than fail-silent.
    """

    source_kind = 'asset_machine'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return active machines owned by the scope client."""
        from assets.models import AssetMachine

        scope = _require_site_unscoped(scope, self.source_kind)
        if scope.client_id is None:
            return AssetMachine.objects.none()
        return AssetMachine.objects.filter(client_id=scope.client_id, active=True)


class MachineAnomalyScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over ``assets.MachineAnomaly`` via its machine.

    Anomalies inherit the machine's client identity; a customer scope owns
    no machines and therefore truthfully sees no anomalies.
    """

    source_kind = 'machine_anomaly'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return anomalies on active machines owned by the scope client."""
        from assets.health_models import MachineAnomaly

        scope = _require_site_unscoped(scope, self.source_kind)
        if scope.client_id is None:
            return MachineAnomaly.objects.none()
        return MachineAnomaly.objects.filter(
            machine__client_id=scope.client_id, machine__active=True
        )


class RiskFindingScopeAdapter(RiskSourceScopeAdapter):
    """Scope adapter over persisted ``repair.RiskFinding`` rows.

    Findings already carry their proven scope key, so membership is exact
    key equality — no join-based re-derivation that could drift from the
    scope proven at promotion time.
    """

    source_kind = 'risk_finding'

    def queryset_for_scope(self, *, actor, scope: MaintenanceScope) -> QuerySet:
        """Return findings persisted under exactly this scope key."""
        from repair.risk_models import RiskFinding

        return RiskFinding.objects.filter(scope_key=encode_scope(scope))


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
        MachineAnomalyScopeAdapter(),
        RiskFindingScopeAdapter(),
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
