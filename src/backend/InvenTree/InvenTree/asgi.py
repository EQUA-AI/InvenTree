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
    """Run the mounted AIMMS application's startup and shutdown handlers."""
    async with ai_app.router.lifespan_context(ai_app):
        yield


# Mount the FastAPI app under /api/ai
# Requests to /api/ai/chat/stream will be routed to ai_app as /chat/stream
application = Starlette(
    routes=[Mount('/api/ai', app=authenticated_ai_app), Mount('/', app=django_app)],
    lifespan=lifespan,
)
