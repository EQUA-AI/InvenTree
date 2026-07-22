"""Server-owned diagnostic context factory for the voice reasoning path (Phase 3b).

Resolves the actor's authorized record roots (machines + non-terminal repair
packets in the user's maintenance scope) and diagnostic capabilities from the
repair domain, then builds an immutable ``DiagnosticContext`` for the Tier-2
reasoning turn.

Fail-closed by construction: returns ``None`` when the actor cannot be
rehydrated, holds no diagnostic capabilities, or has no authorized records -- in
which case the reasoning path exposes no diagnostic tools. Client-supplied turn
content is never used to widen the record scope; all authority is server-resolved
from the authenticated principal. Live sensor/IoT readings are out of scope --
the technician supplies observations verbally to the agent, not via a tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from ai.core.auth import AIPrincipal


def _build_sync(principal: AIPrincipal) -> Any | None:
    """Synchronous resolution (Django ORM) of one turn's diagnostic authority."""
    user_pk = getattr(principal, "user_pk", None)
    if user_pk is None:
        return None

    from repair.services import (
        diagnostic_capabilities_for_actor,
        diagnostic_rehydrate_actor,
        list_diagnostic_record_roots,
    )

    actor = diagnostic_rehydrate_actor(user_pk)
    if actor is None:
        return None

    capabilities = tuple(diagnostic_capabilities_for_actor(actor))
    if not capabilities:
        return None

    roots_data = list_diagnostic_record_roots(actor)
    if not roots_data:
        return None

    from ai.core.tools.diagnostics import DiagnosticRecordRoot, build_diagnostic_context

    try:
        roots = tuple(
            DiagnosticRecordRoot(
                entity_type=item["entity_type"],
                entity_id=item["entity_id"],
                expected_revision=item["expected_revision"],
                linked_machine_id=item["linked_machine_id"],
                authorization_class=item["authorization_class"],
            )
            for item in roots_data
        )
        return build_diagnostic_context(
            principal,
            server_record_roots=roots,
            server_allowed_capabilities=capabilities,
        )
    except (ValueError, TypeError, KeyError):
        # A malformed root or a capability that fails the strict builder
        # invariants must never widen authority; fail closed to the legacy path.
        return None


async def build_voice_diagnostic_context(
    *,
    actor: AIPrincipal,
    trusted_context: Any,
    content: str,
    modality: str,
) -> Any | None:
    """Factory matching ``NormalizedTurnService.diagnostic_context_factory``.

    Authority is derived entirely from the authenticated principal server-side;
    ``trusted_context``, ``content`` and ``modality`` are intentionally not used
    to resolve record scope.
    """
    return await sync_to_async(_build_sync, thread_sensitive=True)(actor)
