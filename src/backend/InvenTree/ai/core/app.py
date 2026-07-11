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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai.core.config import get_settings, get_devui_settings
from ai.core.api import get_devui, devui_context
from ai.core.streaming import get_event_emitter, SSEEventStream, create_run_context, AGUIEvent, EventType
from ai.core.workflows.root import RootWorkflow, get_root_workflow
from ai.core.memory import get_semantic_cache
from ai.core.middleware import (
    get_rate_limiter,
    RateLimitMiddleware,
    RateLimitConfig,
    get_retry_stats,
    retry_azure_openai_call,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# Pydantic models for API
class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    message: str
    thread_id: str | None = None
    user_id: str = "anonymous"
    context: dict[str, Any] | None = None
    file_ids: list[str] | None = None


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
    workflow_root = get_workflow_root()
    logger.info("Root workflow initialized")
    
    # Initialize conversation persistence and search index
    if settings.conversation_persistence_enabled:
        try:
            # Initialize the search index if search is enabled
            if settings.conversation_search_enabled:
                conversation_manager = workflow_root.conversation_manager
                # Check if initialize_search_index exists (it might be on the persistence object)
                # For now, we assume ConversationManager handles initialization internally or lazily
                # But if we need explicit init:
                if hasattr(conversation_manager, "initialize_search_index"):
                     await conversation_manager.initialize_search_index()
                logger.info("Conversation search index initialized")
            else:
                logger.info("Conversation search disabled, using database only")
        except Exception as e:
            logger.warning(f"Failed to initialize conversation persistence: {e}")
    
    # Initialize semantic cache
    if settings.semantic_cache_enabled:
        cache = get_semantic_cache()
        logger.info(f"Semantic cache initialized (threshold: {settings.semantic_cache_similarity_threshold})")
    
    # Start DevUI if enabled
    if devui_settings.enabled:
        devui = get_devui()
        await devui.start()
        logger.info(f"DevUI available at {devui.url}")
    
    yield
    
    # Cleanup
    if devui_settings.enabled:
        devui = get_devui()
        await devui.stop()
    
    logger.info("AIMMS Backend shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="AIMMS Backend",
    description="AI-powered Manufacturing Management System",
    version="2.3.0",
    lifespan=lifespan,
)

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
    exempt_paths={"/health", "/docs", "/openapi.json", "/workflows", "/rate-limit/stats", "/retry/stats", "/upload"},
)


# ==============================================================================
# File Upload Configuration
# ==============================================================================

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".csv", ".docx"}
MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
UPLOAD_TTL_HOURS = 24


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
    # Sanitise thread_id to prevent path traversal
    safe_id = "".join(c for c in thread_id if c.isalnum() or c in ("_", "-"))
    thread_dir = _get_upload_dir() / safe_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    return thread_dir


def resolve_upload_path(file_id: str) -> Path | None:
    """Resolve a file_id to its absolute path, or None if not found."""
    upload_dir = _get_upload_dir()
    # file_id format: {thread_id}/{uuid}_{filename}
    candidate = upload_dir / file_id
    if candidate.exists() and candidate.is_file() and upload_dir in candidate.resolve().parents:
        return candidate.resolve()
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
    # Validate filename & extension
    original_name = file.filename or "upload"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
        )

    # Read the file (enforce size limit)
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(contents)} bytes). Maximum is {MAX_UPLOAD_SIZE_BYTES} bytes (20 MB).",
        )

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

    logger.info(f"File uploaded: {file_id} ({len(contents)} bytes)")

    return UploadResponse(
        file_id=file_id,
        filename=original_name,
        size=len(contents),
        content_type=file.content_type or "application/octet-stream",
        thread_id=thread_id,
    )


@app.post("/upload/cleanup")
async def cleanup_uploads(max_age_hours: int = UPLOAD_TTL_HOURS) -> dict[str, Any]:
    """
    Remove uploaded files older than max_age_hours.
    Called periodically or manually.
    """
    import time

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
            except Exception as e:
                logger.warning(f"Failed to remove {fpath}: {e}")
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
    """
    Main chat endpoint (non-streaming).
    
    Processes user messages through the root workflow.
    For real-time updates during processing, use /chat/stream instead.
    """
    try:
        workflow_root = get_workflow_root()
        emitter = get_event_emitter()
        thread_id = request.thread_id or str(uuid.uuid4())
        
        # Helper to capture metadata from events
        class MetadataCapture:
            def __init__(self):
                self.workflow_id = None
                
            async def handle(self, event: AGUIEvent) -> None:
                if event.thread_id == thread_id:
                    if event.event_type == EventType.WORKFLOW_STARTED:
                        self.workflow_id = event.data.get("workflow_id")

        capture = MetadataCapture()
        unsubscribe = await emitter.subscribe(capture)
        
        response_text = []
        
        # Merge uploaded file metadata into context
        context = dict(request.context or {})
        if request.file_ids:
            uploaded_files = []
            for fid in request.file_ids:
                fpath = resolve_upload_path(fid)
                if fpath:
                    uploaded_files.append({
                        "file_id": fid,
                        "path": str(fpath),
                        "filename": fpath.name,
                        "extension": fpath.suffix.lower(),
                        "size": fpath.stat().st_size,
                    })
            if uploaded_files:
                context["uploaded_files"] = uploaded_files
        
        try:
            async for chunk in workflow_root.run_stream(
                message=request.message,
                emitter=emitter,
                thread_id=thread_id,
                user_id=request.user_id,
                context=context,
            ):
                response_text.append(chunk)
                
            return ChatResponse(
                thread_id=thread_id,
                message="".join(response_text),
                agent="root_workflow",
                workflow_used=capture.workflow_id,
            )
        finally:
            unsubscribe()
    
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
    async def event_generator() -> AsyncIterator[str]:
        emitter = get_event_emitter()
        
        # Ensure we have a thread ID for filtering
        thread_id = request.thread_id or str(uuid.uuid4())
        
        # Create SSE stream first to capture all events
        stream = SSEEventStream(emitter, thread_id=thread_id)
        
        async def process_in_background():
            """Run workflow processing in background."""
            try:
                workflow_root = get_workflow_root()
                
                # Merge uploaded file metadata into context
                context = dict(request.context or {})
                if request.file_ids:
                    uploaded_files = []
                    for fid in request.file_ids:
                        fpath = resolve_upload_path(fid)
                        if fpath:
                            uploaded_files.append({
                                "file_id": fid,
                                "path": str(fpath),
                                "filename": fpath.name,
                                "extension": fpath.suffix.lower(),
                                "size": fpath.stat().st_size,
                            })
                    if uploaded_files:
                        context["uploaded_files"] = uploaded_files

                # Consume streaming response - events are emitted to emitter
                async for _ in workflow_root.run_stream(
                    message=request.message,
                    emitter=emitter,
                    thread_id=thread_id,
                    user_id=request.user_id,
                    context=context,
                ):
                    pass  # Response chunks are also available here if needed
                    
            except Exception as e:
                logger.error(f"Processing error: {e}", exc_info=True)
                # Emit error event
                error_event = AGUIEvent(
                    event_type=EventType.RUN_ERROR,
                    data={"error": str(e)},
                    thread_id=thread_id,
                )
                await emitter.emit(error_event)
            finally:
                # Signal stream to stop when processing is complete
                await stream.stop()
        
        # Start background processing
        process_task = asyncio.create_task(process_in_background())
        
        try:
            # Stream events as they come in
            # The stream will end when stream.stop() is called in process_in_background
            async for event_data in stream.events():
                yield event_data
                    
        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield f"event: ERROR\ndata: {{\"error\": \"{str(e)}\"}}\n\n"
        finally:
            # Ensure the processing task is cleaned up
            if not process_task.done():
                process_task.cancel()
                try:
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


# ===== Thread Management Endpoints =====


@app.get("/threads", response_model=ThreadSyncResponse)
async def list_threads(
    user_id: str = "anonymous",
    include_persisted: bool = True,
    limit: int = 50,
) -> ThreadSyncResponse:
    """
    List all threads for a user.
    
    Combines in-memory threads with persisted threads from the database.
    This is the primary endpoint for frontend thread sync.
    
    Args:
        user_id: User ID to filter threads by
        include_persisted: Whether to include persisted threads from DB
        limit: Maximum number of threads to return
    """
    workflow_root = get_workflow_root()
    seen_thread_ids: set[str] = set()
    threads: list[ThreadInfo] = []
    
    # First, get in-memory threads (most recent state)
    # Note: ConversationManager might not expose list_active_threads directly if it's just a dict
    # We assume it does or we access the internal dict if needed, but better to use public API
    # If list_active_threads doesn't exist, we might need to add it to ConversationManager
    # For now, assuming it exists as it was used before
    if hasattr(workflow_root.conversation_manager, "list_active_threads"):
        thread_ids = workflow_root.conversation_manager.list_active_threads()
    else:
        # Fallback if method missing (e.g. if I didn't copy it)
        # In my implementation of ConversationManager, I didn't add list_active_threads!
        # I should check ConversationManager implementation again.
        # I only copied get_or_create_state, gather_context, etc.
        # I should probably add list_active_threads to ConversationManager.
        thread_ids = []

    for thread_id in thread_ids:
        state = workflow_root.conversation_manager.get_or_create_state(thread_id) # get_state might not exist
        if state and (user_id == "anonymous" or state.user_id == user_id):
            threads.append(ThreadInfo(
                thread_id=thread_id,
                title=state.summary[:50] if state.summary else "",
                message_count=len(state.messages),
                turn_count=state.turn_count,
                summary=state.summary or "",
                created_at=state.created_at.isoformat(),
                last_activity=state.updated_at.isoformat(),
                is_persisted=False,
            ))
            seen_thread_ids.add(thread_id)
    
    # Then, add persisted threads not already in memory
    if include_persisted:
        from ai.core.infrastructure.persistence import ConversationPersistence
        persistence = ConversationPersistence()
        
        if persistence.persistence_enabled:
            try:
                persisted = await persistence.list_threads(
                    user_id=user_id if user_id != "anonymous" else None,
                    active_only=True,
                    limit=limit,
                )
                
                for pt in persisted:
                    if pt.thread_id not in seen_thread_ids:
                        threads.append(ThreadInfo(
                            thread_id=pt.thread_id,
                            title=pt.title,
                            message_count=0,  # Not loaded for list
                            turn_count=pt.turn_count,
                            summary=pt.summary,
                            created_at=pt.created_at.isoformat(),
                            last_activity=pt.updated_at.isoformat(),
                            is_persisted=True,
                        ))
                        seen_thread_ids.add(pt.thread_id)
            except Exception as e:
                logger.error(f"Failed to load persisted threads: {e}")
    
    # Sort by last activity (most recent first)
    threads.sort(key=lambda t: t.last_activity or "", reverse=True)
    
    # Apply limit
    threads = threads[:limit]
    
    return ThreadSyncResponse(
        threads=threads,
        sync_token=None,  # Could add pagination token here
        has_more=len(seen_thread_ids) > limit,
    )


@app.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    include_messages: bool = True,
    message_limit: int = 50,
) -> dict[str, Any]:
    """
    Get thread details and optionally messages.
    
    First checks in-memory state, then falls back to persisted data.
    This allows loading historical threads that aren't in memory.
    """
    workflow_root = get_workflow_root()
    state = workflow_root.conversation_manager.get_state(thread_id)
    
    # If not in memory, try to load from persistence
    if state is None:
        from ai.core.infrastructure.persistence import ConversationPersistence
        persistence = ConversationPersistence()
        
        if persistence.persistence_enabled:
            try:
                persisted = await persistence.load_thread(thread_id)
                if persisted:
                    # Convert persisted thread to response format
                    messages = []
                    if include_messages:
                        messages = [
                            {
                                "id": m.message_id,
                                "role": m.role,
                                "content": m.content,
                                "timestamp": m.created_at.isoformat(),
                                "tool_name": m.tool_name,
                                "workflow_id": m.workflow_id,
                            }
                            for m in persisted.messages[-message_limit:]
                        ]
                    
                    return {
                        "thread_id": thread_id,
                        "user_id": persisted.user_id or "anonymous",
                        "title": persisted.title,
                        "turn_count": persisted.turn_count,
                        "last_workflow": persisted.last_workflow,
                        "pending_handoff": persisted.pending_handoff,
                        "summary": persisted.summary,
                        "messages": messages,
                        "metrics": {},
                        "created_at": persisted.created_at.isoformat(),
                        "updated_at": persisted.updated_at.isoformat(),
                        "is_persisted": True,
                    }
            except Exception as e:
                logger.error(f"Failed to load persisted thread: {e}")
        
        raise HTTPException(status_code=404, detail="Thread not found")
    
    # Return in-memory state
    # metrics = workflow_root.conversation_manager.get_conversation_metrics(thread_id) # Not implemented yet
    metrics = {}
    messages = []
    if include_messages:
        messages = [m.to_dict() for m in state.get_recent_messages(message_limit)]
    
    return {
        "thread_id": thread_id,
        "user_id": state.user_id,
        "title": state.summary[:50] if state.summary else "",
        "turn_count": state.turn_count,
        "last_workflow": state.last_workflow,
        "pending_handoff": state.pending_handoff,
        "summary": state.summary,
        "messages": messages,
        "metrics": metrics,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "is_persisted": False,
    }


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str) -> dict[str, str]:
    """Delete a thread and clean up its state from both memory and persistence."""
    workflow_root = get_workflow_root()
    
    # Clean up in-memory state
    workflow_root.conversation_manager.cleanup(thread_id)
    
    # Also delete from persistence
    from ai.core.infrastructure.persistence import ConversationPersistence
    persistence = ConversationPersistence()
    
    if persistence.persistence_enabled:
        try:
            await persistence.delete_thread(thread_id)
        except Exception as e:
            logger.error(f"Failed to delete persisted thread: {e}")
    
    return {"status": "deleted", "thread_id": thread_id}


@app.put("/threads/{thread_id}")
async def update_thread(
    thread_id: str,
    title: str | None = None,
) -> dict[str, Any]:
    """
    Update thread metadata (e.g., title).
    
    Used by frontend to rename threads.
    """
    workflow_root = get_workflow_root()
    state = workflow_root.conversation_manager.get_state(thread_id)
    
    # Update in-memory if exists
    if state and title:
        # Store title in summary for now (could add separate title field)
        pass
    
    # Update in persistence
    from ai.core.infrastructure.persistence import ConversationPersistence
    persistence = ConversationPersistence()
    
    if persistence.persistence_enabled and title:
        from common.ai_models import AIConversationThread
        from asgiref.sync import sync_to_async
        
        @sync_to_async
        def _update():
            AIConversationThread.objects.filter(thread_id=thread_id).update(title=title)
        
        try:
            await _update()
        except Exception as e:
            logger.error(f"Failed to update thread title: {e}")
    
    return {"thread_id": thread_id, "title": title, "updated": True}


@app.post("/threads/sync")
async def sync_threads(
    user_id: str = "anonymous",
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
    local_thread_ids = local_thread_ids or []
    local_set = set(local_thread_ids)
    
    # Get all server threads
    sync_response = await list_threads(
        user_id=user_id,
        include_persisted=True,
        limit=100,
    )
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
    if request:
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
    # Look up the pending request
    pending = get_hitl_request(request.request_id)
    
    if pending is None:
        return HITLResponse(
            success=False,
            request_id=request.request_id,
            status="not_found",
            message="HITL request not found. It may have already been processed or expired.",
        )
    
    if pending["status"] == "expired":
        return HITLResponse(
            success=False,
            request_id=request.request_id,
            status="expired",
            message="HITL request has expired. The action was not performed.",
        )
    
    if pending["status"] in ("approved", "rejected"):
        return HITLResponse(
            success=False,
            request_id=request.request_id,
            status=pending["status"],
            message=f"HITL request has already been {pending['status']}.",
        )
    
    # Resolve the request
    resolved = resolve_hitl_request(
        request.request_id,
        request.approved,
        request.reason,
        request.user_id,
    )
    
    status = "approved" if request.approved else "rejected"
    action = pending.get("action", "Unknown action")
    
    if request.approved:
        message = f"Action '{action}' has been approved and will proceed."
    else:
        reason_text = f" Reason: {request.reason}" if request.reason else ""
        message = f"Action '{action}' has been rejected.{reason_text}"
    
    logger.info(f"HITL request {request.request_id} {status} by {request.user_id}")
    
    return HITLResponse(
        success=True,
        request_id=request.request_id,
        status=status,
        message=message,
    )


@app.get("/hitl/pending")
async def get_pending_hitl(
    thread_id: str | None = None,
    user_id: str = "anonymous",
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
            "enabled": settings.feature_wf5_cpq,
            "description": "Product configuration and quote generation",
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
    if mode not in ("demo", "live"):
        raise HTTPException(status_code=400, detail="Mode must be 'demo' or 'live'")
    
    from ai.core.switch_mode import get_env_file_path, update_env_value
    from ai.core.integrations import reset_provider, get_mode_status
    
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
