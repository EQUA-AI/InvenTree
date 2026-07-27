"""Signed webhook ingestion.

The generic adapter for platforms that push. A gateway cannot be trusted just
because it knows the URL, so an accepted delivery must satisfy all of:

* an HMAC-SHA256 signature over the exact raw body, using the source's shared
  secret and compared in constant time;
* a timestamp inside the replay window, so a captured request cannot be resent
  later;
* a delivery id not seen before, so a request cannot be resent *now*;
* a body within the size limit, checked before any parsing.

The secret is resolved from deployment configuration through the source's
``secret_ref``. It is never stored on the source row and never returned by an API.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from django.conf import settings
from django.core.cache import cache

#: Reject a delivery whose timestamp is further than this from the server clock.
REPLAY_WINDOW_SECONDS = 300

#: Remember delivery ids for longer than the replay window so a resend inside it
#: is still caught after the entry would otherwise expire.
DELIVERY_CACHE_SECONDS = REPLAY_WINDOW_SECONDS * 2

#: Bound the body before parsing; a 10 MB JSON document is not a sensor update.
MAX_BODY_BYTES = 1_048_576

SIGNATURE_HEADER = 'HTTP_X_AIMMS_SIGNATURE'
TIMESTAMP_HEADER = 'HTTP_X_AIMMS_TIMESTAMP'
DELIVERY_HEADER = 'HTTP_X_AIMMS_DELIVERY'


class WebhookAuthError(Exception):
    """The delivery failed authentication, replay or size checks."""

    def __init__(self, code: str, message: str):
        """Carry a stable code alongside the operator-facing message."""
        super().__init__(message)
        self.code = code


def _secret_for(source) -> str:
    """Resolve the shared secret for a source from deployment configuration.

    Secrets live in ``AIMMS_HEALTH_WEBHOOK_SECRETS`` (a mapping keyed by
    ``secret_ref``), which a deployment populates from its own secret store. A
    source with no resolvable secret cannot be used - webhook ingestion fails
    closed rather than accepting unsigned data.
    """
    secrets = getattr(settings, 'AIMMS_HEALTH_WEBHOOK_SECRETS', {}) or {}
    secret = secrets.get(source.secret_ref) if source.secret_ref else None
    if not secret:
        raise WebhookAuthError(
            'SECRET_UNRESOLVED',
            'No shared secret is configured for this health source.',
        )
    return secret


def expected_signature(secret: str, timestamp: str, body: bytes) -> str:
    """Return the signature a caller must present for this body."""
    message = timestamp.encode() + b'.' + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_delivery(source, meta, body: bytes, *, now=None) -> str:
    """Authenticate one webhook delivery, returning its delivery id.

    Raises :class:`WebhookAuthError` with a stable code for every rejection so an
    operator can tell a misconfigured gateway from a replay attempt, without the
    response revealing which secret or which check failed in detail.
    """
    if len(body) > MAX_BODY_BYTES:
        raise WebhookAuthError('BODY_TOO_LARGE', 'Payload exceeds the size limit.')

    signature = meta.get(SIGNATURE_HEADER, '')
    timestamp = meta.get(TIMESTAMP_HEADER, '')
    delivery_id = meta.get(DELIVERY_HEADER, '')

    if not signature or not timestamp or not delivery_id:
        raise WebhookAuthError(
            'SIGNATURE_MISSING', 'Signature, timestamp and delivery id are required.'
        )

    try:
        sent_at = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise WebhookAuthError('TIMESTAMP_INVALID', 'Malformed timestamp.') from exc

    now = now if now is not None else time.time()
    if abs(now - sent_at) > REPLAY_WINDOW_SECONDS:
        raise WebhookAuthError(
            'TIMESTAMP_OUT_OF_WINDOW',
            'Delivery timestamp is outside the accepted window.',
        )

    secret = _secret_for(source)
    if not hmac.compare_digest(expected_signature(secret, timestamp, body), signature):
        raise WebhookAuthError('SIGNATURE_INVALID', 'Signature verification failed.')

    cache_key = f'machine_health:webhook:{source.pk}:{delivery_id}'
    if not cache.add(cache_key, 1, DELIVERY_CACHE_SECONDS):
        raise WebhookAuthError(
            'REPLAYED_DELIVERY', 'This delivery was already accepted.'
        )

    return delivery_id
