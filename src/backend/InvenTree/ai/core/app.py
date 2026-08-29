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
from ai.core.middleware import (
    RateLimitConfig,
    RateLimitMiddleware,
    get_rate_limiter,
    get_retry_stats,
)
from ai.core.pilot_latch import PilotLatchUnavailable, PilotStopped
from ai.core.quota.admission import AdmissionSaturated
from ai.core.streaming import AGUIEvent, EventType, InMemoryEventEmitter, SSEEventStream
from ai.core.trusted_context import build_trusted_turn_context, resolve_actor_locale
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
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
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
    # S1: client staleness detector for the thread analysis scope. When set,
    # a mismatch with the server's current scope version returns 409
    # ``scope_version_conflict`` before any model call. Detection only — it
    # grants nothing, and legacy clients simply omit it.
    expected_scope_version: int | None = None


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
    # S32b: set on rows in the shared_threads list; owned rows omit it.
    shared: bool = False
    # S1: compact active-analysis-scope summary (mode/version/display_label);
    # old clients ignore the key. The full payload lives on
    # ``GET /threads/{id}/scope``.
    active_scope: dict[str, Any] | None = None


class ThreadSyncResponse(BaseModel):
    """Response for thread sync operation."""

    threads: list[ThreadInfo]
    sync_token: str | None = None
    has_more: bool = False
    # S32b: read-only threads granted to the caller (empty when the
    # feature is dark, so the response shape is always stable).
    shared_threads: list[ThreadInfo] = []
    # S49: server capability advertisement. The frontend's auto wire
    # selection keys off capabilities["agui"]; old clients ignore the key.
    capabilities: dict[str, bool] = {}


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


def _record_rejection(code: str, principal: AIPrincipal | None) -> None:
    """Best-effort §8.10 rejection ledger row; never blocks the response."""
    import contextlib

    from ai.core.pilot_latch import record_request_rejection

    # Telemetry only — a ledger failure must never alter the rejection.
    with contextlib.suppress(Exception):
        record_request_rejection(code, getattr(principal, "user_pk", None))


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
    - Run the S17 model/embedding boot probes
    - Start DevUI if enabled
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

# S36: request spans for the AI plane. Dark by default — without a
# configured tracer provider every span is a no-op, and an absent
# instrumentation package (bare local venvs) is tolerated inside.
from ai.core.tracing import instrument_fastapi  # noqa: E402

instrument_fastapi(app)

# Realtime Voice session routes (WS4). The router inherits the boundary
# principal dependency above; feature flags keep every route fail-closed.
from ai.core.voice.routes import router as _voice_router  # noqa: E402

app.include_router(_voice_router)

# S49: the spec-clean AG-UI adapter. Dark behind feature_agui_endpoint
# (404 at request time); same auth inheritance as the voice router.
from ai.core.agui.routes import router as _agui_router  # noqa: E402

app.include_router(_agui_router)

# Middleware ORDER INVARIANT (S12): Starlette's add_middleware inserts at the
# top of the stack, so the LAST middleware added is the OUTERMOST. CORS must
# be added last (outermost) so responses the rate limiter/budget gate writes
# itself — 429s and 503s — still traverse CORSMiddleware and carry the
# expose_headers (Retry-After, X-RateLimit-Remaining) cross-origin. With the
# previous order those limiter responses bypassed CORS entirely and a
# cross-origin frontend could not read Retry-After off a 429.

# Configure Rate Limiting (added FIRST = runs inside CORS)
settings = get_settings()
_chat_limits = {
    "per_minute": settings.ai_rate_chat_per_minute,
    "per_hour": settings.ai_rate_chat_per_hour,
}
rate_limit_config = RateLimitConfig(
    max_requests_per_minute=settings.ai_rate_user_per_minute,
    max_requests_per_hour=settings.ai_rate_user_per_hour,
    global_max_requests_per_minute=settings.ai_rate_global_per_minute,
    endpoint_limits={
        "/chat": dict(_chat_limits),
        "/chat/stream": dict(_chat_limits),
        # S49: the AG-UI adapter is the same model rail as /chat/stream.
        "/agui": dict(_chat_limits),
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
        # S12: an over-cap user must be able to read WHY — the quota
        # preflight is never rate limited (and never budgeted).
        "/quota/preflight",
    },
)

# Configure CORS - restrict to InvenTree frontend origins
# Set CORS_ALLOWED_ORIGINS env var for production:
#   CORS_ALLOWED_ORIGINS="https://your-inventree-domain.com,https://app.inventree.com"
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


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        version="2.3.0",
        environment=settings.env,
    )


def _server_correlation_id(principal: Any, idempotency_key: str, client_value: str | None) -> str:
    """Mint the turn's correlation id server-side (S36).

    A client-supplied value is never used — it could poison logs, collide
    across users, or overflow the 100-char persistence columns. We do not
    400 on it (breaking clients over a telemetry field is disproportionate);
    a differing value is noted once and ignored.
    """
    minted = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{principal.subject}:{idempotency_key}"))
    if client_value and client_value != minted:
        logger.info("client correlation_id ignored; server-minted id used")
    return minted


async def _reject_stale_scope_version(
    principal: AIPrincipal, thread_id: str | None, expected_version: int | None
) -> None:
    """409 when the client's scope version is stale, BEFORE any model call.

    A pure staleness detector (S1): it grants nothing, and a client that
    never sends ``expected_scope_version`` is unaffected. Raced updates
    between this check and turn intake are harmless — the turn still binds
    the then-current scope atomically and is labeled with that version.
    """
    if expected_version is None or not thread_id:
        return
    repository = _repository(principal)
    try:
        payload = await sync_to_async(repository.get_scope, thread_sensitive=True)(thread_id)
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    if payload["version"] != expected_version:
        raise HTTPException(status_code=409, detail="scope_version_conflict")


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Adapt typed REST chat to the shared normalized turn service."""

    principal = _principal()
    if "user_id" in request.model_fields_set:
        _observe_legacy_identity(request.user_id, source="body")
    await _reject_stale_scope_version(principal, request.thread_id, request.expected_scope_version)
    idempotency_key = request.idempotency_key or str(uuid.uuid4())
    correlation_id = _server_correlation_id(principal, idempotency_key, request.correlation_id)
    try:
        if request.modality != TurnModality.TEXT:
            raise ValueError("typed chat only accepts text modality")
        trusted_context = build_trusted_turn_context(
            principal,
            correlation_id=correlation_id,
            browser_context=request.context,
            server_route_hints=("/chat",),
            locale=await sync_to_async(resolve_actor_locale, thread_sensitive=True)(
                principal.user_pk
            ),
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
    except AdmissionSaturated as exc:
        # S13: typed backpressure — no provider call started, short jittered
        # retry, no durable queue.
        raise HTTPException(
            status_code=503,
            detail="ai_capacity_busy",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except PilotStopped as exc:
        # S15: the pilot is stopped — non-retryable, no Retry-After.
        _record_rejection(exc.code, principal)
        raise HTTPException(status_code=503, detail=exc.code) from None
    except PilotLatchUnavailable as exc:
        # S15: fail CLOSED while the armed latch is unreadable.
        _record_rejection(exc.code, principal)
        raise HTTPException(
            status_code=503,
            detail=exc.code,
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
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
    await _reject_stale_scope_version(principal, request.thread_id, request.expected_scope_version)
    idempotency_key = request.idempotency_key or str(uuid.uuid4())
    correlation_id = _server_correlation_id(principal, idempotency_key, request.correlation_id)
    try:
        if request.modality != TurnModality.TEXT:
            raise ValueError("typed chat only accepts text modality")
        trusted_context = build_trusted_turn_context(
            principal,
            correlation_id=correlation_id,
            browser_context=request.context,
            server_route_hints=("/chat/stream",),
            locale=await sync_to_async(resolve_actor_locale, thread_sensitive=True)(
                principal.user_pk
            ),
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
            except AdmissionSaturated as exc:
                # S13: typed backpressure on the streaming rail.
                await emitter.emit(
                    AGUIEvent(
                        event_type=EventType.RUN_ERROR,
                        data={
                            "message": "The AI service is at capacity. Please retry shortly.",
                            "code": "ai_capacity_busy",
                            "retry_after": exc.retry_after,
                        },
                        thread_id=thread_id,
                    )
                )
            except PilotStopped as exc:
                # S15: the pilot is stopped — typed, non-retryable.
                _record_rejection(exc.code, principal)
                await emitter.emit(
                    AGUIEvent(
                        event_type=EventType.RUN_ERROR,
                        data={"message": "The AI pilot is stopped.", "code": exc.code},
                        thread_id=thread_id,
                    )
                )
            except PilotLatchUnavailable as exc:
                _record_rejection(exc.code, principal)
                await emitter.emit(
                    AGUIEvent(
                        event_type=EventType.RUN_ERROR,
                        data={
                            "message": "The AI pilot gate is unavailable.",
                            "code": exc.code,
                            "retry_after": exc.retry_after,
                        },
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
    q: str | None = Query(default=None, max_length=200),
) -> ThreadSyncResponse:
    """List only threads within the authenticated owner/scope boundary.

    ``q`` (S20 A8) searches titles and message content — inside the same
    repository boundary, so a query can only ever match the caller's own
    threads. The response shape is unchanged.
    """

    del include_persisted  # Durable storage is always authoritative in WS1.
    _observe_legacy_identity(user_id, source="query")
    repository = _repository(_principal())

    def _info(thread, *, shared: bool = False) -> ThreadInfo:
        """Project one thread row for the sync response."""
        return ThreadInfo(
            thread_id=thread.pk,
            title=thread.title,
            message_count=thread.messages.count(),
            turn_count=thread.turns.count(),
            summary=thread.summary,
            created_at=thread.created_at.isoformat(),
            last_activity=thread.updated_at.isoformat(),
            is_persisted=True,
            shared=shared,
            active_scope=repository.scope_summary(thread),
        )

    def materialize() -> tuple[list[ThreadInfo], int, list[ThreadInfo]]:
        """Materialize."""
        all_threads = repository.search(q, limit=limit) if q else repository.list()
        selected = all_threads[: max(1, min(limit, 100))]
        result = [_info(thread) for thread in selected]
        # S32b: shared threads ride the same response; list_shared returns
        # [] whenever the feature is dark. The search box intentionally
        # does not search shared transcripts.
        shared = [] if q else [_info(thread, shared=True) for thread in repository.list_shared()]
        return result, len(all_threads), shared

    threads, total, shared_threads = await sync_to_async(materialize, thread_sensitive=True)()
    return ThreadSyncResponse(
        threads=threads,
        sync_token=None,
        has_more=total > len(threads),
        shared_threads=shared_threads,
        capabilities={
            "agui": bool(getattr(get_settings(), "feature_agui_endpoint", False)),
            # S1: the scope endpoints exist — the frontend gates the whole
            # scope-banner flow on this advertisement (no endpoint probing).
            "thread_scope": True,
            # S10: evidence-set expansion endpoints exist while the gate is
            # not "off" (rows only exist once the executor writes them).
            "evidence_sets": getattr(get_settings(), "evidence_gate_mode", "off") != "off",
        },
    )


def _persisted_provenance(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the diagnosis provenance from the persisted event list.

    Evidence and confidence previously vanished on reload because the message
    projection carried neither; the persisted STATE_DELTA event already holds
    them, so the projection derives the same payload the live stream delivered
    (S22 reload-fidelity fix, provenance included in passing).
    """
    for event in metadata.get("events") or []:
        if isinstance(event, dict) and event.get("kind") == "diagnosis_provenance":
            return {
                "confidence": event.get("confidence"),
                "evidence": event.get("evidence"),
            }
    return None


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
        # S32b: reads (and only reads) honor explicit grants; the shared
        # marker lets the client withhold every write affordance.
        thread, shared = repository.get_readable(thread_id)
        stored_messages = repository.readable_messages(thread_id) if include_messages else []
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
                    # S22: cards and their resolutions survive reload.
                    "question": message.metadata.get("question"),
                    "question_resolution": message.metadata.get("question_resolution"),
                    "provenance": _persisted_provenance(message.metadata),
                    # S28: server-observed entity chips survive reload.
                    "entities": message.metadata.get("entities"),
                    "media_evidence": message.metadata.get("media_evidence"),
                    # S10/S11: the consolidated evidence attachment reloads
                    # with the SAME object shape the live wires delivered.
                    "evidence_analysis": message.metadata.get("evidence_analysis"),
                    # S14: the resolved model identities stamped on the
                    # turn — the battery runner verifies pins from the wire.
                    "model_versions": message.metadata.get("model_versions"),
                }
                for message in selected
            ],
            "metrics": {},
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
            "is_persisted": True,
            "shared": shared,
            # S1: compact scope summary; the full payload (and update path)
            # lives on the dedicated /threads/{id}/scope endpoints.
            "active_scope": repository.scope_summary(thread),
        }

    try:
        return await sync_to_async(materialize, thread_sensitive=True)()
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None


#: Evidence-set cursors are actor/thread/set-bound and expire (S10 §7.6).
_EVIDENCE_CURSOR_SALT = "aimms.evidence-set-cursor"
_EVIDENCE_CURSOR_MAX_AGE_S = 3600


def _evidence_not_found() -> HTTPException:
    """One generic 404 for EVERY failure mode — nothing is disclosed."""
    return HTTPException(status_code=404, detail="Not found")


@app.get("/threads/{thread_id}/evidence-sets/{set_id}")
async def get_evidence_set(thread_id: str, set_id: str, response: Response) -> dict[str, Any]:
    """The set header: counts, coverage, calculation — never scope hashes."""
    principal = _principal()
    repository = _repository(principal)

    def materialize() -> dict[str, Any]:
        """Materialize."""
        row = repository.evidence_set(thread_id, set_id)
        return {
            "set_id": row.pk,
            "source_class": row.source_class,
            "filters": row.filters,
            "population_count": row.population_count,
            "evaluated_count": row.evaluated_count,
            "displayed_count": row.displayed_count,
            "complete_population": row.complete_population,
            "supports_expansion": row.supports_expansion,
            "member_count": row.member_count,
            "member_cap": row.member_cap,
            "calculation": row.calculation,
            "created_at": row.created_at.isoformat(),
        }

    try:
        payload = await sync_to_async(materialize, thread_sensitive=True)()
    except (ThreadNotFound, ScopedThreadRejected):
        raise _evidence_not_found() from None
    response.headers["Cache-Control"] = "private, no-store"
    return payload


@app.get("/threads/{thread_id}/evidence-sets/{set_id}/members")
async def get_evidence_set_members(
    thread_id: str,
    set_id: str,
    response: Response,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """One page of exact membership, reauthorized per member at read time."""
    from django.core import signing

    principal = _principal()
    repository = _repository(principal)

    after_ordinal = 0
    if cursor:
        try:
            claims = signing.loads(
                cursor, salt=_EVIDENCE_CURSOR_SALT, max_age=_EVIDENCE_CURSOR_MAX_AGE_S
            )
        except signing.BadSignature:
            raise _evidence_not_found() from None
        if (
            claims.get("actor") != principal.user_pk
            or claims.get("thread") != thread_id
            or claims.get("set") != set_id
        ):
            raise _evidence_not_found()
        after_ordinal = int(claims.get("after") or 0)

    def materialize() -> dict[str, Any]:
        """Materialize."""
        row = repository.evidence_set(thread_id, set_id)
        members = repository.evidence_set_members(
            thread_id, set_id, after_ordinal=after_ordinal, limit=limit
        )
        next_cursor = None
        if len(members) == limit and members:
            next_cursor = signing.dumps(
                {
                    "actor": principal.user_pk,
                    "thread": thread_id,
                    "set": set_id,
                    "after": members[-1]["member_index"],
                },
                salt=_EVIDENCE_CURSOR_SALT,
            )
        return {
            "members": members,
            "population_count": row.population_count,
            "complete": bool(row.supports_expansion and row.member_count == row.evaluated_count),
            "next_cursor": next_cursor,
        }

    try:
        payload = await sync_to_async(materialize, thread_sensitive=True)()
    except (ThreadNotFound, ScopedThreadRejected):
        raise _evidence_not_found() from None
    response.headers["Cache-Control"] = "private, no-store"
    return payload


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


class ThreadScopeUpdateRequest(BaseModel):
    """Replace a thread's analysis scope under optimistic concurrency (S1)."""

    expected_version: int
    scope: dict[str, Any]


@app.get("/quota/preflight")
async def quota_preflight(
    estimated_tokens: int | None = None,
    estimated_requests: int | None = None,
) -> dict[str, Any]:
    """The S12 read-only quota preflight (rate-limit-exempt, never budgeted).

    Reports the caller's effective profile, per-level token usage
    (used/reserved/remaining/cap), the request-rate windows on the spending
    rail, counter-store health — and whether an estimated run fits. An
    over-cap user always gets a 200 here: the endpoint exists so a blocked
    caller can read WHY (and a battery runner can fail before Q01, not at
    Q173).
    """
    principal = _principal()

    def _read() -> dict[str, Any]:
        from ai.core.middleware.budget import seconds_to_utc_midnight
        from ai.core.middleware.rate_limit_store import CacheRateLimitStore
        from ai.core.quota.profiles import (
            QuotaSourceUnavailable,
            resolve_profile,
            standard_snapshot,
        )
        from ai.core.quota.reservation import level_usage
        from ai.core.quota.wire import (
            QuotaPreflightPayload,
            QuotaStoreStatus,
            QuotaTokenLevel,
            QuotaWindowStatus,
        )

        store_healthy = True
        try:
            snapshot = resolve_profile(principal.user_pk)
        except QuotaSourceUnavailable:
            store_healthy = False
            snapshot = standard_snapshot()

        usages = level_usage(
            user_pk=principal.user_pk,
            tenant_id=str(getattr(principal, "scope", "") or "default"),
            snapshot=snapshot,
        )
        if usages is None:
            store_healthy = False
            usages = ()
        reset_after = seconds_to_utc_midnight()
        tokens = {
            usage.level: QuotaTokenLevel(
                used=usage.used,
                reserved=usage.reserved,
                remaining=max(0, usage.cap - usage.committed) if usage.cap else 0,
                cap=usage.cap,
                reset_after_s=reset_after,
            )
            for usage in usages
        }

        store = CacheRateLimitStore()
        requests: dict[str, QuotaWindowStatus] = {}
        for name, window_seconds, limit in (
            ("per_minute", 60, snapshot.requests_per_minute),
            ("per_hour", 3600, snapshot.requests_per_hour),
        ):
            used = store.peek(
                scope="user",
                endpoint="/chat",
                key=principal.rate_limit_key,
                window_seconds=window_seconds,
            )
            if used is None:
                store_healthy = False
                used = 0
            requests[name] = QuotaWindowStatus(
                limit=limit,
                used=used,
                remaining=max(0, limit - used),
                reset_after_s=window_seconds,
            )

        # A per-process cache (LocMem) cannot back enforcement — surface it
        # (the Part-4 backend check, made observable).
        from django.core.cache import caches

        backend = type(caches["default"]).__module__
        shared = "locmem" not in backend and "dummy" not in backend

        fits: bool | None = None
        if estimated_tokens is not None or estimated_requests is not None:
            fits = True
            if estimated_tokens is not None:
                user_level = tokens.get("user")
                if user_level is not None and user_level.cap:
                    fits = fits and estimated_tokens <= user_level.remaining
            if estimated_requests is not None:
                fits = fits and estimated_requests <= requests["per_hour"].remaining

        # S15: latch visibility for battery runners and dashboards. Fail-soft
        # read — null means "could not read", never "clear".
        pilot_stopped: bool | None = None
        try:
            from ai.core.pilot_latch import load_latch_state

            pilot_stopped = load_latch_state().latched
        except Exception:
            pilot_stopped = None

        return QuotaPreflightPayload(
            profile=snapshot.profile,
            policy_version=snapshot.version,
            tokens=tokens,
            requests=requests,
            store=QuotaStoreStatus(healthy=store_healthy, shared=shared),
            fits=fits,
            pilot_stopped=pilot_stopped,
        ).model_dump()

    return await asyncio.to_thread(_read)


@app.get("/threads/{thread_id}/scope")
async def get_thread_scope(thread_id: str) -> dict[str, Any]:
    """Return the active analysis scope (owner, or shared read-only)."""

    repository = _repository(_principal())
    try:
        return await sync_to_async(repository.get_scope, thread_sensitive=True)(thread_id)
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None


@app.put("/threads/{thread_id}/scope")
async def set_thread_scope(thread_id: str, request: ThreadScopeUpdateRequest) -> dict[str, Any]:
    """Replace the analysis scope on an owned thread.

    409 ``scope_version_conflict`` reports a stale ``expected_version`` (the
    client refreshes and asks the user to resend). 400
    ``scope_update_rejected`` covers every authorization failure without
    disclosing which asset id failed or whether it exists.
    """
    from ai.core.analysis.scope import ScopeValidationError
    from aichat.services.threads import (
        InvalidBoundary,
        ScopeUpdateRejected,
        ScopeVersionConflict,
    )

    repository = _repository(_principal())

    def perform() -> dict[str, Any]:
        """Materialize the thread if needed, then update its scope.

        A client-minted thread id has no server row until its first turn,
        and the machine-page launch flow sets scope BEFORE the first send
        (the ``/upload`` precedent). ``get_or_create`` keeps namespace and
        collision safety: a foreign existing id still resolves to
        ``ThreadNotFound``, never to another principal's thread.
        """
        repository.get_or_create(thread_id)
        return repository.set_scope(
            thread_id, request.scope, expected_version=request.expected_version
        )

    try:
        return await sync_to_async(perform, thread_sensitive=True)()
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except ScopeVersionConflict:
        raise HTTPException(status_code=409, detail="scope_version_conflict") from None
    except ScopeUpdateRejected:
        raise HTTPException(status_code=400, detail="scope_update_rejected") from None
    except (ScopeValidationError, InvalidBoundary) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


class ThreadShareRequest(BaseModel):
    """Grant or revoke read access for one username (S32b)."""

    username: str


def _resolve_grantee(username: str):
    """Resolve an active user by exact username, or None."""
    from django.contrib.auth import get_user_model

    name = str(username or "").strip()
    if not name:
        return None
    return get_user_model().objects.filter(username=name, is_active=True).first()


@app.post("/threads/{thread_id}/share")
async def share_thread(thread_id: str, request: ThreadShareRequest) -> dict[str, Any]:
    """Grant read access on an owned thread (flag-gated, audited).

    An unknown grantee and a disabled feature are both reported without
    disclosing whether the thread exists for other principals.
    """
    from aichat.services.threads import InvalidBoundary

    repository = _repository(_principal())

    def perform() -> dict[str, Any]:
        """Perform."""
        grantee = _resolve_grantee(request.username)
        if grantee is None:
            raise HTTPException(status_code=400, detail="Unknown grantee")
        grant = repository.share(thread_id, grantee_id=grantee.pk)
        return {
            "thread_id": thread_id,
            "grantee": grantee.username,
            "access": grant.access,
            "granted": True,
        }

    try:
        return await sync_to_async(perform, thread_sensitive=True)()
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except InvalidBoundary as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/threads/{thread_id}/revoke-share")
async def revoke_thread_share(thread_id: str, request: ThreadShareRequest) -> dict[str, Any]:
    """Revoke read access on an owned thread; grant rows are kept as audit."""
    from aichat.services.threads import InvalidBoundary

    repository = _repository(_principal())

    def perform() -> dict[str, Any]:
        """Perform."""
        grantee = _resolve_grantee(request.username)
        if grantee is None:
            raise HTTPException(status_code=400, detail="Unknown grantee")
        revoked = repository.revoke_share(thread_id, grantee_id=grantee.pk)
        return {"thread_id": thread_id, "grantee": grantee.username, "revoked": revoked}

    try:
        return await sync_to_async(perform, thread_sensitive=True)()
    except (ThreadNotFound, ScopedThreadRejected):
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except InvalidBoundary as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


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


# S35: the in-memory pending-request dict and its register/get/resolve
# helpers were deleted. The rail was retired in WS7, the helpers had zero
# callers, and per-process approval state violates the "no in-process
# cross-request state" invariant (ai/README.md). The endpoints below remain
# only to answer legacy clients with the retirement notice.


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


@app.get("/rate-limit/stats")
async def rate_limit_stats() -> dict[str, Any]:
    """Get rate limiting statistics (per-process; counters live in the cache)."""
    from ai.core.middleware.rate_limit import get_windowed_rate_limiter

    limiter = get_rate_limiter()
    return {
        "rate_limiting": limiter.get_stats(),
        "windowed": get_windowed_rate_limiter().get_stats(),
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
async def switch_data_mode() -> dict[str, Any]:
    """Retired (S44): no runtime configuration writes.

    This endpoint used to rewrite the ai plane's ``.env`` on disk — runtime
    mutable config that survives nowhere sanely under container revisions.
    ``USE_DEMO_DATASET`` is deploy-time-only now: set it on the container
    app revision (or the local launch env) and restart.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "Retired: runtime config writes are not supported. Set "
            "USE_DEMO_DATASET in the deployment environment and restart."
        ),
    )


# S15 (WP-B5): the redaction authority moved to ai.core.config so the
# evaluation_dossier command shares the EXACT same masking; behavior here
# is byte-identical.
from ai.core.config import redact_config as _redact_config  # noqa: E402


@app.get("/config/effective")
async def effective_config() -> dict[str, Any]:
    """The ai plane's effective configuration, redacted (S44).

    Staff-gated. Answers "what is this revision actually running with"
    without re-deriving env -> settings mappings by hand. Secrets never
    appear: SecretStr fields and any name containing KEY/TOKEN/SECRET/
    PASSWORD/CREDENTIAL are masked.
    """
    if not _principal().is_staff:
        raise HTTPException(status_code=403, detail="Staff access required")

    from ai.core.config import get_settings
    from aimms_flags import REGISTRY

    settings = get_settings()
    dumped = settings.model_dump(mode="json")
    return {
        "settings": {key: _redact_config(value, key) for key, value in dumped.items()},
        "registry": [
            {
                "env_name": entry.env_name,
                "planes": entry.planes,
                "kind": entry.kind,
                "default": entry.default,
            }
            for entry in REGISTRY
        ],
    }


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
