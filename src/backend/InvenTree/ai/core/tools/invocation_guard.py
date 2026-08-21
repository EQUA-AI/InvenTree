"""Authoritative per-invocation authorization for local AI function tools."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_framework import FunctionInvocationContext, FunctionMiddleware
from ai.core.auth import get_current_principal
from ai.core.tools.capabilities import (
    CATALOG_VERSION,
    CapabilityEntry,
    PolicyKind,
    capability_catalog,
    tool_name,
)
from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

logger = logging.getLogger(__name__)
NON_ENUMERATING_DENIAL = "AI tool invocation was not authorized"


class CapabilityAuthorizationError(PermissionError):
    """Raised when a local tool call fails the server-side capability policy."""

    def __init__(self, reason_code: str):
        super().__init__(NON_ENUMERATING_DENIAL)
        self.reason_code = reason_code


@dataclass(frozen=True)
class CapabilityRunContext:
    """Server-derived capability data bound to one agent run."""

    workflow: str
    modality: str
    principal_user_pk: str | None
    selected_tool_ids: frozenset[str]
    catalog_version: str = CATALOG_VERSION


capability_run_context: ContextVar[CapabilityRunContext | None] = ContextVar(
    "aimms_capability_run", default=None
)


@contextmanager
def bind_capability_run(
    *,
    workflow: str,
    modality: str,
    selected_tools: Sequence[Any],
) -> Iterator[CapabilityRunContext]:
    """Bind immutable server-selected capabilities across one async agent run."""
    principal = get_current_principal()
    run_context = CapabilityRunContext(
        workflow=workflow,
        modality=modality,
        principal_user_pk=principal.user_pk if principal is not None else None,
        selected_tool_ids=frozenset(tool_name(tool) for tool in selected_tools),
    )
    token = capability_run_context.set(run_context)
    try:
        yield run_context
    finally:
        capability_run_context.reset(token)


_catalog_index: tuple[int, dict[str, CapabilityEntry]] | None = None


def _catalog_by_id() -> dict[str, CapabilityEntry]:
    """Index the capability catalog by tool id.

    Keyed on the identity of the (lru-cached) catalog tuple rather than a
    separate lru_cache, so ``capability_catalog.cache_clear()`` (used by tests
    and governance-flag changes) automatically invalidates this index too.
    """
    global _catalog_index
    catalog = capability_catalog()
    if _catalog_index is None or _catalog_index[0] != id(catalog):
        _catalog_index = (id(catalog), {entry.tool_id: entry for entry in catalog})
    return _catalog_index[1]


def _fresh_permission_profile_sync(user_pk: str) -> frozenset[tuple[str, str]]:
    from ai.core.tools.rbac import _all_pairs, _native_pairs
    from django.contrib.auth import get_user_model
    from users.permissions import prefetch_rule_sets

    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=int(user_pk))
    except (OverflowError, TypeError, ValueError, user_model.DoesNotExist):
        return frozenset()

    if not user.is_active:
        return frozenset()
    if user.is_superuser:
        return _all_pairs() | _native_pairs(user)

    groups = prefetch_rule_sets(user)
    granted: set[tuple[str, str]] = set()
    for group in groups:
        for rule in group.prefetched_rule_sets:
            for role, permission in _all_pairs():
                if rule.name == role and getattr(rule, f"can_{permission}", False):
                    granted.add((role, permission))
    return frozenset(granted) | _native_pairs(user)


async def fresh_permission_profile(user_pk: str) -> frozenset[tuple[str, str]]:
    """Resolve current permissions at invocation time, never from selection cache."""
    return await sync_to_async(
        _fresh_permission_profile_sync,
        thread_sensitive=True,
    )(user_pk)


def _has_maintenance_scope_sync() -> bool:
    """Whether the current principal resolves to any maintenance scope."""
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return False
    user = get_user_model().objects.filter(pk=principal.user_pk).first()
    if user is None:
        return False
    from tasks.scope import ScopeError, scope_for_actor

    try:
        return bool(scope_for_actor(user))
    except ScopeError:
        return False


async def _has_maintenance_scope() -> bool:
    """Async wrapper for the maintenance-scope precheck."""
    return await sync_to_async(_has_maintenance_scope_sync, thread_sensitive=True)()


def _arguments_dict(arguments: Any) -> dict[str, Any]:
    if hasattr(arguments, "model_dump"):
        return dict(arguments.model_dump())
    if isinstance(arguments, dict):
        return dict(arguments)
    try:
        return dict(vars(arguments))
    except TypeError:
        return {}


def _deny(reason_code: str) -> None:
    raise CapabilityAuthorizationError(reason_code)


async def authorize_invocation(tool_id: str, arguments: Any) -> CapabilityEntry:
    """Freshly authorize one selected canonical tool before execution."""
    run_context = capability_run_context.get()
    if run_context is None:
        _deny("missing_run_context")
    if run_context.catalog_version != CATALOG_VERSION:
        _deny("stale_catalog")

    principal = get_current_principal()
    if principal is None or run_context.principal_user_pk is None:
        _deny("missing_principal")
    if principal.user_pk != run_context.principal_user_pk:
        _deny("principal_mismatch")
    if tool_id not in run_context.selected_tool_ids:
        _deny("tool_not_selected")

    entry = _catalog_by_id().get(tool_id)
    if entry is None:
        _deny("unknown_tool")
    if run_context.workflow not in entry.workflows:
        _deny("workflow_not_allowed")
    if run_context.modality not in entry.modalities:
        _deny("modality_not_allowed")

    policy = entry.authorization
    if policy.kind is PolicyKind.DISABLED:
        _deny("policy_disabled")

    profile = await fresh_permission_profile(principal.user_pk)
    if policy.all_of and not frozenset(policy.all_of).issubset(profile):
        _deny("required_permission_missing")
    if policy.any_of and not frozenset(policy.any_of).intersection(profile):
        _deny("alternative_permission_missing")

    arguments_dict = _arguments_dict(arguments)
    if policy.authorizer == "database_relation_access":
        if not any(permission == "view" for _, permission in profile):
            _deny("database_view_permission_missing")
    elif policy.authorizer == "part_attachment_access":
        part_id = arguments_dict.get("part_id")
        if not isinstance(part_id, int) or isinstance(part_id, bool) or part_id <= 0:
            _deny("invalid_parent_resource")
    elif policy.authorizer == "part_document_access":
        query = arguments_dict.get("query")
        if not isinstance(query, str) or not query.strip():
            _deny("invalid_document_query")
    elif policy.authorizer == "machine_scope_access":
        # An actor with no resolvable maintenance scope is authorized for no
        # asset at all, so refuse before dispatch rather than letting every
        # read return an empty result and read as "this machine has no data".
        # The row-level check still happens inside the shared readers; this
        # only denies the actor who could never pass it.
        if not await _has_maintenance_scope():
            _deny("maintenance_scope_unresolved")
    elif policy.authorizer == "controlled_corpus_access":
        # Site-scoped corpus search: the filter is built from deployment
        # constants, so the guard checks the inputs it CAN check -- a real
        # query string and a configured site key. Deliberately no maintenance
        # scope requirement (machine narrowing degrades instead of gating).
        query = arguments_dict.get("query")
        if not isinstance(query, str) or not query.strip():
            _deny("invalid_document_query")
        from ai.core.config import get_settings

        if not (get_settings().single_site_policy_key or "").strip():
            _deny("site_scope_unconfigured")
    elif policy.authorizer == "attachment_corpus_access":
        # R2 attachment-corpus search. Re-checks the retrieval flag per call
        # (the catalog's DISABLED branch is process-cached), then the same
        # query/site-key inputs as the controlled corpus, then a resolvable
        # maintenance scope -- the tool's client_codes filter derives from
        # scope_for_actor, so an actor who could never pass it is refused
        # before dispatch rather than reading as "no documents exist".
        from ai.core.config import get_settings

        if not get_settings().feature_attachment_rag_retrieval:
            _deny("attachment_retrieval_disabled")
        query = arguments_dict.get("query")
        if not isinstance(query, str) or not query.strip():
            _deny("invalid_document_query")
        if not (get_settings().single_site_policy_key or "").strip():
            _deny("site_scope_unconfigured")
        if not await _has_maintenance_scope():
            _deny("maintenance_scope_unresolved")
    elif policy.authorizer == "evidence_media_access":
        # R3 evidence-media search. Same four checks as the attachment arm
        # with media vocabulary; the maintenance-scope precheck is even more
        # load-bearing here -- every media document is client-coded, so an
        # actor whose scope cannot resolve would read as "no evidence exists"
        # instead of being refused before dispatch.
        from ai.core.config import get_settings

        if not get_settings().feature_media_rag_retrieval:
            _deny("media_retrieval_disabled")
        query = arguments_dict.get("query")
        if not isinstance(query, str) or not query.strip():
            _deny("invalid_media_query")
        if not (get_settings().single_site_policy_key or "").strip():
            _deny("site_scope_unconfigured")
        if not await _has_maintenance_scope():
            _deny("maintenance_scope_unresolved")
    elif policy.kind is PolicyKind.RESOURCE_AUTHORIZER:
        _deny("unknown_resource_authorizer")

    return entry


#: Denials that are NEVER downgraded to shadow. Shadow mode exists to soak a
#: rail whose catalog coverage is new — it must not soften a failure that says
#: we do not know WHO is calling or WHETHER the run was bound at all. Without
#: this, ``missing_run_context`` carried ``workflow=None``, which is in no
#: enforced set, so an unbound run would have been logged and then dispatched:
#: the exact inverse of the guarantee ``rbac_run`` relies on.
_NEVER_SHADOWED = frozenset({
    "missing_run_context",
    "stale_catalog",
    "missing_principal",
    "principal_mismatch",
})


def _enforced_workflows() -> frozenset[str]:
    """Workflows the guard denies on; every other workflow runs in shadow."""
    try:
        from ai.core.config import get_settings

        raw = get_settings().capability_broker_enforced_workflows
    except Exception:  # pragma: no cover - config absent in minimal envs
        raw = "wf8,general"
    configured = frozenset(part.strip() for part in str(raw or "").split(",") if part.strip())
    # A blank or malformed value must not silently make the guard advisory on
    # the rails it already protects, so the soaked defaults are a floor rather
    # than a default the operator can erase.
    return configured | frozenset({"wf8", "general"})


class CapabilityInvocationMiddleware(FunctionMiddleware):
    """MAF middleware that enforces the capability guard before dispatch.

    Enforcement is per workflow (S11): a workflow outside
    ``capability_broker_enforced_workflows`` still runs the full authorization
    and logs what it *would* have denied, but the call proceeds. That is how a
    missing catalog entry surfaces as one log line instead of a rail that
    denies every call the moment the middleware is attached.
    """

    async def process(self, context: FunctionInvocationContext, next) -> None:
        tool_id = tool_name(context.function)
        # S36: one span per tool invocation. Name and decision code only —
        # arguments and results never reach span attributes.
        import time as _time

        from ai.core.correlation import current_correlation
        from ai.core.tool_events import current_tool_event_sink
        from ai.core.tracing import set_span_attrs, turn_span

        # S46: content-free lifecycle events through the turn-scoped sink
        # (None when the flag is off or outside a bound turn). Name, id,
        # status, duration only — never arguments or results.
        sink = current_tool_event_sink.get()
        tool_call_id = uuid.uuid4().hex
        started_at = _time.perf_counter()
        if sink is not None:
            await sink.started(tool_call_id, tool_id)

        def _elapsed_ms() -> float:
            return (_time.perf_counter() - started_at) * 1000

        with turn_span(
            "aimms.tool", tool_name=tool_id, correlation_id=current_correlation()
        ) as span:
            try:
                await authorize_invocation(tool_id, context.arguments)
            except CapabilityAuthorizationError as exc:
                run_context = capability_run_context.get()
                workflow = run_context.workflow if run_context else None
                enforced = exc.reason_code in _NEVER_SHADOWED or workflow in _enforced_workflows()
                logger.warning(
                    "AI tool invocation denied" if enforced else "AI tool invocation shadow-denied",
                    extra={
                        "tool_id": tool_id,
                        "workflow": workflow,
                        "reason_code": exc.reason_code,
                        "enforced": enforced,
                    },
                )
                set_span_attrs(span, decision_code=exc.reason_code)
                if enforced:
                    if sink is not None:
                        await sink.ended(tool_call_id, tool_id, "denied", _elapsed_ms())
                    raise
            except Exception:
                # An unexpected authorize failure (DB error in the permission
                # profile, scope precheck) must not strand an unpaired
                # TOOL_CALL_START on the wire.
                if sink is not None:
                    await sink.ended(tool_call_id, tool_id, "error", _elapsed_ms())
                raise
            try:
                await next(context)
            except Exception:
                if sink is not None:
                    await sink.ended(tool_call_id, tool_id, "error", _elapsed_ms())
                raise
            if sink is not None:
                await sink.ended(tool_call_id, tool_id, "ok", _elapsed_ms())
            # A None/blank tool result must still yield a tool message: the
            # provider rejects an assistant tool_call whose response message
            # is missing, and the framework drops contentless results from
            # the serialized turn (observed: get_part returning None for a
            # nonexistent part -> provider 400 -> turn 500). Make absence
            # explicit — that is also the honest signal for the model.
            result = getattr(context, "result", ...)
            if result is None or (isinstance(result, str) and not result.strip()):
                context.result = {
                    "found": False,
                    "note": (
                        "No matching record for this query. Absence here is "
                        "not proof none exists under a different identifier."
                    ),
                }
            # S27: record what the tool actually returned into the per-turn
            # capture ledger. Post-next and fail-soft: observation must never
            # change or kill a dispatch that already happened.
            try:
                from ai.core.tools.capture_ledger import record_tool_result

                record_tool_result(tool_id, getattr(context, "result", None))
            except Exception:  # pragma: no cover - observation is best-effort
                pass


__all__ = [
    "NON_ENUMERATING_DENIAL",
    "CapabilityAuthorizationError",
    "CapabilityInvocationMiddleware",
    "CapabilityRunContext",
    "authorize_invocation",
    "bind_capability_run",
    "capability_run_context",
    "fresh_permission_profile",
]
