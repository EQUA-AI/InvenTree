"""Default-off root adoption of the existing source-owned read consumers.

Only server configuration selects an adapter, source environment or operation.
Request/model context cannot enroll a source. The existing S3/S6 consumers keep
their independent authentication, native authority, provenance and final-output
checks; this boundary adds configuration and selection continuity. No remote
transport, model, operational writer or historical attribution is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ai.core.auth import get_current_principal, revalidate_current_ai_principal
from ai.core.streaming import EventEmitter, RunContext
from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from collections.abc import Callable

_CONTENT_OPERATIONS = frozenset({
    "work.core",
    "work.context",
    "maintenance.direct",
    "work_document.metadata",
    "machine_document.metadata",
    "work_document.content",
    "machine_document.content",
})
_READ_PINS = (None, "wf8", "wf1", "wf1_diagnostics")
_MAX_PK = (1 << 63) - 1


class SourceReadError(Exception):
    """A selected source read failed; native execution must not replace it."""

    def __init__(self):
        """Use a fixed message without source or provider exception contents."""
        super().__init__("Selected source read is unavailable")


def _literal(value, limit):
    return type(value) is str and 1 <= len(value) <= limit and bool(value.strip())


def _pk(value):
    return type(value) is int and 1 <= value <= _MAX_PK


@dataclass(frozen=True, kw_only=True, repr=False)
class SourceReadRequest:
    """Untrusted query and narrowing hints, never source or actor authority."""

    message: str
    thread_id: str
    pinned_workflow_id: str | None
    schema_version: str = field(default="aimms.ai.source-request.v1", init=False)


@dataclass(frozen=True, kw_only=True, repr=False)
class ContentReadSelection:
    """A server-selected direct S3 read, retaining historical withholding."""

    environment: str
    operation: str
    source_pk: int
    max_bytes: int | None = None
    schema_version: str = field(default="aimms.ai.content-selection.v1", init=False)

    def __post_init__(self):
        """Validate exact source coordinates without granting any access."""
        _selection_key(self)


@dataclass(frozen=True, kw_only=True, repr=False)
class HistoryReadSelection:
    """A server-selected bounded recorded window; fixture support only today."""

    environment: str
    machine_pk: int
    source_pk: int
    binding_pk: int
    window: object = field(repr=False)
    adapter: object = field(default=None, repr=False)
    schema_version: str = field(default="aimms.ai.history-selection.v1", init=False)

    def __post_init__(self):
        """Reject malformed bounds; source capability remains independent."""
        _selection_key(self)


@dataclass(frozen=True, kw_only=True, repr=False)
class SourceReadAdapter:
    """Trusted server selector; None means unsupported before source dispatch.

    Selection is not a source/field/relationship grant. No adapter is installed
    by default. The selector must be synchronous, bounded and source-free;
    source I/O belongs to the independently authorized selected consumer.
    """

    select: Callable
    restore: Callable | None = None

    def __post_init__(self):
        """Require an explicit server callable, not a dotted path from input."""
        from inspect import iscoroutinefunction

        if self.restore is not None and (
            not callable(self.restore)
            or iscoroutinefunction(self.restore)
            or iscoroutinefunction(self.restore.__call__)
        ):
            raise ValueError("A synchronous server restore selector is required")
        if (
            not callable(self.select)
            or iscoroutinefunction(self.select)
            or iscoroutinefunction(self.select.__call__)
        ):
            raise ValueError("A synchronous server selector is required")


def _selection_key(selection):
    """Validate and detach literal selection coordinates for later rechecks."""
    if type(selection) not in (ContentReadSelection, HistoryReadSelection):
        raise SourceReadError()
    if not _literal(selection.environment, 256) or not _pk(selection.source_pk):
        raise SourceReadError()
    if type(selection) is ContentReadSelection:
        from assets.integration_sources import MAX_MACHINE_ATTACHMENT_CONTENT_BYTES

        if (
            type(selection.schema_version) is not str
            or selection.schema_version != "aimms.ai.content-selection.v1"
            or type(selection.operation) is not str
            or selection.operation not in _CONTENT_OPERATIONS
        ):
            raise SourceReadError()
        if selection.operation.endswith(".content"):
            if (
                type(selection.max_bytes) is not int
                or not 1 <= selection.max_bytes <= MAX_MACHINE_ATTACHMENT_CONTENT_BYTES
            ):
                raise SourceReadError()
        elif selection.max_bytes is not None:
            raise SourceReadError()
        return (
            selection.environment,
            selection.schema_version,
            selection.operation,
            selection.source_pk,
            selection.max_bytes,
        )

    from machine_health.services.integration_history import HistoryWindow
    from machine_health.services.integration_s6_adapter import FixtureHistoryAdapter

    if (
        type(selection.schema_version) is not str
        or selection.schema_version != "aimms.ai.history-selection.v1"
        or not _pk(selection.machine_pk)
        or not _pk(selection.binding_pk)
        or type(selection.window) is not HistoryWindow
        or (selection.adapter is not None and type(selection.adapter) is not FixtureHistoryAdapter)
    ):
        raise SourceReadError()
    HistoryWindow.__post_init__(selection.window)
    return (
        selection.environment,
        selection.schema_version,
        selection.machine_pk,
        selection.source_pk,
        selection.binding_pk,
        selection.window.start_ms,
        selection.window.end_ms,
        id(selection.adapter),
    )


def _configured_adapter():
    from django.conf import settings

    return getattr(settings, "AIMMS_AI_SOURCE_READ_ADAPTER", None) if settings.configured else None


def _prepare(selection):
    """Use the existing real ASGI/source consumers; no public authorizer hook."""
    if type(selection) is ContentReadSelection:
        from ai.core.integrations.integration_s3_content import read_source_analysis

        return read_source_analysis(
            environment=selection.environment,
            operation=selection.operation,
            source_pk=selection.source_pk,
            **({"max_bytes": selection.max_bytes} if selection.max_bytes is not None else {}),
        )

    from ai.core.integrations.integration_s6_history import read_recorded_history_analysis

    return read_recorded_history_analysis(
        environment=selection.environment,
        machine_pk=selection.machine_pk,
        source_pk=selection.source_pk,
        binding_pk=selection.binding_pk,
        window=selection.window,
        adapter=selection.adapter,
    )


class _GuardedEmitter(EventEmitter):
    """Recheck server enrollment around each existing native delivery await."""

    def __init__(self, target, guard, source=None):
        self._target, self._guard, self._source = target, guard, source

    async def emit(self, event):
        await self._guard()
        if self._source is not None and event.data.get("kind") in (
            "source_record_analysis",
            "recorded_history_analysis",
        ):
            from ai.core.integrations.source_transcripts import capture_packet
            from ai.core.streaming import AGUIEvent

            analysis, selection = self._source

            async def live_check():
                await self._guard()
                await sync_to_async(analysis._guard, thread_sensitive=True)()
                await self._guard()

            public = capture_packet(event.data, selection, analysis._source.outcome, live_check)
            event = AGUIEvent(
                event_type=event.event_type,
                data={**event.data, "source_observation": public},
                thread_id=event.thread_id,
                run_id=event.run_id,
                agent_name=event.agent_name,
                event_id=event.event_id,
                timestamp=event.timestamp,
            )
        await self._target.emit(event)
        await self._guard()

    async def subscribe(self, handler):
        await self._guard()
        return await self._target.subscribe(handler)


class _PreparedSourceRead:
    """Invocation-local delivery; no cache, replay token or public grant."""

    def __init__(self, analysis, guard, selection):
        self._analysis, self._guard, self._selection = analysis, guard, selection

    async def run_stream(self, run_context):
        """Preserve the native evidence/event contract and guard the yield too."""
        if type(run_context) is not RunContext:
            raise SourceReadError()
        target = run_context.emitter
        if not isinstance(target, EventEmitter):
            raise SourceReadError()
        route = (run_context.thread_id, run_context.run_id, run_context.agent_name)

        async def guard():
            await self._guard()
            if run_context.emitter is not target or route != (
                run_context.thread_id,
                run_context.run_id,
                run_context.agent_name,
            ):
                raise SourceReadError()

        context = RunContext(
            emitter=_GuardedEmitter(target, guard, (self._analysis, self._selection)),
            thread_id=route[0],
            run_id=route[1],
            agent_name=route[2],
        )
        await guard()
        async for chunk in self._analysis.run_stream(context):
            await guard()
            yield chunk
        await guard()


async def select_source_read(*, message, thread_id, context):
    """Negotiate a server read before native routing, without input enrollment.

    Off, voice and unsupported pins return None before source dispatch. After
    selecting a source, faults/denial/incomplete capability fail explicitly;
    there is no automatic fallback to a successful native/empty response.
    """
    adapter = _configured_adapter()
    if adapter is None:
        return None
    if type(adapter) is not SourceReadAdapter:
        raise SourceReadError()
    SourceReadAdapter.__post_init__(adapter)
    modality = context.get("modality", "text")
    pin = context.get("pinned_workflow_id")
    if (
        type(modality) is not str
        or modality != "text"
        or (pin is not None and (type(pin) is not str or pin not in _READ_PINS))
    ):
        return None
    if not _literal(message, 32768) or not _literal(thread_id, 256):
        raise SourceReadError()
    principal = get_current_principal()
    selector = adapter.select

    async def guard():
        if (
            principal is None
            or get_current_principal() is not principal
            or _configured_adapter() is not adapter
            or adapter.select is not selector
            or await revalidate_current_ai_principal() is not principal
        ):
            raise SourceReadError()
        # Reauthentication awaits native session/account work. A configuration
        # or invocation change during that await must not survive the check.
        if (
            get_current_principal() is not principal
            or _configured_adapter() is not adapter
            or adapter.select is not selector
        ):
            raise SourceReadError()

    await guard()
    request = SourceReadRequest(message=message, thread_id=thread_id, pinned_workflow_id=pin)
    selection = await sync_to_async(selector, thread_sensitive=True)(request)
    await guard()
    if selection is None:
        return None
    key = _selection_key(selection)

    async def selected_guard():
        await guard()
        if _selection_key(selection) != key:
            raise SourceReadError()

    await selected_guard()
    analysis = await sync_to_async(_prepare, thread_sensitive=True)(selection)
    await selected_guard()
    if analysis.state != "available":
        raise SourceReadError()
    return _PreparedSourceRead(analysis, selected_guard, selection)
