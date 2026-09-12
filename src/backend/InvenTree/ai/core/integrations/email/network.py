"""Validated and pinned TLS endpoints for administrator-configured mail servers."""

import ipaddress
import os
import re
import socket

from .contracts import MailboxError


def endpoint(host, port):
    """Resolve once, check every address, then connect using a validated IP."""
    if (
        not isinstance(host, str)
        or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", host)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise MailboxError("invalid_endpoint")
    private = [
        ipaddress.ip_network(value.strip())
        for value in os.environ.get("INVENTREE_AGENT_EMAIL_PRIVATE_NETWORKS", "").split(",")
        if value.strip()
    ]
    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise MailboxError("endpoint_unavailable") from None
    if not resolved:
        raise MailboxError("endpoint_unavailable")
    for _, _, _, _, sockaddr in resolved:
        address = ipaddress.ip_address(sockaddr[0])
        if (
            address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or (not address.is_global and not any(address in network for network in private))
        ):
            raise MailboxError("endpoint_denied")
    return resolved[0][4][0]


def connect(host, port, timeout=20):
    """Prevent DNS rebinding between validation and connection."""
    address = endpoint(host, port)
    return socket.create_connection((address, port), timeout=timeout)
