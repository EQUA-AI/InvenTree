#!/usr/bin/env python3
"""Verify a Cosmos container against the definition this repository expects.

This tool is deliberately lopsided: verifying is the normal path and needs no
credentials at all, while creating is opt-in and refuses to touch anything but a
local emulator. A container's partition key cannot be changed after creation, so
a script that quietly "fixed" a live container would be destroying data, not
converging on a schema.

Three ways to check, in order of how little access they need:

1. Offline - save the output of ``az cosmosdb sql container show`` and compare
   it here. No credentials, no network, runs in CI.
2. Live - read the container's properties directly. Needs a data-plane role;
   owning the account in the portal is *not* enough, because Cosmos separates
   the control plane from the data plane.
3. Emulator - create the container locally to develop against.

Exit status is 0 when the container matches and 1 when it drifts, so this can
gate a deployment. It never grants a role, never writes a document and never
prints a credential.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCHEMA = Path(__file__).parent / 'schema' / 'pumphouse_readings.container.json'

#: The emulator's key is published in Microsoft's documentation, so it is a
#: well-known constant rather than a secret. It is still read from the
#: environment so that no key-shaped string lives in this repository.
#:
#: The endpoint is **http**, not https. The vNext emulator used by the `cosmos`
#: compose profile serves plain HTTP, which is why no certificate has to be
#: installed anywhere; the classic emulator's self-signed TLS is the thing being
#: avoided here, not an option being skipped.
EMULATOR_ENDPOINT = 'http://localhost:8081'
EMULATOR_KEY_ENV = 'COSMOS_EMULATOR_KEY'


def load_expected(path: Path) -> dict:
    """Read the container definition this repository treats as authoritative."""
    return json.loads(path.read_text(encoding='utf-8'))


def normalise(live: dict) -> dict:
    """Reduce a live container description to the fields we make claims about.

    Accepts the three shapes the same container arrives in: the ``--query``
    projection suggested in the README, a full ``az ... show`` document, and the
    properties returned by ``ContainerProxy.read()``. They differ only in
    nesting and key names; handling them separately would be three chances to
    get the same comparison subtly wrong.
    """
    if 'resource' in live:
        live = live['resource']

    partition_key = live.get('pk') or live.get('partitionKey') or {}
    indexing = live.get('indexing') or live.get('indexingPolicy') or {}

    # A container with TTL switched off omits defaultTtl entirely. That is a
    # different state from -1 ("enabled, no default expiry"), and conflating the
    # two would let a per-document ttl be silently ignored.
    ttl = live.get('ttl', live.get('defaultTtl'))

    return {
        'paths': list(partition_key.get('paths') or []),
        'kind': partition_key.get('kind'),
        'version': partition_key.get('version'),
        'ttl': ttl,
        'indexing_mode': indexing.get('indexingMode'),
        'included': {entry['path'] for entry in indexing.get('includedPaths') or []},
        'excluded': {entry['path'] for entry in indexing.get('excludedPaths') or []},
    }


def compare(expected: dict, live: dict) -> list[str]:
    """Return one human-readable line per difference; empty means it matches."""
    want = normalise({
        'partitionKey': expected['partitionKey'],
        'defaultTtl': expected['defaultTtl'],
        'indexingPolicy': expected['indexingPolicy'],
    })
    got = normalise(live)
    drift = []

    if want['paths'] != got['paths']:
        drift.append(
            f'partition key paths are {got["paths"] or "unset"}, '
            f'expected {want["paths"]}; this is immutable, so the container '
            'has to be recreated to change it'
        )
    if want['kind'] != got['kind'] or want['version'] != got['version']:
        drift.append(
            f'partition key is {got["kind"]} v{got["version"]}, expected '
            f'{want["kind"]} v{want["version"]}; hierarchical keys need '
            'MultiHash and version 2'
        )
    if want['ttl'] != got['ttl']:
        state = 'switched off' if got['ttl'] is None else repr(got['ttl'])
        drift.append(
            f'defaultTtl is {state}, expected {want["ttl"]} so that TTL is '
            'enabled with no default and a per-document ttl is honoured'
        )
    if want['indexing_mode'] != got['indexing_mode']:
        drift.append(
            f'indexing mode is {got["indexing_mode"]}, expected {want["indexing_mode"]}'
        )

    drift.extend(
        f'indexed path missing: {path}'
        for path in sorted(want['included'] - got['included'])
    )
    if '/*' in want['excluded'] and '/*' not in got['excluded']:
        drift.append(
            'payload is being indexed: excludedPaths has no /* entry, so write '
            'cost scales with tag count for queries we never run'
        )

    return drift


def read_live(args) -> dict:
    """Read container properties from a live account or the local emulator."""
    try:
        from azure.cosmos import CosmosClient
    except ImportError:  # pragma: no cover - declared in requirements.in
        sys.exit('azure-cosmos is not installed; pip install azure-cosmos')

    if args.emulator:
        key = os.environ.get(EMULATOR_KEY_ENV)
        if not key:
            sys.exit(f'Set {EMULATOR_KEY_ENV} to the emulator key.')
        client = CosmosClient(args.endpoint or EMULATOR_ENDPOINT, credential=key)
    else:
        from azure.identity import DefaultAzureCredential

        if not args.endpoint:
            sys.exit('Pass --endpoint, or set INVENTREE_COSMOS_ENDPOINT.')
        client = CosmosClient(args.endpoint, credential=DefaultAzureCredential())

    database = client.get_database_client(args.database)
    return database.get_container_client(args.container).read()


def create(args, expected: dict) -> None:
    """Create the container locally. Refuses to run against a real account."""
    if not args.emulator:
        sys.exit(
            'Refusing to create against a live account. Create the container '
            'from contrib/cosmos/README.md and verify it here: a partition key '
            'cannot be changed afterwards, so that choice belongs to a person.'
        )

    from azure.cosmos import CosmosClient, PartitionKey

    key = os.environ.get(EMULATOR_KEY_ENV)
    if not key:
        sys.exit(f'Set {EMULATOR_KEY_ENV} to the emulator key.')

    client = CosmosClient(args.endpoint or EMULATOR_ENDPOINT, credential=key)
    database = client.create_database_if_not_exists(args.database)
    database.create_container_if_not_exists(
        id=args.container,
        partition_key=PartitionKey(
            path=expected['partitionKey']['paths'],
            kind=expected['partitionKey']['kind'],
            version=expected['partitionKey']['version'],
        ),
        default_ttl=expected['defaultTtl'],
        indexing_policy=expected['indexingPolicy'],
    )
    print(f'Emulator container {args.database}/{args.container} is ready.')


def build_parser() -> argparse.ArgumentParser:
    """Command-line surface: verify by default, create only on request."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--schema', type=Path, default=SCHEMA)
    parser.add_argument(
        '--from-json',
        type=Path,
        help='Compare against saved `az cosmosdb sql container show` output',
    )
    parser.add_argument('--live', action='store_true', help='Read the container')
    parser.add_argument('--create', action='store_true', help='Emulator only')
    parser.add_argument('--emulator', action='store_true')
    parser.add_argument(
        '--endpoint', default=os.environ.get('INVENTREE_COSMOS_ENDPOINT')
    )
    parser.add_argument(
        '--database', default=os.environ.get('INVENTREE_COSMOS_DATABASE', 'aimms')
    )
    parser.add_argument(
        '--container',
        default=os.environ.get('INVENTREE_COSMOS_CONTAINER', 'pumphouse_readings'),
    )
    return parser


def main(argv=None) -> int:
    """Verify, or optionally create on the emulator, and report any drift."""
    parser = build_parser()
    args = parser.parse_args(argv)
    expected = load_expected(args.schema)

    if args.create:
        create(args, expected)
        return 0

    if args.from_json:
        live = json.loads(args.from_json.read_text(encoding='utf-8'))
    elif args.live or args.emulator:
        try:
            live = read_live(args)
        except Exception as exc:
            sys.exit(
                f'Could not read the container ({type(exc).__name__}). A '
                'data-plane role is required; owning the account in the portal '
                'does not grant one. See contrib/cosmos/README.md, or use '
                '--from-json with the az CLI output instead.'
            )
    else:
        parser.error('Choose --from-json, --live, --emulator or --create.')

    drift = compare(expected, live)
    if not drift:
        print(f'{args.database}/{args.container} matches the expected definition.')
        return 0

    print(f'{args.database}/{args.container} drifts from the definition:')
    for line in drift:
        print(f'  - {line}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
