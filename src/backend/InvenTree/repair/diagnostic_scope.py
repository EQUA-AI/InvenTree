"""Deployment-owned diagnostic capability resolvers.

The reasoning rail exposes diagnostic tools only for capabilities returned by
the resolver named in ``AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER``. Until this
module existed no production resolver did, so a deployment that enabled the
diagnosis feature ran the reasoning model with an empty tool set: it could
cite nothing, and its answers were structurally ungroundable. The resolver
lives here (not in ``tasks.scope``) because the capability vocabulary and the
diagnostic read facade are both owned by the repair app, and ``repair``
already depends on ``tasks`` — the reverse import would invert that.

Mirrors the shape of ``tasks.scope.single_site_scope_resolver``: inactive
guard, deferred imports, one role check, fail-closed empty return. The seam
in ``repair.services._diagnostic_capabilities_for_actor`` additionally
intersects whatever is returned with the fixed capability allowlist, so a
resolver can only narrow, never invent, a grant.
"""

from __future__ import annotations

from django.conf import settings

#: Read grants every authorized maintenance operator receives. Deliberately
#: excludes ``diagnostics.health.read`` (its own grant below — reading live
#: industrial telemetry is a different level of access from reading the
#: machine dossier, per repair.services) and ``diagnostics.safety_p0.read``
#: (doubly closed: the registry only carries the live-safety tool while the
#: safety P0s are closed, and this resolver never grants it — opening it is a
#: deliberate deployment decision, not a side effect).
BASE_DIAGNOSTIC_CAPABILITIES = frozenset({
    'diagnostics.machine.read',
    'diagnostics.packet.read',
    'diagnostics.maintenance.read',
    'diagnostics.manuals.read',
    'diagnostics.playbooks.read',
    'diagnostics.parts.read',
})


def single_site_diagnostic_capability_resolver(actor) -> frozenset[str]:
    """Grant diagnostic read capabilities to the site's maintenance operators.

    For a single-tenant deployment: every active user holding the work-order
    view role — the same role the sibling scope resolver keys on, so scope and
    capability cannot drift apart — may read the diagnostic record surfaces.
    ``diagnostics.health.read`` is added only while the deployment has machine
    AI reads enabled (``AIMMS_MACHINE_AI_READ_ENABLED``), the flag that
    already governs whether health projections are AI-readable at all.

    Configured via ``AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER =
    'repair.diagnostic_scope.single_site_diagnostic_capability_resolver'``.

    Fail-closed by construction: an empty return means the diagnostic context
    factory yields ``None``, the reasoning rail exposes zero tools, and the
    turn service refuses the reasoning route with an honest incomplete answer
    rather than running the model blind.
    """
    if actor is None or not getattr(actor, 'is_active', False):
        return frozenset()

    from users.permissions import check_user_role

    if not check_user_role(actor, 'work_order', 'view'):
        return frozenset()

    grants = set(BASE_DIAGNOSTIC_CAPABILITIES)
    if getattr(settings, 'AIMMS_MACHINE_AI_READ_ENABLED', False):
        grants.add('diagnostics.health.read')
    return frozenset(grants)
