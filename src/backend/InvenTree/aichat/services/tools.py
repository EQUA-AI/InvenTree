"""Read-only, typed, per-call-authorized scoped chat tools (SC-ADR-003).

This registry is deliberately separate from the shared write-capable AI
toolset: every tool here is read-only, accepts only typed arguments, and
re-authorizes the acting user against the pinned record on every call.
Denial is a normal, visible result — never an exception that leaks record
existence. Every invocation is logged as a ``ChatToolInvocation`` with
redacted arguments, and successful results stamp a ``ChatCitation`` carrying
source revision and as-of time.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from aichat.models import (
    ChatCitation,
    ChatToolInvocation,
    ConversationStatus,
    ScopedConversation,
    ToolAuthorizationResult,
)
from aichat.services import context as context_service

# Bumped to '2' when the scheduling read tools (Phase 6a) were added.
TOOL_REGISTRY_VERSION = '2'

#: Actions the readiness tool may evaluate (mirrors the evaluator's map).
_READINESS_ACTIONS = frozenset({
    'plan',
    'mark_ready',
    'start',
    'hold',
    'resume',
    'verify',
    'complete',
    'cancel',
    'assign',
    'rework',
    'readiness_drift',
})


class ToolError(Exception):
    """Base class carrying a stable scoped-tool error code."""

    code = 'TOOL_NOT_AVAILABLE'


class ToolNotAvailable(ToolError):  # noqa: N818
    """The tool is not registered for this conversation's context type."""

    code = 'TOOL_NOT_AVAILABLE'


class ToolArgumentsInvalid(ToolError):  # noqa: N818
    """The typed argument schema rejected the supplied arguments."""

    code = 'TOOL_ARGUMENTS_INVALID'


class ConversationReadOnly(ToolError):  # noqa: N818
    """The conversation no longer accepts tool calls."""

    code = 'CONVERSATION_READ_ONLY'


class ToolBudgetExceeded(ToolError):  # noqa: N818
    """The per-turn tool budget is exhausted."""

    code = 'CHAT_RATE_LIMITED'


def _int_argument(
    arguments: Mapping[str, Any], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    """Validate one bounded integer argument."""
    raw = arguments.get(name, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ToolArgumentsInvalid(f'{name} must be an integer')
    if raw < minimum or raw > maximum:
        raise ToolArgumentsInvalid(f'{name} must be between {minimum} and {maximum}')
    return raw


def _no_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Reject any argument for tools that accept none."""
    if arguments:
        raise ToolArgumentsInvalid('this tool accepts no arguments')
    return {}


def _readiness_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the readiness action allow-list."""
    extra = set(arguments) - {'action'}
    if extra:
        raise ToolArgumentsInvalid('unknown arguments supplied')
    action = arguments.get('action', 'start')
    if action not in _READINESS_ACTIONS:
        raise ToolArgumentsInvalid('unknown readiness action')
    return {'action': action}


def _steps_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the bounded steps page size."""
    extra = set(arguments) - {'limit'}
    if extra:
        raise ToolArgumentsInvalid('unknown arguments supplied')
    return {
        'limit': _int_argument(arguments, 'limit', default=50, minimum=1, maximum=100)
    }


def _events_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the bounded events page window."""
    extra = set(arguments) - {'limit', 'offset'}
    if extra:
        raise ToolArgumentsInvalid('unknown arguments supplied')
    return {
        'limit': _int_argument(arguments, 'limit', default=20, minimum=1, maximum=50),
        'offset': _int_argument(
            arguments, 'offset', default=0, minimum=0, maximum=10000
        ),
    }


def _work_order_summary(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Return the allow-listed record snapshot."""
    return {
        'summary': context_service.work_order_snapshot(work_order),
        'source_revision': context_service.source_revision_for(work_order),
    }


def _work_order_readiness(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Return the live readiness evaluator envelope unchanged.

    The tool reports only what the active check registry actually emitted; it
    never infers declared-but-unregistered blockers (guide §5.1).
    """
    from dataclasses import asdict

    from tasks.services.readiness import evaluate_work_order_readiness

    readiness = evaluate_work_order_readiness(
        work_order, action=args['action'], actor=user
    )
    envelope = asdict(readiness)
    envelope['evaluated_at'] = readiness.evaluated_at.isoformat()
    return envelope


def _work_order_steps(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Return the primary procedure application's step execution states."""
    from tasks.procedure_models import WorkOrderProcedureApplication

    application = (
        WorkOrderProcedureApplication.objects
        .filter(work_order=work_order, primary=True)
        .order_by('pk')
        .first()
    )
    if application is None:
        return {'application': None, 'steps': [], 'total': 0, 'truncated': False}

    executions = application.step_executions.order_by('sequence')
    total = executions.count()
    limit = args['limit']
    steps = [
        {
            'sequence': item.sequence,
            'step_key': str(item.step_key),
            'status': item.status,
            'required': bool(item.step_snapshot.get('required', False)),
            'step_type': item.step_snapshot.get('step_type', ''),
            'title': str(item.step_snapshot.get('title', ''))[:200],
        }
        for item in executions[:limit]
    ]
    return {
        'application': {
            'id': application.pk,
            'snapshot_hash': application.snapshot_hash,
            'drift_status': application.drift_status,
        },
        'steps': steps,
        'total': total,
        'truncated': total > limit,
    }


def _work_order_kit_status(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Return the job kit's planning state without stock mutation ability."""
    from tasks.jobkit_models import JobKitShortage

    kit = getattr(work_order, 'job_kit', None)
    if kit is None:
        return {'kit': None}
    open_shortages = JobKitShortage.objects.filter(
        line__kit=kit, status__in=['open', 'requested', 'ordered', 'partial']
    ).count()
    return {
        'kit': {
            'status': kit.status,
            'version': kit.version,
            'built_at': kit.built_at.isoformat() if kit.built_at else None,
            'staged_at': kit.staged_at.isoformat() if kit.staged_at else None,
            'released_at': kit.released_at.isoformat() if kit.released_at else None,
            'line_count': kit.lines.count(),
            'open_shortages': open_shortages,
        }
    }


def _work_order_events_page(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Return one bounded page of the work order's command event history."""
    from tasks.workorder_models import WorkOrderEvent

    events = WorkOrderEvent.objects.filter(work_order=work_order).order_by(
        '-created_at', '-pk'
    )
    total = events.count()
    limit, offset = args['limit'], args['offset']
    page = [
        {
            'event_type': item.event_type,
            'from_status': item.from_status,
            'to_status': item.to_status,
            'reason': item.reason[:500],
            'correlation_id': str(item.correlation_id),
            'created_at': item.created_at.isoformat(),
        }
        for item in events[offset : offset + limit]
    ]
    return {'events': page, 'total': total, 'truncated': offset + limit < total}


# ── Scheduling read tools (Phase 6a) ─────────────────────────────────────────
# Read-only, scoped to the pinned work order and its directly related cards
# (same machine / assignee for conflicts, dependency neighbours for the
# preview). They surface the schedule and let the model *preview* what the
# deterministic planner would propose — but they never write; a change still
# requires a governed proposal and a separate confirmation.


def _conflicts_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the optional conflict-window size (in days)."""
    extra = set(arguments) - {'window_days'}
    if extra:
        raise ToolArgumentsInvalid('unknown arguments supplied')
    return {
        'window_days': _int_argument(
            arguments, 'window_days', default=30, minimum=1, maximum=365
        )
    }


def _work_order_schedule(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Return the pinned work order's own schedule and version."""
    machine = getattr(work_order, 'machine', None)
    return {
        'work_order_id': work_order.pk,
        'reference': getattr(work_order, 'reference', '') or '',
        'title': work_order.title,
        'scheduled_start': (
            work_order.scheduled_start.isoformat()
            if work_order.scheduled_start
            else None
        ),
        'scheduled_end': (
            work_order.scheduled_end.isoformat() if work_order.scheduled_end else None
        ),
        'estimated_minutes': work_order.estimated_minutes,
        'machine_id': work_order.machine_id,
        'machine_name': machine.name if machine else None,
        'assigned_to_id': work_order.assigned_to_id,
        'lifecycle_status': work_order.lifecycle_status,
        'lifecycle_version': work_order.lifecycle_version,
        'source_revision': context_service.source_revision_for(work_order),
    }


def _schedule_conflicts(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Return scheduling overlaps that involve the pinned work order.

    Bounded to a window around the card and to cards sharing its machine or
    assignee — the resources it could actually clash on. Only conflict metadata
    (ids + times) is returned, never other cards' full content.
    """
    from datetime import timedelta

    from django.db.models import Q

    from tasks.models import KanbanCard
    from tasks.services.conflicts import detect_conflicts

    if not work_order.scheduled_start or not work_order.scheduled_end:
        return {'conflicts': [], 'note': 'work order is not scheduled'}

    window = timedelta(days=args['window_days'])
    lower = work_order.scheduled_start - window
    upper = work_order.scheduled_end + window

    resource = Q(machine_id=work_order.machine_id)
    if work_order.assigned_to_id:
        resource |= Q(assigned_to_id=work_order.assigned_to_id)

    nearby = (
        KanbanCard.objects
        .filter(is_active=True)
        .filter(scheduled_start__isnull=False, scheduled_end__isnull=False)
        .filter(resource)
        .filter(scheduled_start__lte=upper, scheduled_end__gte=lower)
    )

    conflicts = [
        warning
        for warning in detect_conflicts(list(nearby))
        if work_order.pk in warning['card_ids']
    ]
    return {'conflicts': conflicts, 'count': len(conflicts)}


def _schedule_preview(work_order, args: dict[str, Any], user) -> dict[str, Any]:
    """Preview what the deterministic planner would propose (no write).

    Considers the pinned card and its direct dependency neighbours so the
    proposed placement respects the immediate constraint graph. The planner —
    not the model — decides the times; this only shows the result.
    """
    from django.db.models import Q

    from tasks.models import KanbanCardDependency
    from tasks.services.schedule_planner import PlanRequest, plan_schedule

    neighbours = {work_order.pk}
    for dep in KanbanCardDependency.objects.filter(
        Q(from_card_id=work_order.pk) | Q(to_card_id=work_order.pk)
    ):
        neighbours.add(dep.from_card_id)
        neighbours.add(dep.to_card_id)

    result = plan_schedule(
        PlanRequest(candidate_ids=sorted(neighbours), horizon_start=timezone.now())
    )
    return {
        'operations': [
            {
                'card_id': op.card_id,
                'new_start': op.new_start.isoformat(),
                'new_end': op.new_end.isoformat(),
            }
            for op in result.operations
        ],
        'warnings': result.warnings,
        'unscheduled': result.unscheduled,
    }


@dataclass(frozen=True)
class ToolSpec:
    """One registered read-only tool: typed args, version, and handler."""

    name: str
    version: str
    description: str
    validate: Callable[[Mapping[str, Any]], dict[str, Any]]
    handler: Callable[[Any, dict[str, Any], Any], dict[str, Any]]


_WORK_ORDER_TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            name='work_order_summary',
            version='1',
            description='Allow-listed snapshot of the pinned work order',
            validate=_no_arguments,
            handler=_work_order_summary,
        ),
        ToolSpec(
            name='work_order_readiness',
            version='1',
            description='Live readiness evaluator envelope for one action',
            validate=_readiness_arguments,
            handler=_work_order_readiness,
        ),
        ToolSpec(
            name='work_order_steps',
            version='1',
            description='Primary procedure application step states',
            validate=_steps_arguments,
            handler=_work_order_steps,
        ),
        ToolSpec(
            name='work_order_kit_status',
            version='1',
            description='Job kit planning status and open shortages',
            validate=_no_arguments,
            handler=_work_order_kit_status,
        ),
        ToolSpec(
            name='work_order_events_page',
            version='1',
            description='Bounded page of work-order command events',
            validate=_events_arguments,
            handler=_work_order_events_page,
        ),
        ToolSpec(
            name='work_order_schedule',
            version='1',
            description="The pinned work order's schedule window and version",
            validate=_no_arguments,
            handler=_work_order_schedule,
        ),
        ToolSpec(
            name='schedule_conflicts',
            version='1',
            description='Scheduling overlaps involving the pinned work order',
            validate=_conflicts_arguments,
            handler=_schedule_conflicts,
        ),
        ToolSpec(
            name='schedule_preview',
            version='1',
            description='Planner-proposed placement for this card (no write)',
            validate=_no_arguments,
            handler=_schedule_preview,
        ),
    )
}

_REGISTRY: dict[str, dict[str, ToolSpec]] = {'work_order': _WORK_ORDER_TOOLS}


def tools_for_context(context_type: str) -> tuple[str, ...]:
    """Return the registered tool names for one context type."""
    return tuple(sorted(_REGISTRY.get(context_type, {})))


def _output_hash(result: Mapping[str, Any]) -> str:
    """Hash a tool result for the audit trail without storing content."""
    canonical = json.dumps(result, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _log_invocation(
    conversation: ScopedConversation,
    *,
    turn_key: str,
    tool: str,
    tool_version: str,
    arguments: Mapping[str, Any],
    authorized: bool,
    output_hash: str = '',
    started: float,
) -> ChatToolInvocation:
    """Persist one audit row for a tool call decision."""
    return ChatToolInvocation.objects.create(
        conversation=conversation,
        turn_key=turn_key,
        tool=tool,
        tool_version=tool_version,
        arguments_redacted=dict(arguments),
        authorization_result=(
            ToolAuthorizationResult.ALLOWED
            if authorized
            else ToolAuthorizationResult.DENIED
        ),
        output_hash=output_hash,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def invoke_tool(
    *,
    user,
    conversation: ScopedConversation,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    turn_key: str,
) -> dict[str, Any]:
    """Authorize and execute one scoped tool call, returning its envelope.

    Authorization is re-derived on every call (FR-SCH-004): conversation
    status, per-turn budget, actor scope, and the record's own authority.
    A denial is returned as a normal envelope so the caller (model or UI)
    sees "not authorized" without existence leakage.

    Raises:
        ToolNotAvailable: For tools outside this context's registry.
        ToolArgumentsInvalid: When the typed schema rejects the arguments.
        ConversationReadOnly: When the conversation no longer accepts calls.
        ToolBudgetExceeded: When the per-turn budget is exhausted.
    """
    started = time.monotonic()
    if not isinstance(turn_key, str) or not turn_key or len(turn_key) > 64:
        raise ToolArgumentsInvalid('turn_key is required')

    registry = _REGISTRY.get(conversation.context_type, {})
    spec = registry.get(tool_name)
    if spec is None:
        raise ToolNotAvailable('tool not available for this context')

    if conversation.status != ConversationStatus.ACTIVE:
        raise ConversationReadOnly('conversation is read only')

    if not isinstance(arguments, Mapping | type(None)):
        raise ToolArgumentsInvalid('arguments must be an object')
    args = spec.validate(arguments or {})

    budget = context_service.max_tool_calls_per_turn()
    used = ChatToolInvocation.objects.filter(
        conversation=conversation, turn_key=turn_key
    ).count()
    if used >= budget:
        raise ToolBudgetExceeded('per-turn tool budget exhausted')

    as_of = timezone.now()
    try:
        record = context_service.reauthorize_context(
            user,
            context_type=conversation.context_type,
            object_id=conversation.object_id,
        )
    except context_service.ContextError:
        _log_invocation(
            conversation,
            turn_key=turn_key,
            tool=spec.name,
            tool_version=spec.version,
            arguments=args,
            authorized=False,
            started=started,
        )
        return {
            'tool': spec.name,
            'tool_version': spec.version,
            'authorized': False,
            'error': 'not authorized',
            'as_of': as_of.isoformat(),
            'result': None,
            'citation_id': None,
        }

    result = spec.handler(record, args, user)

    output_hash = _output_hash(result)
    revision = context_service.source_revision_for(record)
    invocation = _log_invocation(
        conversation,
        turn_key=turn_key,
        tool=spec.name,
        tool_version=spec.version,
        arguments=args,
        authorized=True,
        output_hash=output_hash,
        started=started,
    )
    citation = ChatCitation.objects.create(
        conversation=conversation,
        turn_key=turn_key,
        source_type='tool_result',
        source_id=f'{conversation.context_type}:{conversation.object_id}',
        source_revision=revision,
        locator={'tool': spec.name, 'arguments': args},
        excerpt_hash=output_hash,
        authorization_class='record_scope',
        as_of=as_of,
    )
    return {
        'tool': spec.name,
        'tool_version': spec.version,
        'authorized': True,
        'error': None,
        'as_of': as_of.isoformat(),
        'source_revision': revision,
        'result': result,
        'citation_id': citation.pk,
        'invocation_id': invocation.pk,
    }
