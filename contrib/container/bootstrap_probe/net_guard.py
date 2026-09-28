"""External-transport instrumentation for the whole-process bootstrap probe.

Installed at interpreter startup (``bootstrap_probe/sitecustomize.py``) and
from the probe settings shim (fallback). When ``DM_BOOTSTRAP_NETLOG`` is set,
every outbound socket connect in the process is appended to that JSONL log.
Connects to harness-local services (the disposable PostgreSQL, the dev redis)
are recorded as ``allowed``; everything else is recorded as ``blocked`` and
refused. A blocked ``connect`` raises
:class:`BlockedExternalConnection`; ``connect_ex`` returns ECONNREFUSED —
attempts are never silently allowed and never silently swallowed.

This is observe/block+record instrumentation of external transport only. It
never touches database behavior: database writes are prevented exclusively by
the server-side defense (read-only database role +
``default_transaction_read_only``), so any attempted write fails loudly there
instead of being masked by client-side monkeypatching.

When ``DM_BOOTSTRAP_NETLOG`` is unset this module is completely inert.
"""

from __future__ import annotations

import json
import os
import socket
import time

_installed = False


class BlockedExternalConnection(ConnectionRefusedError):  # noqa: N818 - public probe guard API name, kept verbatim for harness compatibility
    """Raised when the probe guard refuses an off-allowlist external connect."""


def _allowlist() -> set[str]:
    raw = os.environ.get('DM_BOOTSTRAP_NET_ALLOW', '')
    return {item.strip() for item in raw.split(',') if item.strip()}


def _record(entry: dict) -> None:
    path = os.environ.get('DM_BOOTSTRAP_NETLOG')
    if not path:
        return
    entry = dict(entry, ts=round(time.time(), 3), pid=os.getpid())
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, sort_keys=True) + '\n')


def _normalize(address) -> str:
    try:
        if isinstance(address, tuple) and len(address) >= 2:
            return f'{address[0]}:{address[1]}'
        if isinstance(address, (bytes, str)):
            return f'unix:{address!r}'
    except Exception:  # pragma: no cover - normalization must never crash
        pass
    return repr(address)


def _decide(address) -> tuple[str, bool, str]:
    key = _normalize(address)
    if key.startswith('unix:'):
        return key, True, 'unix-local-socket'
    allow = _allowlist()
    if key in allow:
        return key, True, 'allowlist'
    host = key.rsplit(':', 1)[0]
    if f'{host}:*' in allow:
        return key, True, 'allowlist-host-any-port'
    return key, False, 'not-on-allowlist'


def install() -> bool:
    """Wrap socket connect paths. Idempotent; no-op without the export env."""
    global _installed
    if _installed or not os.environ.get('DM_BOOTSTRAP_NETLOG'):
        return False
    _installed = True
    _record({'event': 'guard_installed', 'allow': sorted(_allowlist())})

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address, _real=real_connect):
        key, ok, why = _decide(address)
        _record({
            'event': 'connect',
            'target': key,
            'result': 'allowed' if ok else 'blocked',
            'reason': why,
        })
        if not ok:
            raise BlockedExternalConnection(
                f'bootstrap probe blocked external connect to {key}'
            )
        return _real(self, address)

    def connect_ex(self, address, _real=real_connect_ex):
        key, ok, why = _decide(address)
        _record({
            'event': 'connect_ex',
            'target': key,
            'result': 'allowed' if ok else 'blocked',
            'reason': why,
        })
        if not ok:
            return 111  # ECONNREFUSED: recorded, never a silent allow
        return _real(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    return True
