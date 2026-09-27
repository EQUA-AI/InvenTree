"""Report what is actually stored in a Cosmos container, right now.

Written because "is there any data in Cosmos?" is a question the repository
cannot answer from its own files. The fixtures under contrib/ say what *would*
be written; only the container says what is there.

A document count on its own is misleading here, in both directions. The dev
reseed loop writes one document per cycle, so a large total can be a single
logical sample replayed thousands of times; and the trend chart can only read
the last six hours, so a corpus can be simultaneously large and empty as far
as the UI is concerned. This therefore reports the count, the station split,
the age distribution against the windows the UI actually offers, and the
observed cadence in the readable tail.

The cadence line is the one worth reading. A median gap far larger than the
source's real sampling interval means the container is being *fed* by
something slower than the plant writes - which is the situation on any
developer machine, and is not a fault in the connector.

Read-only: it issues SELECTs and nothing else. Defaults target the local
emulator. Pointing it at a live account needs a data-plane read role, and is
deliberately not the default.

Run from the repository root.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone

EMULATOR_ENDPOINT = 'http://localhost:8081'
EMULATOR_KEY_ENV = 'COSMOS_EMULATOR_KEY'

#: An access token minted outside this process, and when it expires.
#:
#: Needed because the SDK and the credentials live in different places: the dev
#: container has azure-cosmos but no Azure CLI, so DefaultAzureCredential has
#: no cache to read there. Minting on the host and passing the token in is the
#: same workaround data/probe_cosmos.py uses.
TOKEN_ENV = 'COSMOS_ACCESS_TOKEN'
TOKEN_EXPIRES_ENV = 'COSMOS_ACCESS_TOKEN_EXPIRES'

#: Windows the trend UI can actually request, plus two longer ones for context.
#: The six-hour entry is the server's ceiling, so anything older than that is
#: unreachable from a chart no matter how many documents it contains.
WINDOWS = (
    ('last 10 minutes', 10 * 60_000),
    ('last 1 hour', 3_600_000),
    ('last 6 hours', 6 * 3_600_000),
    ('last 24 hours', 24 * 3_600_000),
    ('last 30 days', 30 * 24 * 3_600_000),
)


def iso(epoch_ms: float) -> str:
    """Render an epoch-millisecond sample time as UTC."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def build_client(args):
    """Return a Cosmos client for the emulator or a live account."""
    from azure.cosmos import CosmosClient

    if args.emulator:
        key = os.environ.get(EMULATOR_KEY_ENV)
        if not key:
            sys.exit(f'Set {EMULATOR_KEY_ENV} for emulator access.')
        return CosmosClient(args.endpoint or EMULATOR_ENDPOINT, credential=key)

    if not args.endpoint:
        sys.exit('Pass --endpoint, or set INVENTREE_COSMOS_ENDPOINT.')

    # A token minted elsewhere, for the case this runs somewhere the CLI's
    # cache is not. The dev container has the SDK but no Azure CLI, so
    # DefaultAzureCredential finds nothing there and fails with an error that
    # reads like a permissions problem rather than a missing-cache one.
    token = os.environ.get(TOKEN_ENV)
    if token:
        from azure.core.credentials import AccessToken, TokenCredential

        expires = int(os.environ.get(TOKEN_EXPIRES_ENV, '0'))

        class StaticToken(TokenCredential):
            """Hand the SDK a token the caller already obtained."""

            def get_token(self, *scopes, **kwargs):
                return AccessToken(token, expires)

        return CosmosClient(args.endpoint, credential=StaticToken())

    from azure.identity import DefaultAzureCredential

    return CosmosClient(args.endpoint, credential=DefaultAzureCredential())


def report(times: list[int], stations: Counter, buckets: int) -> None:
    """Print the inventory for the sample times collected."""
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    print(f'documents:            {len(times)}')
    print(f'stations:             {len(stations)}')
    for uuid, count in stations.most_common():
        print(f'    {uuid}  {count}')
    print(f'distinct hour buckets: {buckets}')

    if not times:
        print('container is empty')
        return

    print(f'earliest sample:      {iso(times[0])}')
    print(f'latest sample:        {iso(times[-1])}')
    print(f'now:                  {iso(now_ms)}')

    print('\nby age:')
    for label, span_ms in WINDOWS:
        count = sum(1 for t in times if t >= now_ms - span_ms)
        print(f'    {label:>16}: {count}')

    days = Counter(iso(t)[:10] for t in times)
    print(f'\nby day ({len(days)} distinct):')
    for day, count in sorted(days.items()):
        print(f'    {day}: {count}')

    # Cadence within the readable window. A large max gap next to a small
    # median is the signature of a feeder that stopped and restarted, rather
    # than of a source that samples irregularly.
    recent = [t for t in times if t >= now_ms - 6 * 3_600_000]
    if len(recent) > 1:
        gaps = sorted(recent[i + 1] - recent[i] for i in range(len(recent) - 1))
        median = gaps[len(gaps) // 2]
        print(
            f'\ncadence in last 6h: min={gaps[0]}ms median={median}ms max={gaps[-1]}ms'
        )
        print(f'    median implies ~{median / 1000:.0f}s between samples')


def main() -> None:
    """Query the container and print an inventory."""
    parser = argparse.ArgumentParser(description='Inventory a Cosmos container.')
    parser.add_argument('--emulator', action='store_true', help='Use the emulator')
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
    args = parser.parse_args()

    client = build_client(args)
    container = client.get_database_client(args.database).get_container_client(
        args.container
    )

    docs = list(
        container.query_items(
            'SELECT c.station_uuid, c.hour_bucket, c.sub_time_period FROM c',
            enable_cross_partition_query=True,
        )
    )

    times = sorted(
        int(d['sub_time_period']) for d in docs if d.get('sub_time_period') is not None
    )
    stations = Counter(d.get('station_uuid') for d in docs)
    buckets = len({d.get('hour_bucket') for d in docs})

    report(times, stations, buckets)


if __name__ == '__main__':
    main()
