"""Bind live provider state to one server process; never adopt it elsewhere."""

from __future__ import annotations

import hashlib
import os
import socket
import uuid

from django.db import transaction

# PID participates because gunicorn may preload modules before forking workers.
_BOOT = uuid.uuid4().hex


def process_owner() -> str:
    """Opaque private process identity, stable for its serving lifetime."""
    value = f'{socket.gethostname()}:{os.getpid()}:{_BOOT}'
    return hashlib.sha256(value.encode()).hexdigest()


def owns_transport(session) -> bool:
    """Blank legacy/unconnected rows carry no process binding."""
    return not session.transport_owner or session.transport_owner == process_owner()


def claim_transport(session) -> bool:
    """Atomically claim before SDP/relay creates any provider side effect."""
    from voice.models import VoiceSession

    with transaction.atomic():
        current = VoiceSession.objects.select_for_update().get(pk=session.pk)
        if current.is_terminal or not owns_transport(current):
            return False
        if not current.transport_owner:
            current.transport_owner = process_owner()
            current.save(update_fields=['transport_owner'])
        session.transport_owner = current.transport_owner
    return True
