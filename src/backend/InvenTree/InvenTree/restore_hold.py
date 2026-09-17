"""Deployment-controlled serving hold for isolated restore/replay operations."""

import os


def restore_hold_enabled():
    """Treat any nonempty, non-false setting as held, including misspellings."""
    value = os.environ.get('INVENTREE_RESTORE_HOLD', '').strip().lower()
    return value not in {'', '0', 'false', 'off', 'no'}


class RestoreHoldASGI:
    """Block HTTP and new WebSockets outside every application mount."""

    def __init__(self, application):
        """Wrap the fully assembled application."""
        self.application = application

    async def __call__(self, scope, receive, send):
        """Never dispatch held requests to authentication, routing or storage."""
        if restore_hold_enabled():
            if scope['type'] == 'http':
                await send({
                    'type': 'http.response.start',
                    'status': 503,
                    'headers': [
                        (b'content-type', b'text/plain'),
                        (b'cache-control', b'no-store'),
                        (b'retry-after', b'300'),
                    ],
                })
                await send({
                    'type': 'http.response.body',
                    'body': b'Restore in progress.',
                })
                return
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1013})
                return
        await self.application(scope, receive, send)


class RestoreHoldWSGI:
    """Apply the same hold when the Django-only WSGI entrypoint is used."""

    def __init__(self, application):
        """Wrap the fully assembled application."""
        self.application = application

    def __call__(self, environ, start_response):
        """Return an uncached unavailable response while held."""
        if restore_hold_enabled():
            start_response(
                '503 Service Unavailable',
                [
                    ('Content-Type', 'text/plain'),
                    ('Cache-Control', 'no-store'),
                    ('Retry-After', '300'),
                ],
            )
            return [b'Restore in progress.']
        return self.application(environ, start_response)
