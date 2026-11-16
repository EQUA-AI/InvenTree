"""Opt-in, single-object Python OSDK transport; no population or snapshot claim.

Only trusted server configuration selects the installed generated SDK, field
mapping and credential resolver. A short-lived process bounds elapsed time and
returned bytes, including SDK authentication/retries. This is not a network
byte or worker-memory limit; qualify those separately in the staging runtime.
"""

import hashlib
import importlib.metadata
import json
import keyword
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit

MAX_OUTPUT_BYTES = 16384
ONTOLOGY_READ_SCOPE = 'api:use-ontologies-read'
FIELDS = ('pk', 'client', 'name')
PROPERTY_FIELDS = (
    'pk',
    'client',
    'client_incarnation',
    'incarnation',
    'environment',
    'name',
)
_WORKER_SLOTS = BoundedSemaphore(4)


class TransportError(ValueError):
    """Fixed, public errors never embed SDK exceptions, identifiers or secrets."""

    def __init__(self, code='unavailable'):
        """Constrain the public vocabulary even for malformed worker replies."""
        self.code = (
            code
            if code in {'unavailable', 'denied', 'timeout', 'invalid', 'budget'}
            else 'unavailable'
        )
        super().__init__(f'Palantir read refused: {self.code}')


@dataclass(frozen=True, repr=False)
class Credentials:
    """Resolved immediately before dispatch, never placed in argv or environment."""

    client_id: str
    client_secret: str


@dataclass(frozen=True, repr=False)
class ObjectMapping:
    """Reviewed generated Python names, not arbitrary request property paths.

    The remote primary key is the decimal native pk (string or integer).
    Incarnation is the native machine UUID, or location created_at ISO string.
    Environment is an explicit exporter namespace, independent of client scope.
    """

    domain: str
    object_type: str
    pk: str
    client: str
    client_incarnation: str
    incarnation: str
    environment: str
    name: str
    key_type: str = 'str'


@dataclass(frozen=True, repr=False)
class TransportConfig:
    """No guessed SDK package, tenant, credentials or field policy defaults."""

    origin: str
    environment: str
    distribution: str
    sdk_module: str
    sdk_version: str
    mappings: tuple[ObjectMapping, ...]
    secret_reference: str
    resolve_credentials: Callable
    authorize_fields: Callable
    request_timeout: int = 5
    deadline: int = 20


def _text(value, limit=256):
    return (
        type(value) is str
        and 0 < len(value) <= limit
        and value.strip() == value
        and not any(ord(c) < 32 for c in value)
    )


def validate_config(config):
    """Reject ambiguous origins, imports, mappings and nonliteral limits early."""
    if type(config) is not TransportConfig:
        raise TransportError('invalid')
    if not _text(config.origin):
        raise TransportError('invalid')
    try:
        url = urlsplit(config.origin)
        valid_origin = (
            url.scheme == 'https'
            and url.hostname
            and url.netloc == url.hostname
            and not any((url.path, url.query, url.fragment, url.username, url.password))
            and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', url.hostname)
            and '.' in url.hostname
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise TransportError('invalid')
    if (
        not _text(config.environment, 128)
        or not _text(config.distribution, 128)
        or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', config.distribution)
        or not _text(config.sdk_module, 64)
        or not re.fullmatch(r'[a-z][a-z0-9_]*', config.sdk_module)
        or not _text(config.sdk_version, 80)
        or not re.fullmatch(r'[0-9][a-zA-Z0-9.!+_-]*', config.sdk_version)
        or not _text(config.secret_reference, 512)
        or not callable(config.resolve_credentials)
        or not callable(config.authorize_fields)
        or type(config.request_timeout) is not int
        or type(config.deadline) is not int
        or not 1 <= config.request_timeout < config.deadline <= 60
        or type(config.mappings) is not tuple
        or not 1 <= len(config.mappings) <= 2
    ):
        raise TransportError('invalid')
    domains = set()
    objects = set()
    for mapping in config.mappings:
        if type(mapping) is not ObjectMapping:
            raise TransportError('invalid')
        attrs = [getattr(mapping, name) for name in PROPERTY_FIELDS]
        if (
            type(mapping.domain) is not str
            or mapping.domain not in ('machines', 'locations')
            or mapping.domain in domains
            or not _text(mapping.object_type, 128)
            or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*', mapping.object_type)
            or keyword.iskeyword(mapping.object_type)
            or mapping.object_type in objects
            or type(mapping.key_type) is not str
            or mapping.key_type not in ('str', 'int')
            or any(
                not _text(a, 128) or not re.fullmatch(r'[a-z][a-z0-9_]*', a)
                for a in attrs
            )
            or len(set(attrs)) != len(attrs)
        ):
            raise TransportError('invalid')
        domains.add(mapping.domain)
        objects.add(mapping.object_type)
    return config


def sdk_distribution(config):
    """Inspect the exact installed version/package layout without importing it.

    Deployment must also lock the tenant-generated wheel and transitive runtime
    hashes. An exact installed version alone is not a wheel-integrity proof.
    """
    validate_config(config)
    try:
        dist = importlib.metadata.distribution(config.distribution)
        expected = f'{config.sdk_module}/__init__.py'
        if dist.version != config.sdk_version or expected not in {
            str(p) for p in dist.files or ()
        }:
            raise TransportError('invalid')
        return dist
    except importlib.metadata.PackageNotFoundError:
        raise TransportError('unavailable') from None


def _wire_config(config, mapping):
    return {
        'origin': config.origin,
        'distribution': config.distribution,
        'sdk_module': config.sdk_module,
        'sdk_version': config.sdk_version,
        'request_timeout': config.request_timeout,
        'mapping': asdict(mapping),
    }


def _stop(worker):
    """Kill the owned SDK process group, including children, and reap it."""
    if worker.poll() is None:
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # Never drain an unbounded provider-controlled stream during cleanup.
    try:
        worker.wait(timeout=1)
    finally:
        for stream in (worker.stdin, worker.stdout):
            if stream is not None:
                stream.close()


def _invoke_worker(payload, deadline):
    """Use a clean isolated interpreter and secret-bearing anonymous stdin pipe.

    Worker output is capped before parsing. SDK stdout/stderr are discarded in
    the child. The approved generated package is trusted executable code, not
    sandboxed code. No local databases or inherited application secrets are
    needed in the child environment.
    """
    import selectors
    import time

    command = [
        sys.executable,
        '-I',
        '-B',
        str(Path(__file__).with_name('palantir_worker.py')),
    ]
    # Keep CA lookup/PATH/locale; do not inherit token, proxy or auto-auth vars.
    environment = {
        k: v
        for k, v in os.environ.items()
        if k in {'PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    }
    data = json.dumps(payload, allow_nan=False).encode()
    if len(data) > 8192:
        raise TransportError('budget')
    worker = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    started = time.monotonic()
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(worker.stdin.fileno(), False)
            selector.register(worker.stdin, selectors.EVENT_WRITE)
            selector.register(worker.stdout, selectors.EVENT_READ)
            sent = 0
            finished = False
            while not finished:
                remaining = deadline - (time.monotonic() - started)
                events = selector.select(max(0, remaining))
                if remaining <= 0 or not events:
                    raise TransportError('timeout')
                for key, _ in events:
                    if key.fileobj is worker.stdin:
                        sent += os.write(
                            worker.stdin.fileno(), data[sent : sent + 4096]
                        )
                        if sent == len(data):
                            selector.unregister(worker.stdin)
                            worker.stdin.close()
                            worker.stdin = None
                    else:
                        chunk = os.read(worker.stdout.fileno(), 4096)
                        if not chunk:
                            finished = True
                            break
                        output.extend(chunk)
                        if len(output) > MAX_OUTPUT_BYTES:
                            raise TransportError('budget')
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            raise TransportError('timeout')
        worker.wait(timeout=remaining)
        if worker.returncode != 0:
            raise TransportError()
        reply = json.loads(output)
        if type(reply) is not dict or set(reply) not in ({'error'}, {'row'}):
            raise TransportError('invalid')
        if 'error' in reply:
            raise TransportError(reply['error'])
        return reply['row']
    except subprocess.TimeoutExpired:
        raise TransportError('timeout') from None
    except TransportError:
        raise
    except Exception:
        raise TransportError() from None
    finally:
        _stop(worker)


def fetch_object(config, mapping, *, pk, client_pk, client_incarnation, incarnation):
    """Fetch only an already authorized exact identity; no source fallback."""
    validate_config(config)
    if (
        mapping not in config.mappings
        or any(
            type(k) is not int or not 1 <= k <= (1 << 63) - 1 for k in (pk, client_pk)
        )
        or not _text(incarnation, 128)
        or not _text(client_incarnation, 128)
    ):
        raise TransportError('invalid')
    if not _WORKER_SLOTS.acquire(blocking=False):
        raise TransportError('budget')
    try:
        credentials = config.resolve_credentials(config.secret_reference)
        if (
            type(credentials) is not Credentials
            or not _text(credentials.client_id, 256)
            or not _text(credentials.client_secret, 2048)
        ):
            raise TransportError('invalid')
        payload = _wire_config(config, mapping)
        payload.update({'credentials': asdict(credentials), 'pk': pk})
        row = _invoke_worker(payload, config.deadline)
        if (
            type(row) is not dict
            or set(row) != set(PROPERTY_FIELDS)
            or type(row['pk']) is not int
            or row['pk'] != pk
            or type(row['client']) is not int
            or row['client'] != client_pk
            or type(row['client_incarnation']) is not str
            or row['client_incarnation'] != client_incarnation
            or type(row['incarnation']) is not str
            or row['incarnation'] != incarnation
            or type(row['environment']) is not str
            or row['environment'] != config.environment
            or not _text(row['name'], 255)
        ):
            raise TransportError('invalid')
        return {field: row[field] for field in FIELDS}
    except TransportError:
        raise
    except Exception:
        raise TransportError() from None
    finally:
        _WORKER_SLOTS.release()


def config_fingerprint(config):
    """Bind policy/secret-resolver identity too, without hashing credential bytes."""
    validate_config(config)
    value = [_wire_config(config, m) for m in config.mappings]
    value += [
        config.environment,
        config.secret_reference,
        config.deadline,
        id(config.resolve_credentials),
        id(config.authorize_fields),
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
