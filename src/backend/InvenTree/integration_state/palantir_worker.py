"""One generated Python OSDK object read in an expendable process.

Public SDK calls follow Palantir's Python 2.x and migration documentation.
Unsupported/generated API variants fail closed instead of guessing signatures.
"""

import contextlib
import importlib
import json
import os
import sys
from pathlib import Path

if __name__ == '__main__':
    # -I removes checkout/PYTHONPATH imports; bootstrap this known source only.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration_state.palantir_transport import (
    MAX_OUTPUT_BYTES,
    ONTOLOGY_READ_SCOPE,
    PROPERTY_FIELDS,
    ObjectMapping,
    TransportConfig,
    TransportError,
    sdk_distribution,
)


def _sdk_read(payload):
    mapping = ObjectMapping(**payload['mapping'])
    # Structural checks are repeated in the worker, before SDK import or auth.
    config = TransportConfig(
        origin=payload['origin'],
        environment='worker-validation',
        distribution=payload['distribution'],
        sdk_module=payload['sdk_module'],
        sdk_version=payload['sdk_version'],
        mappings=(mapping,),
        secret_reference='pipe',
        resolve_credentials=lambda _: None,
        authorize_fields=lambda *_: False,
        request_timeout=payload['request_timeout'],
        deadline=60,
    )
    dist = sdk_distribution(config)
    sdk = importlib.import_module(config.sdk_module)
    expected = Path(dist.locate_file(f'{config.sdk_module}/__init__.py')).resolve()
    if Path(sdk.__file__).resolve() != expected:
        raise TransportError('invalid')
    types = importlib.import_module(f'{config.sdk_module}.ontology.objects')
    object_class = getattr(types, mapping.object_type)
    properties = [
        getattr(object_class.object_type, getattr(mapping, p)) for p in PROPERTY_FIELDS
    ]
    http = sdk.Config(
        timeout=config.request_timeout, verify=True, scheme='https', proxies={}
    )
    auth = sdk.ConfidentialClientAuth(
        client_id=payload['credentials']['client_id'],
        client_secret=payload['credentials']['client_secret'],
        hostname=config.origin.removeprefix('https://'),
        scopes=[ONTOLOGY_READ_SCOPE],
        should_refresh=False,
        config=http,
    )
    client = sdk.FoundryClient(
        auth=auth, hostname=config.origin.removeprefix('https://'), config=http
    )
    collection = getattr(client.ontology.objects, mapping.object_type)
    pk = payload['pk'] if mapping.key_type == 'int' else str(payload['pk'])
    obj = collection.get(pk, properties=properties)
    # Never serialize the SDK object's full dictionary, links or lazy fields.
    return {p: getattr(obj, getattr(mapping, p)) for p in PROPERTY_FIELDS}


def main():
    """Emit only a bounded detached primitive projection or fixed error code."""
    reply = {'error': 'unavailable'}
    # Preserve the protocol fd and silence even native SDK logging to fd 1/2.
    protocol = os.dup(sys.stdout.fileno())
    with open(os.devnull, 'w', encoding='utf-8') as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        try:
            raw = sys.stdin.buffer.read(8193)
            if len(raw) > 8192:
                raise TransportError('budget')
            payload = json.loads(raw)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                row = _sdk_read(payload)
            if any(type(v) not in (str, int) for v in row.values()):
                raise TransportError('invalid')
            reply = {'row': row}
            encoded = json.dumps(reply, allow_nan=False).encode()
            if len(encoded) > MAX_OUTPUT_BYTES:
                raise TransportError('budget')
        except TransportError as error:
            reply = {'error': error.code}
        except TimeoutError:
            reply = {'error': 'timeout'}
        except Exception:
            # SDK exception details can contain URLs, source values or tokens.
            # Do not classify by untrusted strings or retry on an auth failure.
            reply = {'error': 'unavailable'}
        with os.fdopen(protocol, 'wb') as output:
            output.write(json.dumps(reply, allow_nan=False).encode())


if __name__ == '__main__':
    main()
