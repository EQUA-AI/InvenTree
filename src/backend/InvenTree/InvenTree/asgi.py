"""
ASGI config for InvenTree project.

It exposes the ASGI callable as a module-level variable named ``application``.

It mounts the AIMMS FastAPI app under /api/ai/ to serve AI features alongside Django.
"""

import os
from django.core.asgi import get_asgi_application
from starlette.applications import Starlette
from starlette.routing import Mount
from ai.core.app import app as ai_app

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'InvenTree.settings')

django_app = get_asgi_application()

# Mount the FastAPI app under /api/ai
# Requests to /api/ai/chat/stream will be routed to ai_app as /chat/stream
application = Starlette(routes=[
    Mount("/api/ai", app=ai_app),
    Mount("/", app=django_app)
])
