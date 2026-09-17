"""ASGI config for InvenTree project.

It exposes the ASGI callable as a module-level variable named ``application``.

It mounts the AIMMS FastAPI app under /api/ai/ to serve AI features alongside Django.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from django.core.asgi import get_asgi_application

from starlette.applications import Starlette
from starlette.routing import Mount, Route

from InvenTree.restore_hold import RestoreHoldASGI, restore_hold_enabled

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'InvenTree.settings')

django_app = get_asgi_application()

# Import the AI application only after Django is configured. The wrappers are
# outside the FastAPI mount so every present and future AI route receives the
# same immutable principal before route or rate-limit code runs, and every
# http request on the mount gets its own thread-sensitive executor whose
# Django connection is released once the response has been sent (M2 PR 6;
# the auth middleware sits inside so its ORM hop is covered too).
from ai.core.auth import AIBoundaryAuthMiddleware
from ai.core.db_hygiene import ConnectionReleaseMiddleware
from ai.core.runtime import Failure, liveness, readiness, runtime


async def unavailable_ai(scope, receive, send):
    """No AI application is mounted if its configuration cannot even load."""
    from starlette.responses import JSONResponse

    if scope['type'] == 'websocket':
        await send({'type': 'websocket.close', 'code': 1013})
    elif scope['type'] == 'http':
        await JSONResponse({'detail': 'AI_RUNTIME_UNAVAILABLE'}, status_code=503)(
            scope, receive, send
        )


try:
    from ai.core.app import app as ai_app

    authenticated_ai_app = ConnectionReleaseMiddleware(AIBoundaryAuthMiddleware(ai_app))
except Exception:
    # Invalid settings can fail before lifespan entry, including construction of
    # the auth policy. Do not mount any AI routes with a fabricated auth policy.
    ai_app = None
    authenticated_ai_app = unavailable_ai
    runtime.state = 'permanently_failed'
    runtime.failure = Failure(False, 'configuration')
    logging.getLogger('inventree').error(
        'AIMMS configuration could not load; AI mount unavailable'
    )


@asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    """Run the mounted AIMMS application's startup and shutdown handlers.

    AI features are optional: a missing or invalid AI configuration (e.g. no
    Azure OpenAI environment in CI or a bare deployment) must degrade to a
    working InvenTree server with the AI mount unavailable, not kill the
    whole ASGI application at startup.
    """
    if ai_app is None or restore_hold_enabled():
        yield
        return

    context = ai_app.router.lifespan_context(ai_app)
    started = False
    try:
        await context.__aenter__()
        started = True
    except Exception:
        runtime.state = 'permanently_failed'
        runtime.failure = Failure(False, 'initialization')
        logging.getLogger('inventree').error('AIMMS lifecycle failed; AI unavailable')

    try:
        yield
    finally:
        if started:
            await context.__aexit__(None, None, None)


# Mount the FastAPI app under /api/ai
# Requests to /api/ai/chat/stream will be routed to ai_app as /chat/stream
application = Starlette(
    routes=[
        Route('/health/live', liveness),
        Route('/health/ai-ready', readiness),
        Mount('/api/ai', app=authenticated_ai_app),
        Mount('/', app=django_app),
    ],
    lifespan=lifespan,
)
application = RestoreHoldASGI(application)
