"""Server-resolved, signed scoped-chat context (Feature #14, SC-ADR-001/002).

A scoped conversation is pinned to exactly one authorized record. The pin is
never browser prompt text: it is resolved here from a scope-filtered,
permission-checked lookup and carried as a short-lived Django-signed token
bound to the acting user, their session credential state, the object, and its
revision. The token narrows — it never grants — and every consumer must still
re-authorize the user and object on each call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.core import signing
from django.utils import timezone
from django.utils.crypto import constant_time_compare

SCOPED_CONTEXT_TOKEN_VERSION = 1
SCOPED_CONTEXT_TOKEN_SALT = 'aimms.aichat.scoped-context.v1'
SCOPED_CONTEXT_TOKEN_PURPOSE = 'aimms.scoped-chat'

#: Capability identifiers resolved per actor at context-resolution time.
CAPABILITY_QA = 'qa'
CAPABILITY_PROPOSE_HOLD = 'propose_hold'
CAPABILITY_PROPOSE_RESUME = 'propose_resume'

_TOKEN_CLAIMS = frozenset({
    'v',
    'purpose',
    'sub',
    'session_auth_hash',
    'context_type',
    'object_id',
    'scope_hash',
    'revision',
    'capabilities',
})


class ContextError(Exception):
    """Base class carrying a stable scoped-context error code."""

    code = 'CONTEXT_FORBIDDEN'


class ContextTypeUnknown(ContextError):  # noqa: N818
    """The context type is unknown or not enabled for this deployment."""

    code = 'CONTEXT_TYPE_UNKNOWN'


class ContextForbidden(ContextError):  # noqa: N818
    """Scope-safe denial: indistinguishable from a missing record."""

    code = 'CONTEXT_FORBIDDEN'


class ContextTokenInvalid(ContextError):  # noqa: N818
    """The context token failed signature, claim, or binding validation."""

    code = 'CONTEXT_TOKEN_INVALID'


class ContextTokenExpired(ContextError):  # noqa: N818
    """The context token aged out; the caller should re-resolve."""

    code = 'CONTEXT_TOKEN_EXPIRED'


@dataclass(frozen=True)
class ChatContext:
    """Immutable, server-resolved context descriptor for one record."""

    context_type: str
    object_id: str
    scope_key: str
    scope_hash: str
    display_label: str
    capabilities: tuple[str, ...]
    source_revision: str
    as_of: datetime
    snapshot: dict[str, Any]
    token: str


def scoped_chat_enabled() -> bool:
    """Whether the scoped-chat master switch is on (fail closed)."""
    return bool(getattr(settings, 'AIMMS_SCOPED_CHAT_ENABLED', False))


def enabled_context_types() -> tuple[str, ...]:
    """Return the deployment's enabled context types (fail closed)."""
    configured = getattr(settings, 'AIMMS_SCOPED_CHAT_CONTEXTS', None) or ()
    return tuple(str(item) for item in configured)


def proposals_enabled() -> bool:
    """Whether scoped conversations may draft action proposals."""
    return bool(getattr(settings, 'AIMMS_SCOPED_CHAT_PROPOSALS', False))


def token_ttl_seconds() -> int:
    """Return the context-token lifetime in seconds."""
    minutes = getattr(settings, 'AIMMS_SCOPED_CHAT_TOKEN_TTL_MIN', 15)
    return max(60, int(minutes) * 60)


def max_tool_calls_per_turn() -> int:
    """Return the per-turn tool budget."""
    return max(1, int(getattr(settings, 'AIMMS_SCOPED_CHAT_MAX_TOOL_CALLS', 4)))


def actor_scope_strings(user) -> tuple[str, str]:
    """Resolve the actor's maintenance scope fail-closed as (key, hash).

    Raises:
        ContextForbidden: When the actor scope cannot be resolved.
    """
    from tasks.scope import ScopeError, scope_for_actor

    try:
        scopes = scope_for_actor(user)
    except ScopeError as exc:
        raise ContextForbidden('context unavailable') from exc
    if not scopes:
        raise ContextForbidden('context unavailable')
    key = '|'.join(sorted(repr(scope) for scope in scopes))
    return key, hashlib.sha256(key.encode('utf-8')).hexdigest()


def source_revision_for(work_order) -> str:
    """Return a compact revision fingerprint for a work order."""
    basis = f'{work_order.lifecycle_version}:{work_order.updated_at.isoformat()}'
    return f'v{work_order.lifecycle_version}:{hashlib.sha256(basis.encode()).hexdigest()[:16]}'


def work_order_snapshot(work_order) -> dict[str, Any]:
    """Build the reviewed, allow-listed prompt snapshot for a work order.

    Hidden notes, attachment bodies, credentials, and cross-tenant
    relationships are prohibited prompt inputs; only these fields may appear.
    """
    return {
        'reference': work_order.reference or '',
        'title': work_order.title,
        'lifecycle_status': work_order.lifecycle_status,
        'work_order_type': work_order.work_order_type,
        'priority': work_order.priority,
        'lifecycle_version': work_order.lifecycle_version,
        'machine': str(work_order.machine) if work_order.machine_id else None,
        'assigned_to': (
            work_order.assigned_to.get_username() if work_order.assigned_to_id else None
        ),
        'due_date': work_order.due_date.isoformat() if work_order.due_date else None,
        'scheduled_start': (
            work_order.scheduled_start.isoformat()
            if work_order.scheduled_start
            else None
        ),
        'scheduled_end': (
            work_order.scheduled_end.isoformat() if work_order.scheduled_end else None
        ),
    }


def _authorized_work_order(user, object_id: str):
    """Load one work order scope-safely; denial never discloses existence."""
    from tasks.models import KanbanCard
    from tasks.scope import ScopeError, require_work_order_scope

    try:
        pk = int(object_id)
    except (TypeError, ValueError) as exc:
        raise ContextForbidden('context unavailable') from exc
    work_order = (
        KanbanCard.objects
        .filter(pk=pk)
        .select_related('machine', 'assigned_to')
        .first()
    )
    if work_order is None:
        raise ContextForbidden('context unavailable')
    try:
        require_work_order_scope(user, work_order)
    except ScopeError as exc:
        raise ContextForbidden('context unavailable') from exc
    return work_order


def _work_order_capabilities(user) -> tuple[str, ...]:
    """Resolve the actor's scoped-chat capability set for a work order."""
    capabilities = [CAPABILITY_QA]
    if proposals_enabled():
        from tasks.permissions import TRANSITION_WORKORDER

        if user.has_perm(TRANSITION_WORKORDER):
            capabilities.extend([CAPABILITY_PROPOSE_HOLD, CAPABILITY_PROPOSE_RESUME])
    return tuple(capabilities)


def mint_context_token(
    user,
    *,
    context_type: str,
    object_id: str,
    scope_hash: str,
    source_revision: str,
    capabilities: tuple[str, ...],
) -> str:
    """Sign the short-lived context token bound to user, session, and record."""
    claims = {
        'v': SCOPED_CONTEXT_TOKEN_VERSION,
        'purpose': SCOPED_CONTEXT_TOKEN_PURPOSE,
        'sub': str(user.pk),
        'session_auth_hash': user.get_session_auth_hash(),
        'context_type': context_type,
        'object_id': str(object_id),
        'scope_hash': scope_hash,
        'revision': source_revision,
        'capabilities': sorted(capabilities),
    }
    return signing.dumps(claims, salt=SCOPED_CONTEXT_TOKEN_SALT, compress=True)


def validate_context_token(
    user,
    token: str,
    *,
    expected_type: str | None = None,
    expected_object_id: str | None = None,
) -> dict[str, Any]:
    """Validate a context token for the acting user and return its claims.

    The token narrows, it never grants: callers must still independently
    authorize the user against the record on every use.

    Raises:
        ContextTokenExpired: When the signature aged out.
        ContextTokenInvalid: For any signature, claim, subject, session, or
            binding mismatch.
    """
    if not isinstance(token, str) or not token:
        raise ContextTokenInvalid('context token required')
    try:
        claims = signing.loads(
            token, salt=SCOPED_CONTEXT_TOKEN_SALT, max_age=token_ttl_seconds()
        )
    except signing.SignatureExpired as exc:
        raise ContextTokenExpired('context token expired') from exc
    except signing.BadSignature as exc:
        raise ContextTokenInvalid('context token invalid') from exc

    if not isinstance(claims, dict) or set(claims) != _TOKEN_CLAIMS:
        raise ContextTokenInvalid('context token invalid')
    if claims.get('v') != SCOPED_CONTEXT_TOKEN_VERSION:
        raise ContextTokenInvalid('context token invalid')
    if claims.get('purpose') != SCOPED_CONTEXT_TOKEN_PURPOSE:
        raise ContextTokenInvalid('context token invalid')
    if not getattr(user, 'is_authenticated', False):
        raise ContextTokenInvalid('context token invalid')
    if claims.get('sub') != str(user.pk):
        raise ContextTokenInvalid('context token invalid')
    session_hash = claims.get('session_auth_hash')
    if not isinstance(session_hash, str) or not constant_time_compare(
        session_hash, user.get_session_auth_hash()
    ):
        raise ContextTokenInvalid('context token invalid')
    if expected_type is not None and claims.get('context_type') != expected_type:
        raise ContextTokenInvalid('context token invalid')
    if expected_object_id is not None and claims.get('object_id') != str(
        expected_object_id
    ):
        raise ContextTokenInvalid('context token invalid')
    return claims


def resolve_context(user, *, context_type: str, object_id: str) -> ChatContext:
    """Resolve one record into a signed, capability-bearing chat context.

    Resolution is fail-closed: the feature flags, the actor scope, and the
    record's own authority must all agree before a token is minted.

    Raises:
        ContextTypeUnknown: When scoped chat or the type is not enabled.
        ContextForbidden: Scope-safe denial for missing/unauthorized records.
    """
    if not scoped_chat_enabled():
        raise ContextTypeUnknown('scoped chat is not enabled')
    if context_type not in enabled_context_types():
        raise ContextTypeUnknown('context type is not enabled')
    if context_type != 'work_order':
        # Asset and packet contexts remain gated on their scope hardening
        # prerequisites (guide §2.4 SC-ADR-008); nothing else is registered.
        raise ContextTypeUnknown('context type is not enabled')
    if not getattr(user, 'is_authenticated', False):
        raise ContextForbidden('context unavailable')
    if not getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False):
        raise ContextTypeUnknown('context type is not enabled')

    scope_key, scope_hash = actor_scope_strings(user)
    work_order = _authorized_work_order(user, object_id)
    capabilities = _work_order_capabilities(user)
    revision = source_revision_for(work_order)
    token = mint_context_token(
        user,
        context_type=context_type,
        object_id=str(work_order.pk),
        scope_hash=scope_hash,
        source_revision=revision,
        capabilities=capabilities,
    )
    label = work_order.reference or f'WO-{work_order.pk}'
    return ChatContext(
        context_type=context_type,
        object_id=str(work_order.pk),
        scope_key=scope_key,
        scope_hash=scope_hash,
        display_label=f'{label}: {work_order.title}',
        capabilities=capabilities,
        source_revision=revision,
        as_of=timezone.now(),
        snapshot=work_order_snapshot(work_order),
        token=token,
    )


def reauthorize_context(user, *, context_type: str, object_id: str):
    """Re-authorize the pinned record for the acting user right now.

    Returns the freshly loaded record. This is the per-call authorization
    primitive used by tools and render-time citation checks; it never uses
    cached authority.
    """
    if context_type != 'work_order':
        raise ContextTypeUnknown('context type is not enabled')
    if not getattr(user, 'is_authenticated', False):
        raise ContextForbidden('context unavailable')
    actor_scope_strings(user)
    return _authorized_work_order(user, object_id)
