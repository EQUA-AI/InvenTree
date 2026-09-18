"""Opt-in Python hostname guard; not a firewall or a raw-socket sandbox.

libpq, gRPC, direct IP sockets and subprocesses remain outside this boundary.
Denied destinations never enter logs or exception messages.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import logging
import os
import re
import socket
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger("inventree")
DEFAULT_ALLOW = (
    "aimms-foundry.openai.azure.com",
    "aimms-foundry.services.ai.azure.com",
    "*.search.windows.net",
    "*.cognitiveservices.azure.com",
    "epconchat-pg-dev.postgres.database.azure.com",
    "sts.googleapis.com",
    "iamcredentials.googleapis.com",
    "*-aiplatform.googleapis.com",
)
_LOCK = threading.Lock()
_INSTALLED = None


class EgressDenied(PermissionError):
    """A content-free refusal at the configured network boundary."""


def _hostname(value):
    if isinstance(value, bytes):
        value = value.decode("ascii")
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Invalid egress host")
    host = value.rstrip(".").lower().encode("idna").decode("ascii")
    if not re.fullmatch(r"[a-z0-9.:-]+", host):
        raise ValueError("Invalid egress host")
    return host


@dataclass(frozen=True)
class HostPolicy:
    """Immutable policy shared by the resolver and connection wrappers."""

    mode: str
    patterns: tuple[str, ...]

    def permits(self, host):
        try:
            host = _hostname(host)
        except (ValueError, UnicodeError):
            return False
        # Never include ACA's unsupported IMDS endpoint through a wildcard.
        if host == "169.254.169.254":
            return False
        for pattern in self.patterns:
            if "*" not in pattern:
                if host == pattern:
                    return True
            else:
                first, suffix = pattern.split(".", 1)
                label, _, tail = host.partition(".")
                if tail == suffix and fnmatch.fnmatchcase(label, first):
                    return True
        return False

    def check(self, host):
        if self.mode == "off" or self.permits(host):
            return
        logger.warning("egress.blocked mode=%s", self.mode)
        if self.mode == "enforce":
            raise EgressDenied("Egress destination is not permitted")


def policy_from_environment():
    """Validate configuration without resolving DNS or contacting a provider."""
    mode = os.getenv("AIMMS_EGRESS_MODE", "off").strip().lower()
    if mode not in {"off", "audit", "enforce"}:
        raise ValueError("Invalid AIMMS_EGRESS_MODE")
    patterns = []
    raw = os.getenv("AIMMS_EGRESS_ALLOW")
    values = DEFAULT_ALLOW if raw is None else raw.split(",")
    for value in values:
        value = value.strip().lower().rstrip(".")
        if not value:
            continue
        if "*" in value:
            if not re.fullmatch(r"\*(?:-[a-z0-9-]+)?\.[a-z0-9-]+(?:\.[a-z0-9-]+)+", value):
                raise ValueError("Invalid egress allow-list pattern")
        else:
            value = _hostname(value)
        patterns.append(value)
    identity = os.getenv("IDENTITY_ENDPOINT", "")
    if identity:
        endpoint = urlsplit(identity)
        host = endpoint.hostname
        if endpoint.scheme != "http" or not host or endpoint.username or endpoint.password:
            raise ValueError("Invalid identity endpoint")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("Identity endpoint must use a local address") from None
        if not (address.is_private or address.is_loopback) or address.is_unspecified:
            raise ValueError("Identity endpoint must use a local address")
        patterns.append(_hostname(host))
    return HostPolicy(mode, tuple(dict.fromkeys(patterns)))


def install_guard():
    """Install once at application startup; changes require a process restart."""
    global _INSTALLED
    policy = policy_from_environment()
    with _LOCK:
        if _INSTALLED is not None:
            if policy != _INSTALLED:
                raise ValueError("Egress policy changed; restart required")
            return
        if policy.mode == "off":
            return
        resolver = socket.getaddrinfo
        connector = socket.create_connection

        def guarded_resolver(host, *args, **kwargs):
            # None is the local bind-address lookup, never a remote hostname.
            if host is not None:
                policy.check(host)
            return resolver(host, *args, **kwargs)

        def guarded_connector(address, *args, **kwargs):
            policy.check(address[0])
            return connector(address, *args, **kwargs)

        socket.getaddrinfo = guarded_resolver
        socket.create_connection = guarded_connector
        _INSTALLED = policy


def disable_optional_memory_telemetry():
    """Set the vendor opt-out before a future admitted optional-engine import."""
    os.environ["MEM0_TELEMETRY"] = "false"
    os.environ.setdefault("MEM0_DIR", "/tmp/mem0")
