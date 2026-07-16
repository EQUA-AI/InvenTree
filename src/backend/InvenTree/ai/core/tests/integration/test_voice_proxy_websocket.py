"""WS2-T7: probe the deployment reverse proxy for WebSocket upgrades.

Opt-in: skips unless ``AIMMS_AZURE_INTEGRATION=1`` *and*
``AIMMS_PROXY_PROBE_URL`` names the externally reachable AIMMS base URL
(e.g. ``https://aimms.example.com``). The probe sends an HTTP/1.1 Upgrade
request with stdlib sockets and asserts the proxy answers with a
well-formed HTTP status instead of dropping or refusing the connection.
It authenticates nothing and sends no credentials.
"""

from __future__ import annotations

import os
import socket
import ssl
from urllib.parse import urlsplit

import pytest

INTEGRATION_ENABLED = os.environ.get("AIMMS_AZURE_INTEGRATION") == "1"
PROBE_URL = os.environ.get("AIMMS_PROXY_PROBE_URL", "")

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_ENABLED and PROBE_URL),
    reason=(
        "target-host probe; set AIMMS_AZURE_INTEGRATION=1 and "
        "AIMMS_PROXY_PROBE_URL on the approved host"
    ),
)

_UPGRADE_TEMPLATE = (
    "GET {path} HTTP/1.1\r\n"
    "Host: {host}\r\n"
    "Connection: Upgrade\r\n"
    "Upgrade: websocket\r\n"
    "Sec-WebSocket-Version: 13\r\n"
    # Canonical RFC 6455 §1.3 example handshake nonce, not a credential.
    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"  # gitleaks:allow
    "\r\n"
)


def _probe(path: str) -> str:
    parts = urlsplit(PROBE_URL)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    raw = socket.create_connection((host, port), timeout=15)
    try:
        if parts.scheme == "https":
            context = ssl.create_default_context()
            raw = context.wrap_socket(raw, server_hostname=host)
        raw.sendall(_UPGRADE_TEMPLATE.format(path=path, host=host).encode("ascii"))
        head = raw.recv(4096).decode("latin-1", errors="replace")
    finally:
        raw.close()
    return head


def test_proxy_answers_websocket_upgrade_requests():
    head = _probe("/api/ai/voice/sessions/probe/signal")
    status_line = head.split("\r\n", 1)[0]
    assert status_line.startswith(("HTTP/1.1 ", "HTTP/1.0 ")), (
        f"proxy did not speak HTTP to an Upgrade request: {status_line!r}"
    )
    code = int(status_line.split(" ", 2)[1])
    # 101 would mean an unauthenticated upgrade succeeded — that is a failure.
    assert code != 101, "proxy upgraded an unauthenticated WebSocket request"
    # Any 4xx (auth/policy rejection) proves the path forwards upgrades to
    # the application; 502/503/504 or a dropped connection would mean the
    # proxy blocks long-lived upgrade traffic.
    assert 400 <= code < 500, f"unexpected proxy status {code} for upgrade probe"
