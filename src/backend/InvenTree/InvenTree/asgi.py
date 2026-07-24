"""ASGI config for InvenTree project.

It exposes the ASGI callable as a module-level variable named ``application``.

It mounts the AIMMS FastAPI app under /api/ai/ to serve AI features alongside Django.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from django.core.asgi import get_asgi_application

from starlette.applications import Starlette
from starlette.routing import Mount

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'InvenTree.settings')

django_app = get_asgi_application()

# Import the AI application only after Django is configured. The wrapper is
# outside the FastAPI mount so every present and future AI route receives the
# same immutable principal before route or rate-limit code runs.
from ai.core.app import app as ai_app
from ai.core.auth import AIBoundaryAuthMiddleware

authenticated_ai_app = AIBoundaryAuthMiddleware(ai_app)


@asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    """Run the mounted AIMMS application's startup and shutdown handlers.

    AI features are optional: a missing or invalid AI configuration (e.g. no
    Azure OpenAI environment in CI or a bare deployment) must degrade to a
    working InvenTree server with the AI mount unavailable, not kill the
    whole ASGI application at startup.
    """
    import logging

    context = ai_app.router.lifespan_context(ai_app)
    started = False
    try:
        await context.__aenter__()
        started = True
    except Exception:
        logging.getLogger('inventree').exception(
            'AIMMS startup failed - continuing without AI features'
        )

    try:
        yield
    finally:
        if started:
            await context.__aexit__(None, None, None)


# Mount the FastAPI app under /api/ai
# Requests to /api/ai/chat/stream will be routed to ai_app as /chat/stream
application = Starlette(
    routes=[Mount('/api/ai', app=authenticated_ai_app), Mount('/', app=django_app)],
    lifespan=lifespan,
)
