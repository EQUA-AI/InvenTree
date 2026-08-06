"""
AIMMS Application Entry Point

This is the main entry point for the AIMMS backend.
It initializes all components and starts the FastAPI server
with SSE streaming support.

Usage:
    # Development
    python -m ai.core.app

    # Production
    uvicorn ai.core.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator  # noqa: TC003
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ai.core.api import get_devui
from ai.core.auth import (
    AIPrincipal,
    get_current_principal,
    record_identity_anomaly,
    require_ai_principal,
)
from ai.core.config import get_devui_settings, get_settings
from ai.core.memory import get_semantic_cache
from ai.core.middleware import (
    RateLimitConfig,
    RateLimitMiddleware,
    get_rate_limiter,
    get_retry_stats,
)
from ai.core.streaming import AGUIEvent, EventType, InMemoryEventEmitter, SSEEventStream
from ai.core.trusted_context import build_trusted_turn_context
from ai.core.turn_service import (
    NormalizedTurnService,
    TurnAlreadyRunning,
    TurnExecutionFailed,
)
from ai.core.workflows.root import RootWorkflow, get_root_workflow
from aichat.models import TurnModality, TurnState
from aichat.services import (
    IdempotencyConflict,
    ScopedThreadRejected,
    ThreadNotFound,
    ThreadRepository,
)
from asgiref.sync import sync_to_async
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Configure logging.
#
# InvenTree/settings.py calls logging.basicConfig() during django.setup(), which
# runs before this module is imported (InvenTree/asgi.py builds the Django app
# first). A second basicConfig() is then a documented no-op, so this block used
# to leave the root logger at WARNING and every logger.info() in ai/core was
# discarded in production -- including the voice write-confirmation audit trail,
# which had zero records for the entire 2026-07-26 test session.
#
# Set the level on our own namespace instead of trying to reconfigure the root:
# records still reach the handler Django installed, and InvenTree's own logging
# is left alone.
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("ai").setLevel(os.getenv("AIMMS_LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)


# Pydantic models for API
class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    message: str
    thread_id: str | None = None
    user_id: str = "anonymous"
    context: dict[str, Any] | None = None
    file_ids: list[str] | None = None
    modality: str = TurnModality.TEXT
    modality_metadata: dict[str, Any] | None = None
    idempotency_key: str | None = None
    correlation_id: str | None = None


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""

    thread_id: str
    message: str
    agent: str
    workflow_used: str | None = None


class UploadResponse(BaseModel):
    """Response model for file upload."""

    file_id: str
    filename: str
    size: int
    content_type: str
    thread_id: str


class HealthResponse(BaseModel):
    """Response model for health check."""

    status: str
    version: str
    environment: str


class ThreadInfo(BaseModel):
    """Information about a thread."""

    thread_id: str
    title: str = ""
    message_count: int
    turn_count: int = 0
    summary: str = ""
    created_at: str | None = None
    last_activity: str | None = None
    is_persisted: bool = False


class ThreadSyncResponse(BaseModel):
    """Response for thread sync operation."""

    threads: list[ThreadInfo]
    sync_token: str | None = None
    has_more: bool = False


class ThreadMessage(BaseModel):
    """A message in a thread."""

    id: str
    role: str
    content: str
    timestamp: str
    tool_name: str | None = None
    workflow_id: str | None = None


# Global root workflow (instantiated per request)
def get_workflow_root() -> RootWorkflow:
    """Get a new root workflow instance."""
    return get_root_workflow()


_turn_service: NormalizedTurnService | None = None


def get_turn_service() -> NormalizedTurnService:
    """Return the normalized service used by every interactive modality.

    One shared instance per process: the service holds no per-turn state,
    and rebuilding it per request rebuilt the router/adapter wiring too.
    """
    global _turn_service
    if _turn_service is None:
        from ai.core.voice.tool_actions import get_voice_write_gate

        _turn_service = NormalizedTurnService(
            workflow_factory=get_workflow_root,
            voice_write_gate=get_voice_write_gate(),
        )
    return _turn_service


def _principal() -> AIPrincipal:
    """Return the immutable mounted-boundary principal or fail closed."""

    principal = get_current_principal()
    if not isinstance(principal, AIPrincipal):
        raise HTTPException(status_code=401, detail="AI authentication required")
    return principal


def _repository(principal: AIPrincipal) -> ThreadRepository:
    """Bind the sole thread repository to the server-owned pilot boundary."""

    return ThreadRepository(actor=principal.user_pk, scope_key=principal.scope)


def _observe_legacy_identity(value: str | None, *, source: str) -> None:
    """Record, but never consume, caller-supplied identity compatibility fields."""

    if value is not None:
        record_identity_anomaly(f"legacy_{source}_user_id")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan manager.

    Handles startup and shutdown tasks:
    - Initialize root workflow
    - Start DevUI if enabled
    - Initialize semantic cache
    - Initialize conversation persistence and search index
    """
    settings = get_settings()
    devui_settings = get_devui_settings()

    logger.info(f"Starting AIMMS Backend (env: {settings.env})")

    # Initialize root workflow
    get_workflow_root()
    logger.info("Root workflow initialized")

    # S17 model/embedding pins: refuse to serve a retrieval plane whose live
    # embedding output cannot be stored in the configured index. A ModelPinError
    # here aborts startup by design; EMBEDDING_BOOT_PROBE_ENABLED=false is the
    # one-env rollback. Unconfigured planes skip loudly inside the probe.
    from ai.core.integrations.model_pins import run_boot_probes

    await asyncio.to_thread(run_boot_probes)

    # Voice Live provider gateway (WS4-T4 deployment wiring). Installed only
    # when the realtime flag is on; otherwise the SDP relay keeps reporting
    # honestly unavailable and text remains the fallback.
    if settings.feature_voice_live:
        from ai.core.voice import gateway as voice_gateway
        from ai.core.voice.routes import (
            set_provider_channel_closer,
            set_provider_channel_factory,
        )

        set_provider_channel_factory(voice_gateway.channel_for_session)
        set_provider_channel_closer(voice_gateway.close_channel)
        logger.info("Voice Live provider gateway installed")

    # Durable threads and turns are owned exclusively by the aichat repository.
    # The workflow's legacy memory object is execution-local and is not an
    # authorization or persistence source.
    logger.info("Authorized aichat persistence boundary initialized")

    # Initialize semantic cache
    if settings.semantic_cache_enabled:
        get_semantic_cache()
        logger.info(
            f"Semantic cache initialized (threshold: {settings.semantic_cache_similarity_threshold})"
        )

    # Start DevUI if enabled
    if devui_settings.enabled:
        devui = get_devui()
        await devui.start()
        logger.info(f"DevUI available at {devui.url}")

    yield

    # Cleanup
    if settings.feature_voice_live:
        from ai.core.voice import gateway as voice_gateway

        await voice_gateway.shutdown()

    if devui_settings.enabled:
        devui = get_devui()
        await devui.stop()

    logger.info("AIMMS Backend shutdown complete")


# Create FastAPI app. Interactive docs and the OpenAPI schema are exposed in
# development only: outside dev they were the sole unauthenticated standalone
# surface (execution-plan S8).
_expose_docs = get_settings().env == "development"
app = FastAPI(
    title="AIMMS Backend",
    description="AI-powered Manufacturing Management System",
    version="2.3.0",
    lifespan=lifespan,
    dependencies=[Depends(require_ai_principal)],
    docs_url="/docs" if _expose_docs else None,
    redoc_url="/redoc" if _expose_docs else None,
    openapi_url="/openapi.json" if _expose_docs else None,
)

# Realtime Voice session routes (WS4). The router inherits the boundary
# principal dependency above; feature flags keep every route fail-closed.
from ai.core.voice.routes import router as _voice_router  # noqa: E402

app.include_router(_voice_router)

# Configure CORS - restrict to InvenTree frontend origins
# Set CORS_ALLOWED_ORIGINS env var for production:
#   CORS_ALLOWED_ORIGINS="https://your-inventree-domain.com,https://app.inventree.com"
settings = get_settings()
logger.info(f"CORS allowed origins: {settings.cors_allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE"],  # PUT needed for uploads
    allow_headers=[
        "Content-Type",
        "Accept",
        "Authorization",
        "X-User-ID",
        "X-Request-ID",
        "X-CSRFToken",
        "Idempotency-Key",
    ],
    expose_headers=[
        "X-RateLimit-Remaining",
        "Retry-After",
    ],
)

# Configure Rate Limiting
rate_limit_config = RateLimitConfig(
    max_requests_per_minute=20,
    max_requests_per_hour=200,
    global_max_requests_per_minute=100,
    endpoint_limits={
        "/chat": {"per_minute": 10, "per_hour": 100},
        "/chat/stream": {"per_minute": 10, "per_hour": 100},
    },
)
app.add_middleware(
    RateLimitMiddleware,
    limiter=get_rate_limiter(rate_limit_config),
    user_id_header="X-User-ID",
    exempt_paths={
        "/health",
        "/docs",
        "/openapi.json",
        "/workflows",
        "/rate-limit/stats",
        "/retry/stats",
    },
)


# ==============================================================================
# File Upload Configuration
# ==============================================================================

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".csv", ".docx"}
MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
UPLOAD_TTL_HOURS = 24


def _is_audio_upload(contents: bytes, content_type: str | None) -> bool:
    """Is audio upload."""
    declared = (content_type or "").split(";", 1)[0].strip().casefold()
    if declared.startswith("audio/"):
        return True
    header = contents[:16]
    return (
        header.startswith((b"ID3", b"OggS", b"fLaC", b"\x1aE\xdf\xa3"))
        or header.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))
        or (header.startswith(b"RIFF") and header[8:12] == b"WAVE")
        or (len(header) >= 12 and header[4:8] == b"ftyp")
    )


def _get_upload_dir() -> Path:
    """Get the ai_uploads directory under MEDIA_ROOT."""
    try:
        from django.conf import settings as django_settings

        media_root = Path(django_settings.MEDIA_ROOT)
    except Exception:
        # Fallback for standalone mode
        media_root = Path(os.environ.get("MEDIA_ROOT", "/home/inventree/data/media"))
    upload_dir = media_root / "ai_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _get_thread_upload_dir(thread_id: str) -> Path:
    """Get thread-scoped upload directory."""
    if (
        not thread_id
        or len(thread_id) > 80
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in thread_id
        )
    ):
        raise HTTPException(status_code=400, detail="Invalid thread identifier")
    thread_dir = _get_upload_dir() / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    return thread_dir


def resolve_upload_path(file_id: str, *, expected_thread_id: str | None = None) -> Path | None:
    """Resolve a file only inside its already-authorized owning thread."""

    upload_dir = _get_upload_dir()
    parts = Path(file_id).parts
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        return None
    file_thread_id, filename = parts
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    if any(char not in allowed for part in parts for char in part):
        return None
    if expected_thread_id is not None and file_thread_id != expected_thread_id:
        return None
    candidate = (upload_dir / file_thread_id / filename).resolve()
    expected_parent = (upload_dir / file_thread_id).resolve()
    if candidate.parent != expected_parent:
        return None
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    thread_id: str = Form("default"),
) -> UploadResponse:
    """
    Upload a file for AI chat processing.

    Stores the file under MEDIA_ROOT/ai_uploads/{thread_id}/{uuid}_{filename}.
    The returned file_id can be passed in subsequent chat requests.

    Constraints:
      - Max 20 MB
      - Allowed types: .pdf, .png, .jpg, .jpeg, .xlsx, .csv, .docx
    """
    principal = _principal()
    repository = _repository(principal)
    try:
        await sync_to_async(repository.get_or_create, thread_sensitive=True)(thread_id)
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None

    # Validate filename & extension
    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
        )

    # Read the file (enforce size limit)
    contents = await file.read(MAX_UPLOAD_SIZE_BYTES + 1)
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(contents)} bytes). Maximum is {MAX_UPLOAD_SIZE_BYTES} bytes (20 MB).",
        )
    if _is_audio_upload(contents, file.content_type):
        raise HTTPException(status_code=400, detail="Audio uploads are not allowed")

    # Build a unique filename
    file_uuid = uuid.uuid4().hex[:12]
    safe_name = "".join(c for c in original_name if c.isalnum() or c in (".", "_", "-"))
    stored_name = f"{file_uuid}_{safe_name}"

    thread_dir = _get_thread_upload_dir(thread_id)
    dest = thread_dir / stored_name
    dest.write_bytes(contents)

    # file_id is the relative path from ai_uploads root
    safe_thread = thread_dir.name
    file_id = f"{safe_thread}/{stored_name}"

    logger.info("AI file uploaded (thread_id=%s, size=%d)", thread_id, len(contents))

    return UploadResponse(
        file_id=file_id,
        filename=original_name,
        size=len(contents),
        content_type=file.content_type or "application/octet-stream",
        thread_id=thread_id,
    )


@app.post("/upload/cleanup")
async def cleanup_uploads(
    max_age_hours: int = Query(default=UPLOAD_TTL_HOURS, ge=1, le=168),
) -> dict[str, Any]:
    """
    Remove uploaded files older than max_age_hours.
    Called periodically or manually.
    """
    import time

    if not _principal().is_staff:
        raise HTTPException(status_code=403, detail="Staff access required")

    upload_dir = _get_upload_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    errors = 0

    for thread_dir in upload_dir.iterdir():
        if not thread_dir.is_dir():
            continue
        for fpath in thread_dir.iterdir():
            try:
                if fpath.stat().st_mtime < cutoff:
                    fpath.unlink()
                    removed += 1
            except OSError:
                logger.warning("AI upload cleanup failed for one file")
                errors += 1
        # Remove empty thread dirs
        try:
            if not any(thread_dir.iterdir()):
                thread_dir.rmdir()
        except Exception:
            pass

    logger.info(f"Upload cleanup: removed {removed} files, {errors} errors")
    return {"removed": removed, "errors": errors, "max_age_hours": max_age_hours}


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        version="2.3.0",
        environment=settings.env,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Adapt typed REST chat to the shared normalized turn service."""

    principal = _principal()
    if "user_id" in request.model_fields_set:
        _observe_legacy_identity(request.user_id, source="body")
    idempotency_key = request.idempotency_key or str(uuid.uuid4())
    correlation_id = request.correlation_id or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{principal.subject}:{idempotency_key}")
    )
    try:
        if request.modality != TurnModality.TEXT:
            raise ValueError("typed chat only accepts text modality")
        trusted_context = build_trusted_turn_context(
            principal,
            correlation_id=correlation_id,
            browser_context=request.context,
            server_route_hints=("/chat",),
        )
        metadata = await _turn_metadata(principal, request)
        result = await get_turn_service().process(
            actor=principal,
            thread_id=request.thread_id,
            content=request.message,
            modality=TurnModality.TEXT,
            trusted_context=trusted_context,
            modality_metadata=metadata,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except (IdempotencyConflict, TurnAlreadyRunning):
        raise HTTPException(status_code=409, detail="Idempotency conflict") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        logger.error("Normalized AI chat failed")
        raise HTTPException(status_code=500, detail="AI turn failed") from None

    if result.response_state == TurnState.FAILED:
        raise HTTPException(status_code=500, detail="AI turn failed")
    if result.response_state != TurnState.COMPLETE:
        raise HTTPException(
            status_code=409,
            detail=f"AI turn is {result.response_state}",
        )

    return ChatResponse(
        thread_id=result.thread_id,
        message=result.message,
        agent=result.agent,
        workflow_used=result.workflow_used,
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """
    Streaming chat endpoint with Server-Sent Events.

    Returns real-time updates during agent execution:
    - RUN_STARTED/RUN_FINISHED lifecycle events
    - AGENT_THINKING/AGENT_EXECUTING state changes
    - WORKFLOW_STARTED for workflow selection
    - TEXT_MESSAGE_START/CONTENT/END for response streaming
    - TOOL_CALL_* for tool execution
    - HITL_REQUIRED for approval requests
    - ERROR for failures

    AG-UI event format (SSE):
        event: EVENT_TYPE
        data: {"key": "value", ...}
    """
    principal = _principal()
    if "user_id" in request.model_fields_set:
        _observe_legacy_identity(request.user_id, source="body")
    idempotency_key = request.idempotency_key or str(uuid.uuid4())
    correlation_id = request.correlation_id or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{principal.subject}:{idempotency_key}")
    )
    try:
        if request.modality != TurnModality.TEXT:
            raise ValueError("typed chat only accepts text modality")
        trusted_context = build_trusted_turn_context(
            principal,
            correlation_id=correlation_id,
            browser_context=request.context,
            server_route_hints=("/chat/stream",),
        )
        metadata = await _turn_metadata(principal, request)
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # A caller-created id is retained for visible SSE filtering. When omitted,
    # this is the id the service durably creates through get_or_create.
    thread_id = request.thread_id or f"thread_{uuid.uuid4().hex}"

    async def event_generator() -> AsyncIterator[str]:
        """Event generator."""
        emitter = InMemoryEventEmitter()
        stream = SSEEventStream(emitter, thread_id=thread_id)
        await stream.start()

        async def process_in_background() -> None:
            """Process in background."""
            try:
                await get_turn_service().process(
                    actor=principal,
                    thread_id=thread_id,
                    content=request.message,
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
                        data={"message": "Idempotency conflict", "code": "idempotency_conflict"},
                        thread_id=thread_id,
                    )
                )
            except TurnExecutionFailed:
                # The service emitted and durably captured one value-free
                # RUN_ERROR event before raising this marker.
                logger.error("Normalized AI stream turn failed")
            except Exception:
                logger.error("Normalized AI stream failed")
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
            async for event_data in stream.events():
                yield event_data
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


async def _turn_metadata(principal: AIPrincipal, request: ChatRequest) -> dict[str, Any]:
    """Build bounded modality metadata after authorizing every file reference."""

    metadata: dict[str, Any] = {
        "untrusted_client_context": dict(request.context or {}),
    }
    if not request.file_ids:
        return metadata
    if not request.thread_id:
        raise ValueError("thread_id is required when using uploaded files")

    repository = _repository(principal)
    try:
        await sync_to_async(repository.get, thread_sensitive=True)(request.thread_id)
    except (ThreadNotFound, ScopedThreadRejected):
        raise ThreadNotFound("Thread not found") from None

    uploaded_files: list[dict[str, Any]] = []
    for file_id in request.file_ids:
        path = resolve_upload_path(file_id, expected_thread_id=request.thread_id)
        if path is None:
            raise ValueError("Uploaded file is unavailable for this thread")
        uploaded_files.append({
            "file_id": file_id,
            "path": str(path),
            "filename": path.name,
            "extension": path.suffix.lower(),
            "size": path.stat().st_size,
        })
    metadata["uploaded_files"] = uploaded_files
    return metadata


# ===== Thread Management Endpoints =====


@app.get("/threads", response_model=ThreadSyncResponse)
async def list_threads(
    user_id: str | None = None,
    include_persisted: bool = True,
    limit: int = Query(default=50, ge=1, le=100),
) -> ThreadSyncResponse:
    """List only threads within the authenticated owner/scope boundary."""

    del include_persisted  # Durable storage is always authoritative in WS1.
    _observe_legacy_identity(user_id, source="query")
    repository = _repository(_principal())

    def materialize() -> tuple[list[ThreadInfo], int]:
        """Materialize."""
        all_threads = repository.list()
        selected = all_threads[: max(1, min(limit, 100))]
        result = [
            ThreadInfo(
                thread_id=thread.pk,
                title=thread.title,
                message_count=thread.messages.count(),
                turn_count=thread.turns.count(),
                summary=thread.summary,
                created_at=thread.created_at.isoformat(),
                last_activity=thread.updated_at.isoformat(),
                is_persisted=True,
            )
            for thread in selected
        ]
        return result, len(all_threads)

    threads, total = await sync_to_async(materialize, thread_sensitive=True)()
    return ThreadSyncResponse(
        threads=threads,
        sync_token=None,
        has_more=total > len(threads),
    )


@app.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    include_messages: bool = True,
    message_limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Return a durable thread only after scope-first authorization."""

    principal = _principal()
    repository = _repository(principal)

    def materialize() -> dict[str, Any]:
        """Materialize."""
        thread = repository.get(thread_id)
        stored_messages = repository.messages(thread_id) if include_messages else []
        selected = stored_messages[-max(1, min(message_limit, 200)) :]
        return {
            "thread_id": thread.pk,
            "user_id": principal.user_pk,
            "title": thread.title,
            "turn_count": thread.turns.count(),
            "last_workflow": thread.last_workflow,
            "pending_handoff": None,
            "summary": thread.summary,
            "messages": [
                {
                    "id": message.pk,
                    "role": message.role,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat(),
                    "tool_name": message.metadata.get("tool_name"),
                    "workflow_id": message.metadata.get("workflow_id"),
                    "response_state": message.metadata.get("response_state"),
                }
                for message in selected
            ],
            "metrics": {},
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
            "is_persisted": True,
        }

    try:
        return await sync_to_async(materialize, thread_sensitive=True)()
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str) -> dict[str, str]:
    """Delete a thread through the sole authorized repository."""

    try:
        await sync_to_async(_repository(_principal()).delete, thread_sensitive=True)(thread_id)
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    return {"status": "deleted", "thread_id": thread_id}


@app.put("/threads/{thread_id}")
async def update_thread(
    thread_id: str,
    title: str | None = None,
) -> dict[str, Any]:
    """Rename a thread through the sole authorized repository."""

    if title is None:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        thread = await sync_to_async(_repository(_principal()).rename, thread_sensitive=True)(
            thread_id, title
        )
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    return {"thread_id": thread.pk, "title": thread.title, "updated": True}


@app.post("/threads/sync")
async def sync_threads(
    user_id: str | None = None,
    local_thread_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Sync threads between frontend and backend.

    This endpoint helps resolve conflicts when frontend has threads
    that backend doesn't know about, or vice versa.

    Args:
        user_id: User ID for filtering
        local_thread_ids: Thread IDs the frontend has locally

    Returns:
        - threads_to_fetch: Threads that exist on server but not locally
        - threads_to_remove: Threads that don't exist on server anymore
        - threads_in_sync: Threads that exist on both
    """
    _observe_legacy_identity(user_id, source="query")
    local_thread_ids = local_thread_ids or []
    local_set = set(local_thread_ids)

    # Get all server threads
    sync_response = await list_threads(user_id=None, include_persisted=True, limit=100)
    server_thread_ids = {t.thread_id for t in sync_response.threads}

    return {
        "threads_to_fetch": list(server_thread_ids - local_set),
        "threads_to_remove": list(local_set - server_thread_ids),
        "threads_in_sync": list(local_set & server_thread_ids),
        "server_threads": [t.model_dump() for t in sync_response.threads],
    }


# ==============================================================================
# HITL (Human-in-the-Loop) Endpoints
# ==============================================================================


class HITLRespondRequest(BaseModel):
    """Request model for HITL approval/rejection."""

    request_id: str
    approved: bool
    reason: str | None = None
    user_id: str = "anonymous"


class HITLResponse(BaseModel):
    """Response model for HITL operations."""

    success: bool
    request_id: str
    status: str
    message: str


# Global dict to track pending HITL requests
# In production, this would be in Redis or similar
_pending_hitl_requests: dict[str, dict[str, Any]] = {}


def register_hitl_request(
    request_id: str,
    action: str,
    details: dict[str, Any],
    thread_id: str,
    timeout_seconds: int = 300,
) -> None:
    """Register a pending HITL request."""
    import time

    _pending_hitl_requests[request_id] = {
        "action": action,
        "details": details,
        "thread_id": thread_id,
        "created_at": time.time(),
        "expires_at": time.time() + timeout_seconds,
        "status": "pending",
    }


def get_hitl_request(request_id: str) -> dict[str, Any] | None:
    """Get a pending HITL request."""
    import time

    request = _pending_hitl_requests.get(request_id)
    if request:  # noqa: SIM102
        # Check if expired
        if time.time() > request["expires_at"]:
            request["status"] = "expired"
    return request


def resolve_hitl_request(
    request_id: str,
    approved: bool,
    reason: str | None = None,
    user_id: str = "anonymous",
) -> dict[str, Any] | None:
    """Resolve a pending HITL request."""
    request = _pending_hitl_requests.get(request_id)
    if request:
        request["status"] = "approved" if approved else "rejected"
        request["resolved_by"] = user_id
        request["resolved_reason"] = reason
        # Keep for a bit for status queries, then clean up
        # In production, use TTL in Redis
    return request


@app.post("/hitl/respond", response_model=HITLResponse)
async def respond_to_hitl(request: HITLRespondRequest) -> HITLResponse:
    """
    Respond to a Human-in-the-Loop approval request.

    This endpoint is called when the user approves or rejects an action
    that requires human verification (e.g., creating/deleting items,
    large batch operations, etc.).

    Request:
        - request_id: Unique ID of the HITL request
        - approved: True if approved, False if rejected
        - reason: Optional reason (especially for rejections)
        - user_id: User making the decision

    Response:
        - success: Whether the response was recorded
        - request_id: The request ID
        - status: New status (approved/rejected/expired/not_found)
        - message: Human-readable message
    """
    _principal()  # authentication is still required to receive the notice
    if "user_id" in request.model_fields_set:
        _observe_legacy_identity(request.user_id, source="body")

    # WS7: the in-memory HITL rail is retired. It never dispatched a real
    # domain command, and body-identity approval is unacceptable. Reviews
    # and confirmations happen only on the authenticated proposal surface
    # (/api/aichat/proposals/), which executes canonical work-order commands
    # and stores real receipts. This endpoint resolves nothing.
    logger.info("Legacy HITL respond called; rail is retired")
    return HITLResponse(
        success=False,
        request_id=request.request_id,
        status="retired",
        message=(
            "The legacy approval rail is retired and performs no action. "
            "Confirm or reject actions on the authenticated proposal "
            "surface at /api/aichat/proposals/."
        ),
    )


@app.get("/hitl/pending")
async def get_pending_hitl(
    thread_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Get pending HITL requests.

    Args:
        thread_id: Filter by thread ID
        user_id: User ID for filtering

    Returns:
        List of pending HITL requests
    """
    import time

    _observe_legacy_identity(user_id, source="query")
    repository = _repository(_principal())
    if thread_id:
        try:
            await sync_to_async(repository.get, thread_sensitive=True)(thread_id)
        except (ThreadNotFound, ScopedThreadRejected):
            raise HTTPException(status_code=404, detail="Thread not found") from None

    # WS7: the in-memory rail is retired; never surface approvable items
    # from it. The authenticated proposal surface owns pending approvals.
    return []

    pending = []

    for request_id, request in _pending_hitl_requests.items():
        # Filter by thread if specified
        if thread_id and request.get("thread_id") != thread_id:
            continue

        # Only return pending requests
        if request["status"] != "pending":
            continue

        # Check if expired
        if time.time() > request["expires_at"]:
            continue

        request_thread_id = request.get("thread_id")
        if request_thread_id:
            try:
                await sync_to_async(repository.get, thread_sensitive=True)(request_thread_id)
            except (ThreadNotFound, ScopedThreadRejected):
                continue

        pending.append({
            "request_id": request_id,
            "action": request["action"],
            "details": request["details"],
            "thread_id": request.get("thread_id"),
            "expires_in_seconds": int(request["expires_at"] - time.time()),
        })

    return pending


@app.get("/cache/stats")
async def cache_stats() -> dict[str, Any]:
    """Get semantic cache statistics."""
    settings = get_settings()
    if not settings.semantic_cache_enabled:
        return {"enabled": False}

    cache = get_semantic_cache()
    return {
        "enabled": True,
        **cache.get_stats(),
    }


@app.post("/cache/invalidate")
async def invalidate_cache(
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Invalidate cache entries."""
    if not _principal().is_staff:
        raise HTTPException(status_code=403, detail="Staff access required")
    settings = get_settings()
    if not settings.semantic_cache_enabled:
        return {"enabled": False, "invalidated": 0}

    cache = get_semantic_cache()
    count = cache.invalidate(workflow_id=workflow_id)
    return {"invalidated": count}


@app.get("/rate-limit/stats")
async def rate_limit_stats() -> dict[str, Any]:
    """Get rate limiting statistics."""
    limiter = get_rate_limiter()
    return {
        "rate_limiting": limiter.get_stats(),
        "config": {
            "max_requests_per_minute": rate_limit_config.max_requests_per_minute,
            "max_requests_per_hour": rate_limit_config.max_requests_per_hour,
            "global_max_per_minute": rate_limit_config.global_max_requests_per_minute,
            "endpoint_limits": rate_limit_config.endpoint_limits,
        },
    }


@app.get("/retry/stats")
async def retry_stats() -> dict[str, Any]:
    """Get retry mechanism statistics."""
    stats = get_retry_stats()
    return {
        "retry_stats": stats.to_dict(),
    }


@app.get("/workflows")
async def list_workflows() -> list[dict[str, Any]]:
    """List available workflows and their status."""
    settings = get_settings()

    return [
        {
            "id": "wf1_diagnostics",
            "name": "Diagnostics",
            "tier": "T6",
            "enabled": settings.feature_wf1_diagnostics,
            "description": "Complex troubleshooting and fault analysis",
        },
        {
            "id": "wf2_parts_analysis",
            "name": "Parts Analysis",
            "tier": "T2",
            "enabled": settings.feature_wf2_sequential,
            "description": "BOM analysis and compatibility checking",
        },
        {
            "id": "wf3_research",
            "name": "Research",
            "tier": "T3",
            "enabled": settings.feature_wf3_concurrent,
            "description": "Multi-source research and documentation lookup",
        },
        {
            "id": "wf4_procurement",
            "name": "Procurement",
            "tier": "T4",
            "enabled": settings.feature_wf4_procurement,
            "description": "Purchase order creation with HITL approval",
        },
        {
            "id": "wf5_cpq",
            "name": "Configure-Price-Quote",
            "tier": "T5",
            "enabled": False,
            "description": "Retired: CPQ contradicts the client-scoped fork",
        },
        {
            "id": "wf6_documents",
            "name": "Documents",
            "tier": "T4",
            "enabled": settings.feature_wf6_incoming_docs,
            "description": "Document processing and data extraction",
        },
        {
            "id": "wf8_lookup",
            "name": "Lookup",
            "tier": "T1",
            "enabled": settings.feature_wf8_lookup,
            "description": "Simple stock and part queries",
        },
    ]


@app.get("/data/status")
async def data_status() -> dict[str, Any]:
    """
    Get current data mode status.

    Returns whether the system is using demo data or live InvenTree API.
    """
    from ai.core.integrations import get_mode_status

    return get_mode_status()


@app.post("/data/switch")
async def switch_data_mode(mode: str) -> dict[str, Any]:
    """
    Switch between demo and live data modes.

    Note: This only updates the setting. A server restart is required
    for the change to take full effect.

    Args:
        mode: Either "demo" or "live"
    """
    if not _principal().is_staff:
        raise HTTPException(status_code=403, detail="Staff access required")
    if mode not in ("demo", "live"):
        raise HTTPException(status_code=400, detail="Mode must be 'demo' or 'live'")

    from ai.core.integrations import get_mode_status, reset_provider
    from ai.core.switch_mode import get_env_file_path, update_env_value

    env_path = get_env_file_path()
    value = "true" if mode == "demo" else "false"

    if update_env_value("USE_DEMO_DATASET", value, env_path):
        # Reset the provider so next request uses new mode
        reset_provider()
        return {
            "success": True,
            "mode": mode,
            "message": f"Switched to {mode} mode. Restart server for full effect.",
            "status": get_mode_status(),
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to update .env file")


def main() -> None:
    """Run the application."""
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "ai.core.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
