"""POST /agui — the spec-clean AG-UI run endpoint (S49).

Mirrors ``/chat/stream`` structurally (same single turn pipeline, background
task, cancel-on-disconnect, in-band errors) with the translation layer at
the stream edge. Dark behind ``feature_agui_endpoint``: off ⇒ 404 at request
time, indistinguishable from absent.

Auth is inherited: the ASGI boundary middleware plus the app-level
``require_ai_principal`` dependency cover this router; CSRF/origin checks
apply to the unsafe method exactly as on ``/chat/stream``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from ai.core.agui.models import RunAgentInput, derive_user_message
from ai.core.agui.translate import SpecSSEStream, SpecTranslator, encode_sse
from ai.core.streaming import (
    AGUIEvent,
    EventType,
    InMemoryEventEmitter,
)
from aichat.models import TurnModality
from asgiref.sync import sync_to_async
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agui"])


@router.api_route(
    "/agui",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def run_agui(request: Request) -> StreamingResponse:
    """Run one agent turn over the official AG-UI protocol.

    Dark-shape discipline: the FLAG CHECK runs before body parsing and
    before method dispatch, so a flag-off deployment answers 404 to every
    method and every body — indistinguishable from an absent route (a
    pydantic-typed body parameter would 422 malformed input before the
    handler could 404). The route is also excluded from the OpenAPI schema.
    """
    # Runtime imports from the app module: the router is included mid-module,
    # so a module-level import would be circular (voice routes precedent).
    from ai.core.app import (
        ChatRequest,
        _principal,
        _server_correlation_id,
        _turn_metadata,
        get_turn_service,
    )
    from ai.core.config import get_settings
    from ai.core.trusted_context import build_trusted_turn_context, resolve_actor_locale
    from ai.core.turn_service import TurnAlreadyRunning, TurnExecutionFailed
    from aichat.services import (
        IdempotencyConflict,
        ScopedThreadRejected,
        ThreadNotFound,
    )
    from pydantic import ValidationError

    if not getattr(get_settings(), "feature_agui_endpoint", False):
        raise HTTPException(status_code=404, detail="Not found")
    if request.method != "POST":
        raise HTTPException(status_code=405, detail="Method not allowed")

    try:
        run_input = RunAgentInput.model_validate(await request.json())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from None
    except ValueError:
        raise HTTPException(status_code=422, detail="Request body must be JSON") from None

    principal = _principal()
    try:
        content = derive_user_message(run_input)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if len(run_input.messages) > 1:
        # Value-free by design: count only, never message content.
        logger.debug("agui inbound transcript dropped (message_count=%d)", len(run_input.messages))

    # The turn key: forwardedProps wins, then the header, then a mint.
    # runId is deliberately NOT the key — the SDK mints a fresh runId per
    # runAgent() attempt, so retries of one logical turn would replay-miss.
    idempotency_key = (
        (run_input.forwarded_props.idempotency_key or "").strip()
        or (request.headers.get("Idempotency-Key") or "").strip()
        or str(uuid.uuid4())
    )
    correlation_id = _server_correlation_id(principal, idempotency_key, None)
    try:
        trusted_context = build_trusted_turn_context(
            principal,
            correlation_id=correlation_id,
            browser_context=None,
            server_route_hints=("/agui",),
            locale=await sync_to_async(resolve_actor_locale, thread_sensitive=True)(
                principal.user_pk
            ),
        )
        metadata = await _turn_metadata(
            principal,
            # thread_id must ride along: uploads are authorized against the
            # thread, and _turn_metadata rejects file_ids without one.
            ChatRequest(
                message=content,
                thread_id=run_input.thread_id,
                file_ids=run_input.forwarded_props.file_ids,
            ),
        )
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    thread_id = run_input.thread_id or f"thread_{uuid.uuid4().hex}"
    translator = SpecTranslator(thread_id=thread_id, run_id=run_input.run_id)

    async def event_generator() -> AsyncIterator[str]:
        emitter = InMemoryEventEmitter()
        stream = SpecSSEStream(emitter, thread_id=thread_id, translator=translator)
        await stream.start()

        async def process_in_background() -> None:
            try:
                await get_turn_service().process(
                    actor=principal,
                    thread_id=thread_id,
                    content=content,
                    modality=TurnModality.TEXT,
                    trusted_context=trusted_context,
                    modality_metadata=metadata,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    emitter=emitter,
                )
            except asyncio.CancelledError:
                raise
            except (ThreadNotFound, ScopedThreadRejected):
                await emitter.emit(
                    AGUIEvent(
                        event_type=EventType.RUN_ERROR,
                        data={"message": "Thread not found", "code": "thread_not_found"},
                        thread_id=thread_id,
                    )
                )
            except (IdempotencyConflict, TurnAlreadyRunning):
                await emitter.emit(
                    AGUIEvent(
                        event_type=EventType.RUN_ERROR,
                        data={
                            "message": "Idempotency conflict",
                            "code": "idempotency_conflict",
                        },
                        thread_id=thread_id,
                    )
                )
            except TurnExecutionFailed:
                # The service emitted and durably captured one value-free
                # RUN_ERROR event before raising this marker.
                logger.error("AG-UI stream turn failed")
            except Exception:
                logger.error("AG-UI stream failed")
                await emitter.emit(
                    AGUIEvent(
                        event_type=EventType.RUN_ERROR,
                        data={"message": "AI turn failed", "code": "turn_failed"},
                        thread_id=thread_id,
                    )
                )
            finally:
                await stream.stop()

        process_task = asyncio.create_task(process_in_background())

        try:
            # Spec ordering guarantee: RUN_STARTED echoing the request ids is
            # always the first frame; the translator dedupes the pipeline's
            # own RUN_STARTED (whose stored ids differ on replay).
            yield encode_sse(translator.run_started_frame())
            async for sse_frame in stream.frames():
                yield sse_frame
        finally:
            if not process_task.done():
                process_task.cancel()
                try:  # noqa: SIM105
                    await process_task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
